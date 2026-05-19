from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swallow.core.job_store import JobStore
from swallow.core.models import JobMetadata
from swallow.sdk.errors import IngestSdkConfigError, IngestSdkJobNotFound, IngestSdkProtocolError
from swallow.sdk.models import (
    IngestDocumentRef,
    IngestError,
    IngestJob,
    IngestOutputs,
    IngestResult,
    IngestStatus,
    IngestTextPayload,
    IngestWarning,
    ReturnContentMode,
)


DEFAULT_PREVIEW_BYTES = 4 * 1024
DEFAULT_FULL_CONTENT_BYTES = 1024 * 1024
TERMINAL_STATUSES = {"success", "partial", "failed"}


def map_job(store_root: Path | str, job_id: str) -> IngestJob:
    root = Path(store_root)
    metadata = read_metadata(root, job_id)
    manifest = read_optional_json_object(root / (metadata.manifest_path or f"jobs/{job_id}/manifest.json"))
    status = map_status(metadata, manifest)
    outputs = map_outputs(metadata, manifest)
    return IngestJob(
        job_id=metadata.job_id,
        status=status,
        source_type=metadata.source_type,
        created_at=metadata.created_at,
        trace_path=outputs.trace_path or metadata.trace_path,
        document_path=outputs.markdown_path,
        ingest_document_path=outputs.document_json_path,
        manifest_path=outputs.manifest_path,
    )


def map_result(
    store_root: Path | str,
    job_id: str,
    *,
    content: ReturnContentMode = "preview",
    preview_bytes: int = DEFAULT_PREVIEW_BYTES,
    full_content_bytes: int = DEFAULT_FULL_CONTENT_BYTES,
) -> IngestResult:
    validate_content_mode(content)
    root = Path(store_root)
    metadata = read_metadata(root, job_id)
    manifest = read_optional_json_object(root / (metadata.manifest_path or f"jobs/{job_id}/manifest.json"))
    status = map_status(metadata, manifest)
    outputs = map_outputs(metadata, manifest)
    document = map_document_ref(root, metadata, outputs)
    preview = None
    full_content = None

    markdown_path = root / outputs.markdown_path if outputs.markdown_path else None
    if markdown_path is not None and markdown_path.exists():
        if content == "preview":
            preview = read_text_payload(markdown_path, limit_bytes=preview_bytes)
        elif content == "full":
            full_content = read_text_payload(markdown_path, limit_bytes=full_content_bytes)

    return IngestResult(
        job_id=metadata.job_id,
        status=status,
        outputs=outputs,
        document=document,
        preview=preview,
        content=full_content,
        warnings=map_warnings(manifest),
        errors=map_errors(metadata, manifest),
    )


def read_metadata(root: Path, job_id: str) -> JobMetadata:
    metadata = JobStore(root).read_job_metadata(job_id)
    if metadata is None:
        raise IngestSdkJobNotFound(f"Job not found: {job_id}")
    return metadata


def map_status(metadata: JobMetadata, manifest: dict[str, Any]) -> IngestStatus:
    if metadata.status == "success":
        status = manifest.get("status")
        if status in TERMINAL_STATUSES:
            return status
    return metadata.status


def map_outputs(metadata: JobMetadata, manifest: dict[str, Any]) -> IngestOutputs:
    manifest_outputs = manifest.get("outputs")
    if not isinstance(manifest_outputs, dict):
        manifest_outputs = {}
    return IngestOutputs(
        markdown_path=metadata.document_path or read_str(manifest_outputs.get("markdown")),
        document_json_path=metadata.ingest_document_path or read_str(manifest_outputs.get("json")),
        trace_path=metadata.trace_path or read_str(manifest_outputs.get("trace")),
        manifest_path=metadata.manifest_path or read_str(manifest_outputs.get("manifest")),
        chunks_path=read_str(manifest_outputs.get("chunks")),
    )


def map_document_ref(root: Path, metadata: JobMetadata, outputs: IngestOutputs) -> IngestDocumentRef:
    if outputs.document_json_path:
        document_path = root / outputs.document_json_path
        if document_path.exists():
            payload = read_json_object(document_path)
            source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
            content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
            return IngestDocumentRef(
                id=read_str(payload.get("id")),
                title=read_str(content.get("title")),
                source_type=read_str(source.get("source_type")),
                mime_type=read_str(source.get("mime_type")),
                sha256=read_str(source.get("sha256")),
                raw_id=read_str(payload.get("raw_id")),
                original_filename=read_str(source.get("original_filename")),
            )

    return IngestDocumentRef(
        id=metadata.ingest_document_id,
        source_type=metadata.source_type,
        mime_type=metadata.mime_type,
        sha256=metadata.sha256,
        raw_id=metadata.raw_id,
        original_filename=metadata.original_filename,
    )


def map_warnings(manifest: dict[str, Any]) -> list[IngestWarning]:
    warnings = manifest.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [map_warning(item) for item in warnings]


def map_warning(item: Any) -> IngestWarning:
    if isinstance(item, dict):
        return IngestWarning(
            code=read_str(item.get("code")),
            message=read_str(item.get("message")) or json.dumps(item, ensure_ascii=False, default=str),
            stage=read_str(item.get("stage")),
            worker=read_str(item.get("worker")),
            details=standard_details(item),
        )
    return IngestWarning(message=str(item))


def map_errors(metadata: JobMetadata, manifest: dict[str, Any]) -> list[IngestError]:
    errors = manifest.get("errors")
    if not isinstance(errors, list) or not errors:
        errors = [metadata.error] if metadata.error else []
    return [map_error(item) for item in errors if item is not None]


def map_error(item: Any) -> IngestError:
    if isinstance(item, dict):
        fallback_allowed = bool(item.get("fallback_allowed"))
        retryable = bool(item.get("retryable"))
        recoverable = bool(item.get("recoverable", fallback_allowed))
        return IngestError(
            code=read_str(item.get("code")) or "INGEST_ERROR",
            message=read_str(item.get("message")) or json.dumps(item, ensure_ascii=False, default=str),
            stage=read_str(item.get("stage")),
            worker=read_str(item.get("worker")),
            retryable=retryable,
            recoverable=recoverable,
            details=standard_details(item),
        )
    return IngestError(code="INGEST_ERROR", message=str(item))


def read_text_payload(path: Path, *, limit_bytes: int) -> IngestTextPayload:
    with path.open("rb") as file:
        data = file.read(limit_bytes + 1)
    truncated = len(data) > limit_bytes
    if truncated:
        data = data[:limit_bytes]
    return IngestTextPayload(
        text=data.decode("utf-8", errors="replace"),
        truncated=truncated,
        bytes_read=len(data),
        limit_bytes=limit_bytes,
    )


def read_optional_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json_object(path)


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise IngestSdkProtocolError(f"Invalid JSON at {path}: {error}") from error
    if not isinstance(payload, dict):
        raise IngestSdkProtocolError(f"Expected JSON object at {path}")
    return payload


def validate_content_mode(content: str) -> None:
    if content not in {"none", "preview", "full"}:
        raise IngestSdkConfigError(f"Unsupported content mode: {content}")


def read_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def standard_details(item: dict[str, Any]) -> dict[str, Any]:
    standard = {"code", "message", "stage", "worker", "retryable", "recoverable", "fallback_allowed"}
    return {str(key): value for key, value in item.items() if key not in standard}
