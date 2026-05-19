from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from swallow.core.errors import error_to_dict
from swallow.core.ids import new_job_id
from swallow.core.models import IngestDocument, JobMetadata, JobRecord, JobSummary, RawRecord
from swallow.core.time import now_iso


class JobStore:
    def __init__(self, store_root: Path | str = ".") -> None:
        self.store_root = Path(store_root)
        self.jobs_root = self.store_root / "jobs"

    def create_job(
        self,
        raw: RawRecord,
        *,
        source_type: str,
        source_url: str | None = None,
    ) -> JobRecord:
        job_id = new_job_id()
        job_dir = self.jobs_root / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        (job_dir / "intermediate").mkdir(exist_ok=True)
        (job_dir / "artifacts").mkdir(exist_ok=True)
        (job_dir / "logs").mkdir(exist_ok=True)

        job = JobRecord(
            id=job_id,
            raw_id=raw.raw_id,
            source_type=source_type,
            source_url=source_url,
            job_dir=job_dir.relative_to(self.store_root).as_posix(),
            trace_path=(job_dir / "trace.jsonl").relative_to(self.store_root).as_posix(),
            created_at=now_iso(),
        )
        self.write_job_metadata(
            JobMetadata(
                job_id=job.id,
                raw_id=raw.raw_id,
                sha256=raw.sha256,
                source_type=source_type,
                source_url=source_url,
                original_filename=raw.original_filename,
                mime_type=raw.mime_type,
                status="running",
                job_dir=job.job_dir,
                trace_path=job.trace_path,
                manifest_path=(job_dir / "manifest.json").relative_to(self.store_root).as_posix(),
                created_at=job.created_at,
            )
        )
        self.write_manifest(job, raw, status="running")
        return job

    def resolve_job_dir(self, job: JobRecord) -> Path:
        return self.store_root / job.job_dir

    def resolve_trace_path(self, job: JobRecord) -> Path:
        return self.store_root / job.trace_path

    def resolve_job_metadata_path(self, job: JobRecord | str) -> Path:
        job_id = job.id if isinstance(job, JobRecord) else job
        return self.jobs_root / job_id / "job.json"

    def read_job_metadata(self, job_id: str) -> JobMetadata | None:
        path = self.resolve_job_metadata_path(job_id)
        if not path.exists():
            return None
        return JobMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def write_job_metadata(self, metadata: JobMetadata) -> None:
        path = self.resolve_job_metadata_path(metadata.job_id)
        write_json_file(path, metadata.model_dump(mode="json"))

    def mark_job_finished(
        self,
        job: JobRecord,
        doc: IngestDocument,
        *,
        document_path: str,
        ingest_document_path: str,
    ) -> None:
        metadata = self.read_job_metadata(job.id)
        if metadata is None:
            return
        self.write_job_metadata(
            metadata.model_copy(
                update={
                    "status": "success",
                    "document_path": document_path,
                    "ingest_document_path": ingest_document_path,
                    "manifest_path": f"{job.job_dir}/manifest.json",
                    "ingest_document_id": doc.id,
                    "error": None,
                    "finished_at": now_iso(),
                }
            )
        )

    def mark_job_queued(self, job: JobRecord, raw: RawRecord) -> None:
        self.mark_job_active(job, raw, status="queued")

    def mark_job_running(self, job: JobRecord, raw: RawRecord) -> None:
        self.mark_job_active(job, raw, status="running")

    def mark_job_active(self, job: JobRecord, raw: RawRecord, *, status: str) -> None:
        metadata = self.read_job_metadata(job.id)
        if metadata is None:
            return
        self.write_job_metadata(
            metadata.model_copy(
                update={
                    "status": status,
                    "document_path": None,
                    "ingest_document_path": None,
                    "ingest_document_id": None,
                    "error": None,
                    "finished_at": None,
                }
            )
        )
        self.write_manifest(job, raw, status=status)

    def mark_job_failed(self, job: JobRecord, error: Exception) -> None:
        metadata = self.read_job_metadata(job.id)
        if metadata is None:
            return
        self.write_job_metadata(
            metadata.model_copy(
                update={
                    "status": "failed",
                    "manifest_path": f"{job.job_dir}/manifest.json",
                    "error": error_to_dict(error),
                    "finished_at": now_iso(),
                }
            )
        )

    def write_manifest(
        self,
        job: JobRecord,
        raw: RawRecord,
        *,
        status: str,
        route: list[str] | None = None,
        workers: list[dict[str, Any]] | None = None,
        outputs: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> Path:
        manifest_path = self.store_root / job.job_dir / "manifest.json"
        manifest = {
            "job_id": job.id,
            "status": status,
            "raw_id": raw.raw_id,
            "source_type": job.source_type,
            "source_url": job.source_url,
            "input": {
                "sha256": raw.sha256,
                "mime_type": raw.mime_type,
                "original_filename": raw.original_filename,
                "raw_path": raw.path,
                "size_bytes": raw.size_bytes,
            },
            "route": route or [],
            "workers": workers or [],
            "outputs": {
                "markdown": None,
                "json": None,
                "trace": job.trace_path,
                "manifest": f"{job.job_dir}/manifest.json",
                **(outputs or {}),
            },
            "warnings": warnings or [],
            "errors": errors or [],
            "created_at": job.created_at,
            "updated_at": now_iso(),
        }
        write_json_file(manifest_path, manifest)
        return manifest_path

    def list_jobs(self, *, limit: int | None = None) -> list[JobSummary]:
        if not self.jobs_root.exists():
            return []

        summaries: list[JobSummary] = []
        job_dirs = [path for path in self.jobs_root.iterdir() if path.is_dir()]
        for job_dir in sorted(job_dirs, key=lambda path: path.name, reverse=True):
            summaries.append(self.summarize_job_dir(job_dir))
            if limit is not None and len(summaries) >= limit:
                break
        return summaries

    def summarize_job_dir(self, job_dir: Path) -> JobSummary:
        metadata = read_job_metadata_file(job_dir / "job.json")
        ingest_document = job_dir / "ingest_document.json"
        trace_path = job_dir / "trace.jsonl"
        trace_rel = safe_relative(trace_path, self.store_root)

        if ingest_document.exists():
            payload = json.loads(ingest_document.read_text(encoding="utf-8"))
            source = payload.get("source") if isinstance(payload, dict) else {}
            provenance = payload.get("provenance") if isinstance(payload, dict) else {}
            quality = payload.get("quality") if isinstance(payload, dict) else {}
            return JobSummary(
                job_id=str(payload.get("job_id") or job_dir.name),
                status=metadata.status if metadata is not None else "success",
                raw_id=payload.get("raw_id"),
                source_type=read_mapping_value(source, "source_type"),
                source_url=read_mapping_value(source, "source_url"),
                original_filename=read_mapping_value(source, "original_filename"),
                primary_worker=read_mapping_value(provenance, "primary_worker"),
                quality_score=read_float(read_mapping_value(quality, "score")),
                document_path=metadata.document_path if metadata is not None else safe_relative(job_dir / "document.md", self.store_root),
                ingest_document_path=(
                    metadata.ingest_document_path if metadata is not None else safe_relative(ingest_document, self.store_root)
                ),
                manifest_path=metadata.manifest_path if metadata is not None else safe_relative(job_dir / "manifest.json", self.store_root),
                trace_path=trace_rel,
                created_at=(metadata.created_at if metadata is not None else payload.get("created_at") or payload.get("ingested_at")),
            )

        if metadata is not None:
            return JobSummary(
                job_id=metadata.job_id,
                status=metadata.status,
                raw_id=metadata.raw_id,
                source_type=metadata.source_type,
                source_url=metadata.source_url,
                original_filename=metadata.original_filename,
                document_path=metadata.document_path,
                ingest_document_path=metadata.ingest_document_path,
                manifest_path=metadata.manifest_path,
                trace_path=metadata.trace_path,
                created_at=metadata.created_at,
            )

        trace = read_trace_summary(trace_path)
        return JobSummary(
            job_id=job_dir.name,
            status=trace["status"],
            raw_id=trace.get("raw_id"),
            source_type=trace.get("source_type"),
            source_url=trace.get("source_url"),
            trace_path=trace_rel,
            created_at=trace.get("created_at"),
        )

    def inspect_job(
        self,
        job_id: str,
        *,
        trace_tail_limit: int = 20,
        include_raw: bool = False,
        include_artifacts: bool = False,
    ) -> dict[str, Any] | None:
        job_dir = self.jobs_root / job_id
        ingest_document = job_dir / "ingest_document.json"
        if ingest_document.exists():
            payload = json.loads(ingest_document.read_text(encoding="utf-8"))
            return self.enrich_inspection_payload(job_id, payload, include_raw=include_raw, include_artifacts=include_artifacts)

        metadata_path = job_dir / "job.json"
        trace_path = job_dir / "trace.jsonl"
        if metadata_path.exists():
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload["trace_tail"] = read_trace_tail(trace_path, limit=trace_tail_limit)
            return self.enrich_inspection_payload(job_id, payload, include_raw=include_raw, include_artifacts=include_artifacts)

        if trace_path.exists():
            payload = {
                "job_id": job_id,
                "status": read_trace_summary(trace_path)["status"],
                "trace_path": safe_relative(trace_path, self.store_root),
                "trace_tail": read_trace_tail(trace_path, limit=trace_tail_limit),
            }
            return self.enrich_inspection_payload(job_id, payload, include_raw=include_raw, include_artifacts=include_artifacts)

        return None

    def enrich_inspection_payload(
        self,
        job_id: str,
        payload: dict[str, Any],
        *,
        include_raw: bool,
        include_artifacts: bool,
    ) -> dict[str, Any]:
        enriched = dict(payload)
        if include_raw:
            enriched["raw"] = self.inspect_raw(payload)
        if include_artifacts:
            enriched["artifacts"] = collect_job_artifacts(self.jobs_root / job_id, payload, self.store_root)
        return enriched

    def inspect_raw(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        sha256 = read_payload_sha256(payload)
        if not sha256:
            return None

        meta_path = self.store_root / "raw_store" / sha256 / "original.meta.json"
        if not meta_path.exists():
            return {
                "sha256": sha256,
                "meta_path": safe_relative(meta_path, self.store_root),
                "meta_exists": False,
                "exists": False,
            }

        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        raw_path = self.store_root / str(raw.get("path", ""))
        raw["meta_path"] = safe_relative(meta_path, self.store_root)
        raw["meta_exists"] = True
        raw["absolute_path"] = str(raw_path.resolve())
        raw["exists"] = raw_path.exists()
        return raw


def read_job_metadata_file(path: Path) -> JobMetadata | None:
    if not path.exists():
        return None
    return JobMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(20):
        try:
            temp_path.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


def read_payload_sha256(payload: dict[str, Any]) -> str | None:
    sha256 = payload.get("sha256")
    if isinstance(sha256, str) and sha256:
        return sha256
    source = payload.get("source")
    if isinstance(source, dict):
        sha256 = source.get("sha256")
        if isinstance(sha256, str) and sha256:
            return sha256
    return None


def collect_job_artifacts(job_dir: Path, payload: dict[str, Any], store_root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for artifact in declared_artifacts(payload):
        if not isinstance(artifact, dict):
            continue
        path_value = artifact.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue
        artifact_path = job_dir / path_value
        item = dict(artifact)
        item["source"] = "declared"
        item["absolute_path"] = str(artifact_path.resolve())
        item["exists"] = artifact_path.exists()
        artifacts.append(item)
        seen.add(normalize_artifact_path(path_value))

    intermediate_dir = job_dir / "intermediate"
    if intermediate_dir.exists():
        for path in sorted(intermediate_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(job_dir).as_posix()
            if normalize_artifact_path(relative) in seen:
                continue
            artifacts.append(
                {
                    "type": "intermediate_file",
                    "source": "discovered",
                    "path": relative,
                    "absolute_path": str(path.resolve()),
                    "exists": True,
                    "size_bytes": path.stat().st_size,
                }
            )
            seen.add(normalize_artifact_path(relative))

    return artifacts


def declared_artifacts(payload: dict[str, Any]) -> list[Any]:
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        artifacts = provenance.get("artifacts")
        if isinstance(artifacts, list):
            return artifacts
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        return artifacts
    return []


def normalize_artifact_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def read_trace_summary(trace_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": "unknown"}
    if not trace_path.exists():
        return summary

    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if event.get("event") == "job_started":
            summary["created_at"] = event.get("timestamp")
            summary["raw_id"] = details.get("raw_id")
            summary["source_type"] = details.get("source_type")
            summary["source_url"] = details.get("source_url")
        if event.get("event") == "raw_saved" and event.get("raw_id"):
            summary["raw_id"] = event.get("raw_id")
        if event.get("event") == "job_finished":
            summary["status"] = "success"
        if event.get("event") == "job_failed":
            summary["status"] = "failed"
    return summary


def read_trace_tail(trace_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    return read_trace_events(trace_path, limit=limit)


def read_trace_events(trace_path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not trace_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    if limit is None:
        return events
    return events[-max(limit, 0) :] if limit else []


def render_trace_jsonl(trace_path: Path, *, limit: int | None = None) -> str:
    events = read_trace_events(trace_path, limit=limit)
    if not events:
        return ""
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def read_mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def read_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
