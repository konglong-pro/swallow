from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swallow.core.errors import error_to_dict
from swallow.core.models import IngestDocument, JobRecord, RawRecord, WorkerResult
from swallow.core.time import now_iso


class TraceWriter:
    def __init__(self, trace_path: Path | str) -> None:
        self.trace_path = Path(trace_path)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        event: str,
        *,
        job_id: str,
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        record = {
            "event": event,
            "job_id": job_id,
            "timestamp": now_iso(),
            **fields,
            "details": details or {},
        }
        with self.trace_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def job_started(self, job: JobRecord, raw: RawRecord) -> None:
        self.write(
            "job_started",
            job_id=job.id,
            details={
                "raw_id": raw.raw_id,
                "source_type": job.source_type,
                "source_url": job.source_url,
            },
        )

    def raw_saved(self, job: JobRecord, raw: RawRecord) -> None:
        self.write(
            "raw_saved",
            job_id=job.id,
            raw_id=raw.raw_id,
            details={
                "sha256": raw.sha256,
                "path": raw.path,
                "mime_type": raw.mime_type,
                "size_bytes": raw.size_bytes,
            },
        )

    def route_selected(self, job: JobRecord, plan: list[str]) -> None:
        self.write("route_selected", job_id=job.id, details={"plan": plan})

    def fallback_selected(
        self,
        job: JobRecord,
        *,
        worker_name: str,
        reason: str,
        quality_score: float | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.write(
            "fallback_selected",
            job_id=job.id,
            worker=worker_name,
            details={
                "reason": reason,
                "quality_score": quality_score,
                "warnings": warnings or [],
            },
        )

    def worker_started(
        self,
        job: JobRecord,
        worker_name: str,
        worker_version: str,
        *,
        raw_id: str | None = None,
        input_path: str | None = None,
        started_at: str | None = None,
        params_hash: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if raw_id is not None:
            fields["raw_id"] = raw_id
        if input_path is not None:
            fields["input_path"] = input_path
        if started_at is not None:
            fields["started_at"] = started_at
        if params_hash is not None:
            fields["params_hash"] = params_hash
        self.write(
            "worker_started",
            job_id=job.id,
            worker=worker_name,
            worker_version=worker_version,
            details={"params": params} if params is not None else None,
            **fields,
        )

    def worker_finished(
        self,
        job: JobRecord,
        result: WorkerResult,
        *,
        duration_ms: int,
        output_hash: str | None = None,
        raw_id: str | None = None,
        input_path: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        params_hash: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if raw_id is not None:
            fields["raw_id"] = raw_id
        if input_path is not None:
            fields["input_path"] = input_path
        if started_at is not None:
            fields["started_at"] = started_at
        if finished_at is not None:
            fields["finished_at"] = finished_at
        if params_hash is not None:
            fields["params_hash"] = params_hash
        details: dict[str, Any] = {
            "warnings": result.warnings,
            "errors": result.errors,
            "metadata": result.metadata,
        }
        if params is not None:
            details["params"] = params
        self.write(
            "worker_finished",
            job_id=job.id,
            worker=result.worker_name,
            worker_version=result.worker_version,
            status=result.status,
            duration_ms=duration_ms,
            output_hash=output_hash,
            details=details,
            **fields,
        )

    def worker_failed(
        self,
        job: JobRecord,
        worker_name: str,
        worker_version: str,
        error: Exception,
        *,
        duration_ms: int,
        raw_id: str | None = None,
        input_path: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        params_hash: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if raw_id is not None:
            fields["raw_id"] = raw_id
        if input_path is not None:
            fields["input_path"] = input_path
        if started_at is not None:
            fields["started_at"] = started_at
        if finished_at is not None:
            fields["finished_at"] = finished_at
        if params_hash is not None:
            fields["params_hash"] = params_hash
        details: dict[str, Any] = {"error": error_to_dict(error)}
        if params is not None:
            details["params"] = params
        self.write(
            "worker_failed",
            job_id=job.id,
            worker=worker_name,
            worker_version=worker_version,
            status="failed",
            duration_ms=duration_ms,
            details=details,
            **fields,
        )

    def quality_checked(self, job: JobRecord, quality: Any) -> None:
        self.write(
            "quality_checked",
            job_id=job.id,
            status="success" if quality.score >= 0.75 else "partial" if quality.score >= 0.45 else "failed",
            details=quality.model_dump(mode="json"),
        )

    def security_error(
        self,
        job: JobRecord,
        *,
        worker_name: str | None = None,
        raw_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {"status": "failed"}
        if worker_name is not None:
            fields["worker"] = worker_name
        if raw_id is not None:
            fields["raw_id"] = raw_id
        self.write("security_error", job_id=job.id, details=details or {}, **fields)

    def document_built(self, job: JobRecord, doc: IngestDocument) -> None:
        self.write(
            "document_built",
            job_id=job.id,
            details={"ingest_document_id": doc.id, "raw_id": doc.raw_id},
        )

    def document_written(self, job: JobRecord, document_path: str, ingest_document_path: str) -> None:
        self.write(
            "document_written",
            job_id=job.id,
            details={
                "document_path": document_path,
                "ingest_document_path": ingest_document_path,
            },
        )

    def job_finished(self, job: JobRecord, doc: IngestDocument) -> None:
        self.write(
            "job_finished",
            job_id=job.id,
            status="success",
            details={"ingest_document_id": doc.id},
        )

    def job_failed(self, job: JobRecord, error: Exception) -> None:
        self.write(
            "job_failed",
            job_id=job.id,
            status="failed",
            details={"error": error_to_dict(error)},
        )
