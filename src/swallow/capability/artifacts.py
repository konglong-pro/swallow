from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from swallow.capability.errors import CapabilityArtifactError, CapabilityPolicyError
from swallow.capability.schemas import ArtifactDescriptor, ArtifactView, ArtifactViewOptions, ProviderPolicy


TEXT_MEDIA_TYPES = {
    "application/json",
    "application/jsonl",
    "application/x-jsonlines",
    "text/markdown",
    "text/plain",
}


@dataclass(frozen=True)
class ResolvedArtifact:
    ref: str
    path: Path
    kind: str
    role: str
    trust_level: str
    media_type: str | None
    raw_original: bool = False


def job_artifact_ref(job_id: str, filename: str) -> str:
    return f"swallow://jobs/{job_id}/{filename}"


def raw_artifact_ref(raw_id: str, filename: str) -> str:
    return f"swallow://raw/{raw_id}/{filename}"


def batch_artifact_ref(batch_id: str, filename: str) -> str:
    return f"swallow://batch_runs/{batch_id}/{filename}"


def descriptor_for_job_artifact(job_id: str, filename: str) -> ArtifactDescriptor:
    kind, role, trust_level, media_type = classify_job_artifact(filename)
    return ArtifactDescriptor(
        artifact_ref=job_artifact_ref(job_id, filename),
        kind=kind,
        role=role,  # type: ignore[arg-type]
        trust_level=trust_level,  # type: ignore[arg-type]
        media_type=media_type,
    )


def descriptor_for_batch_artifact(batch_id: str, filename: str) -> ArtifactDescriptor:
    kind, role, trust_level, media_type = classify_batch_artifact(filename)
    return ArtifactDescriptor(
        artifact_ref=batch_artifact_ref(batch_id, filename),
        kind=kind,
        role=role,  # type: ignore[arg-type]
        trust_level=trust_level,  # type: ignore[arg-type]
        media_type=media_type,
    )


def descriptor_for_raw_artifact(raw_id: str, filename: str, media_type: str | None = None) -> ArtifactDescriptor:
    kind = "raw_original" if filename == "original" else "raw_metadata"
    role = "source" if filename == "original" else "evidence"
    trust_level = "source" if filename == "original" else "evidence"
    return ArtifactDescriptor(
        artifact_ref=raw_artifact_ref(raw_id, filename),
        kind=kind,
        role=role,  # type: ignore[arg-type]
        trust_level=trust_level,  # type: ignore[arg-type]
        media_type=media_type if filename == "original" else "application/json",
    )


def resolve_artifact_ref(store_root: Path, artifact_ref: str) -> ResolvedArtifact:
    parsed = urlparse(artifact_ref)
    if parsed.scheme != "swallow":
        raise CapabilityArtifactError(f"Unsupported artifact ref scheme: {artifact_ref}")
    if parsed.netloc == "jobs":
        return resolve_job_ref(store_root, artifact_ref, parsed.path.strip("/").split("/"))
    if parsed.netloc == "raw":
        return resolve_raw_ref(store_root, artifact_ref, parsed.path.strip("/").split("/"))
    if parsed.netloc == "batch_runs":
        return resolve_batch_ref(store_root, artifact_ref, parsed.path.strip("/").split("/"))
    raise CapabilityArtifactError(f"Unsupported artifact ref authority: {artifact_ref}")


def resolve_job_ref(store_root: Path, artifact_ref: str, parts: list[str]) -> ResolvedArtifact:
    if len(parts) != 2:
        raise CapabilityArtifactError(f"Job artifact refs must be swallow://jobs/<job_id>/<name>: {artifact_ref}")
    job_id, filename = parts
    allowed = {"document.md", "ingest_document.json", "manifest.json", "trace.jsonl"}
    if filename not in allowed:
        raise CapabilityArtifactError(f"Unsupported P1 job artifact: {filename}")
    kind, role, trust_level, media_type = classify_job_artifact(filename)
    return ResolvedArtifact(
        ref=artifact_ref,
        path=store_root / "jobs" / job_id / filename,
        kind=kind,
        role=role,
        trust_level=trust_level,
        media_type=media_type,
    )


def resolve_raw_ref(store_root: Path, artifact_ref: str, parts: list[str]) -> ResolvedArtifact:
    if len(parts) != 2:
        raise CapabilityArtifactError(f"Raw artifact refs must be swallow://raw/<raw_id>/<name>: {artifact_ref}")
    raw_id, filename = parts
    if filename not in {"original", "original.meta.json"}:
        raise CapabilityArtifactError(f"Unsupported P1 raw artifact: {filename}")
    raw_meta = find_raw_meta(store_root, raw_id)
    if filename == "original.meta.json":
        return ResolvedArtifact(
            ref=artifact_ref,
            path=raw_meta,
            kind="raw_metadata",
            role="evidence",
            trust_level="evidence",
            media_type="application/json",
        )
    payload = read_json_object(raw_meta)
    raw_path = store_root / str(payload.get("path", ""))
    media_type = payload.get("mime_type") if isinstance(payload.get("mime_type"), str) else guess_media_type(raw_path)
    return ResolvedArtifact(
        ref=artifact_ref,
        path=raw_path,
        kind="raw_original",
        role="source",
        trust_level="source",
        media_type=media_type,
        raw_original=True,
    )


def resolve_batch_ref(store_root: Path, artifact_ref: str, parts: list[str]) -> ResolvedArtifact:
    if len(parts) != 2:
        raise CapabilityArtifactError(f"Batch artifact refs must be swallow://batch_runs/<batch_id>/<name>: {artifact_ref}")
    batch_id, filename = parts
    allowed = {"summary.json", "trace.jsonl"}
    if filename not in allowed:
        raise CapabilityArtifactError(f"Unsupported P1 batch artifact: {filename}")
    kind, role, trust_level, media_type = classify_batch_artifact(filename)
    return ResolvedArtifact(
        ref=artifact_ref,
        path=store_root / "batch_runs" / batch_id / filename,
        kind=kind,
        role=role,
        trust_level=trust_level,
        media_type=media_type,
    )


def find_raw_meta(store_root: Path, raw_id: str) -> Path:
    raw_root = store_root / "raw_store"
    if not raw_root.exists():
        raise CapabilityArtifactError(f"Raw store not found for raw id: {raw_id}")
    for path in raw_root.glob("*/original.meta.json"):
        payload = read_json_object(path)
        if payload.get("raw_id") == raw_id:
            return path
    raise CapabilityArtifactError(f"Raw artifact not found: {raw_id}")


def build_artifact_view(
    resolved: ResolvedArtifact,
    *,
    options: ArtifactViewOptions,
    policy: ProviderPolicy,
) -> ArtifactView:
    if resolved.raw_original and not policy.allow_raw_artifact_read:
        raise CapabilityPolicyError("Reading raw original artifacts requires allow_raw_artifact_read")
    if options.content_mode == "full" and not policy.allow_full_content:
        raise CapabilityPolicyError("Full artifact content requires allow_full_content")
    if not resolved.path.exists() or not resolved.path.is_file():
        raise CapabilityArtifactError(f"Artifact file does not exist: {resolved.ref}")

    size_bytes = resolved.path.stat().st_size
    content_mode = "none"
    text = None
    data_base64 = None
    truncated = False
    if options.content_mode != "none":
        content_mode = options.content_mode
        data = read_capped(resolved.path, options.max_bytes)
        truncated = size_bytes > len(data)
        if is_text_media_type(resolved.media_type):
            text = data.decode("utf-8", errors="replace")
        elif options.include_binary:
            data_base64 = base64.b64encode(data).decode("ascii")
        else:
            content_mode = "none"

    return ArtifactView(
        artifact_ref=resolved.ref,
        kind=resolved.kind,
        role=resolved.role,  # type: ignore[arg-type]
        trust_level=resolved.trust_level,  # type: ignore[arg-type]
        media_type=resolved.media_type,
        size_bytes=size_bytes,
        content_mode=content_mode,  # type: ignore[arg-type]
        text=text,
        data_base64=data_base64,
        truncated=truncated,
        local_path=str(resolved.path.resolve()) if policy.expose_local_paths else None,
    )


def classify_job_artifact(filename: str) -> tuple[str, str, str, str | None]:
    if filename == "document.md":
        return "markdown_candidate", "primary", "candidate", "text/markdown"
    if filename == "ingest_document.json":
        return "ingest_document", "evidence", "evidence", "application/json"
    if filename == "manifest.json":
        return "manifest", "evidence", "evidence", "application/json"
    if filename == "trace.jsonl":
        return "trace", "evidence", "evidence", "application/jsonl"
    return "intermediate_artifact", "intermediate", "evidence", None


def classify_batch_artifact(filename: str) -> tuple[str, str, str, str | None]:
    if filename == "summary.json":
        return "batch_summary", "evidence", "evidence", "application/json"
    if filename == "trace.jsonl":
        return "trace", "evidence", "evidence", "application/jsonl"
    return "intermediate_artifact", "intermediate", "evidence", None


def read_capped(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as file:
        return file.read(max_bytes + 1)[:max_bytes]


def is_text_media_type(media_type: str | None) -> bool:
    if media_type is None:
        return False
    return media_type.startswith("text/") or media_type in TEXT_MEDIA_TYPES


def guess_media_type(path: Path) -> str | None:
    if path.name == "trace.jsonl":
        return "application/jsonl"
    if path.suffix == ".md":
        return "text/markdown"
    media_type, _encoding = mimetypes.guess_type(path.name)
    return media_type


def read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CapabilityArtifactError(f"Invalid JSON artifact: {path}") from error
    if not isinstance(payload, dict):
        raise CapabilityArtifactError(f"Expected JSON object artifact: {path}")
    return payload
