from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from swallow.core.batch import BatchTraceWriter
from swallow.core.errors import InputError, SystemResourceError, error_to_dict
from swallow.core.ids import new_batch_id
from swallow.core.job_store import JobStore, safe_relative
from swallow.core.models import JobRecord, RawRecord
from swallow.core.time import now_iso
from swallow.core.trace import TraceWriter


QUEUE_TERMINAL_STATUSES = {"success", "partial", "failed", "canceled"}
BATCH_TERMINAL_STATUSES = {"success", "partial", "failed", "canceled"}


@dataclass(frozen=True)
class QueueItem:
    job_id: str
    batch_id: str | None
    input_label: str | None
    status: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    worker_id: str | None = None


@dataclass(frozen=True)
class QueuePreparedJob:
    raw: RawRecord
    job: JobRecord


class QueueStore:
    def __init__(self, store_root: Path | str = ".") -> None:
        self.store_root = Path(store_root)
        self.queue_root = self.store_root / "queue"
        self.queue_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.queue_root / "queue.sqlite3"
        self.job_store = JobStore(self.store_root)
        self._init_db()

    def enqueue_job(
        self,
        job: JobRecord,
        *,
        max_attempts: int,
        batch_id: str | None = None,
        input_label: str | None = None,
        order_index: int | None = None,
    ) -> QueueItem:
        now = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                insert into queue_items (
                  job_id, batch_id, input_label, order_index, status, attempts, max_attempts,
                  cancel_requested, created_at, updated_at
                ) values (?, ?, ?, ?, 'queued', 0, ?, 0, ?, ?)
                """,
                (job.id, batch_id, input_label, order_index, max_attempts, now, now),
            )
        if batch_id is not None:
            self.refresh_batch(batch_id)
        return self.get_item(job.id)

    def create_batch(
        self,
        *,
        patterns: Sequence[str],
        total: int,
        workers: int,
        warnings: list[str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> str:
        batch_id = new_batch_id()
        batch_dir = self.store_root / "batch_runs" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=False)
        summary_path = safe_relative(batch_dir / "summary.json", self.store_root)
        trace_path = safe_relative(batch_dir / "trace.jsonl", self.store_root)
        status = "failed" if total == 0 else "queued"
        now = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                insert into batches (
                  batch_id, status, total, workers, summary_path, trace_path,
                  warnings_json, errors_json, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    status,
                    total,
                    workers,
                    summary_path,
                    trace_path,
                    json.dumps(warnings or [], ensure_ascii=False),
                    json.dumps(errors or [], ensure_ascii=False),
                    now,
                    now,
                ),
            )
        trace = BatchTraceWriter(batch_dir / "trace.jsonl", batch_id=batch_id)
        trace.write("batch_submitted", status=status, details={"patterns": list(patterns), "total": total})
        if total == 0:
            self.refresh_batch(batch_id)
        return batch_id

    def get_item(self, job_id: str) -> QueueItem:
        with self._connect() as connection:
            row = connection.execute("select * from queue_items where job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise InputError(f"Queue item not found: {job_id}", code="QUEUE_ITEM_NOT_FOUND")
        return queue_item_from_row(row)

    def claim_next(self, *, worker_id: str, lease_seconds: float) -> QueueItem | None:
        self.recover_stale_leases()
        now_epoch = time.time()
        lease_expires_at = now_epoch + lease_seconds
        now = now_iso()
        with self._connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select * from queue_items
                where status = 'queued'
                order by created_at asc, order_index asc, job_id asc
                limit 1
                """
            ).fetchone()
            if row is None:
                connection.execute("commit")
                return None
            connection.execute(
                """
                update queue_items
                set status = 'running',
                    attempts = attempts + 1,
                    worker_id = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    updated_at = ?
                where job_id = ?
                """,
                (worker_id, lease_expires_at, now_epoch, now, row["job_id"]),
            )
            connection.execute("commit")
        item = self.get_item(str(row["job_id"]))
        prepared = self.read_prepared_job(item.job_id)
        self.job_store.mark_job_running(prepared.job, prepared.raw)
        self._write_job_queue_event(item.job_id, "queue_job_claimed", {"worker_id": worker_id, "attempt": item.attempts})
        return item

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: float) -> None:
        now_epoch = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                update queue_items
                set heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                where job_id = ? and worker_id = ? and status = 'running'
                """,
                (now_epoch, now_epoch + lease_seconds, now_iso(), job_id, worker_id),
            )

    def complete_job(self, job_id: str, *, status: str) -> None:
        item = self.get_item(job_id)
        now = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                update queue_items
                set status = ?, worker_id = null, lease_expires_at = null, heartbeat_at = null, updated_at = ?
                where job_id = ?
                """,
                (status, now, job_id),
            )
        self._write_job_queue_event(job_id, "queue_job_finished", {"status": status})
        if item.batch_id is not None:
            self._write_batch_item_event(item.batch_id, job_id)
            self.refresh_batch(item.batch_id)

    def fail_or_retry_job(self, job_id: str, error: Exception) -> str:
        item = self.get_item(job_id)
        error_payload = error_to_dict(error)
        now = now_iso()
        if item.cancel_requested:
            status = "canceled"
            self._mark_job_canceled(job_id)
        elif bool(error_payload.get("retryable")) and item.attempts < item.max_attempts:
            status = "queued"
            prepared = self.read_prepared_job(job_id)
            self.job_store.mark_job_queued(prepared.job, prepared.raw)
        else:
            status = "failed"
            self._mark_job_failed_if_needed(job_id, error)

        with self._connect() as connection:
            connection.execute(
                """
                update queue_items
                set status = ?,
                    worker_id = null,
                    lease_expires_at = null,
                    heartbeat_at = null,
                    last_error_json = ?,
                    updated_at = ?
                where job_id = ?
                """,
                (status, json.dumps(error_payload, ensure_ascii=False), now, job_id),
            )
        self._write_job_queue_event(job_id, "queue_job_retrying" if status == "queued" else "queue_job_failed", {"status": status, "error": error_payload})
        if item.batch_id is not None:
            self._write_batch_item_event(item.batch_id, job_id)
            self.refresh_batch(item.batch_id)
        return status

    def recover_stale_leases(self) -> int:
        now_epoch = time.time()
        with self._connect() as connection:
            rows = connection.execute(
                """
                select * from queue_items
                where status = 'running' and lease_expires_at is not null and lease_expires_at < ?
                """,
                (now_epoch,),
            ).fetchall()
        recovered = 0
        for row in rows:
            item = queue_item_from_row(row)
            if item.cancel_requested:
                self._set_queue_status(item.job_id, "canceled", error={"code": "JOB_CANCELED", "message": "Job canceled."})
                self._mark_job_canceled(item.job_id)
            elif item.attempts < item.max_attempts:
                prepared = self.read_prepared_job(item.job_id)
                self.job_store.mark_job_queued(prepared.job, prepared.raw)
                self._set_queue_status(item.job_id, "queued")
            else:
                error = SystemResourceError("Queue lease expired after max attempts", code="QUEUE_LEASE_EXPIRED")
                self._mark_job_failed_if_needed(item.job_id, error)
                self._set_queue_status(item.job_id, "failed", error=error_to_dict(error))
            if item.batch_id is not None:
                self.refresh_batch(item.batch_id)
            recovered += 1
        return recovered

    def cancel_job(self, job_id: str) -> bool:
        item = self.get_item(job_id)
        if item.status in QUEUE_TERMINAL_STATUSES:
            return False
        if item.status == "queued":
            self._mark_job_canceled(job_id)
            self._set_queue_status(job_id, "canceled", cancel_requested=True, error={"code": "JOB_CANCELED", "message": "Job canceled."})
            if item.batch_id is not None:
                self._write_batch_item_event(item.batch_id, job_id)
                self.refresh_batch(item.batch_id)
            return True
        self._set_cancel_requested(job_id)
        return True

    def cancel_batch(self, batch_id: str) -> bool:
        rows = self.list_batch_items(batch_id)
        changed = False
        for item in rows:
            if item.status not in QUEUE_TERMINAL_STATUSES:
                changed = self.cancel_job(item.job_id) or changed
        self.refresh_batch(batch_id)
        return changed

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        self.refresh_batch(batch_id)
        with self._connect() as connection:
            row = connection.execute("select * from batches where batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            raise InputError(f"Batch not found: {batch_id}", code="BATCH_NOT_FOUND")
        return self._batch_payload(row)

    def list_batch_items(self, batch_id: str) -> list[QueueItem]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from queue_items where batch_id = ? order by order_index asc, job_id asc",
                (batch_id,),
            ).fetchall()
        return [queue_item_from_row(row) for row in rows]

    def refresh_batch(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            batch = connection.execute("select * from batches where batch_id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise InputError(f"Batch not found: {batch_id}", code="BATCH_NOT_FOUND")
        payload = self._batch_payload(batch, refresh=False)
        summary = {
            "batch_id": payload["batch_id"],
            "status": payload["status"],
            "total": payload["total"],
            "queued": payload["queued"],
            "running": payload["running"],
            "success": payload["success"],
            "partial": payload["partial"],
            "failed": payload["failed"],
            "canceled": payload["canceled"],
            "jobs": payload["jobs"],
            "started_at": payload["created_at"],
            "finished_at": now_iso() if payload["status"] in BATCH_TERMINAL_STATUSES else None,
            "warnings": payload["warnings"],
            "errors": payload["errors"],
            "summary_path": payload["summary_path"],
            "trace_path": payload["trace_path"],
        }
        summary_path = self.store_root / payload["summary_path"]
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        finished_at = None
        if payload["status"] in BATCH_TERMINAL_STATUSES:
            finished_at = now_iso()
            trace_path = self.store_root / payload["trace_path"]
            existing = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
            if "batch_finished" not in existing:
                BatchTraceWriter(trace_path, batch_id=batch_id).write(
                    "batch_finished",
                    status=payload["status"],
                    details={key: payload[key] for key in ("total", "success", "partial", "failed", "canceled")},
                )
        with self._connect() as connection:
            connection.execute(
                "update batches set status = ?, updated_at = ?, finished_at = coalesce(finished_at, ?) where batch_id = ?",
                (payload["status"], now_iso(), finished_at, batch_id),
            )
        return summary

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("select status, count(*) as count from queue_items group by status").fetchall()
            batches = connection.execute("select status, count(*) as count from batches group by status").fetchall()
        return {
            "queue_path": safe_relative(self.db_path, self.store_root),
            "jobs": {str(row["status"]): int(row["count"]) for row in rows},
            "batches": {str(row["status"]): int(row["count"]) for row in batches},
        }

    def read_prepared_job(self, job_id: str) -> QueuePreparedJob:
        metadata = self.job_store.read_job_metadata(job_id)
        if metadata is None:
            raise InputError(f"Job not found: {job_id}", code="JOB_NOT_FOUND")
        raw_path = self.store_root / "raw_store" / metadata.sha256 / "original.meta.json"
        if not raw_path.exists():
            raise InputError(f"Raw metadata not found for job {job_id}: {raw_path}", code="RAW_METADATA_NOT_FOUND")
        raw = RawRecord.model_validate_json(raw_path.read_text(encoding="utf-8"))
        return QueuePreparedJob(
            raw=raw,
            job=JobRecord(
                id=metadata.job_id,
                raw_id=metadata.raw_id,
                source_type=metadata.source_type,
                source_url=metadata.source_url,
                job_dir=metadata.job_dir,
                trace_path=metadata.trace_path,
                created_at=metadata.created_at,
            ),
        )

    def _batch_payload(self, row: sqlite3.Row, *, refresh: bool = True) -> dict[str, Any]:
        items = self.list_batch_items(str(row["batch_id"]))
        counts = {status: sum(1 for item in items if item.status == status) for status in ["queued", "running", "success", "partial", "failed", "canceled"]}
        total = int(row["total"])
        status = compute_batch_status(total=total, counts=counts, errors=read_json_list(row["errors_json"]))
        jobs = [self._batch_job_payload(item) for item in items]
        return {
            "batch_id": str(row["batch_id"]),
            "status": status,
            "total": total,
            **counts,
            "job_ids": [item.job_id for item in items],
            "jobs": jobs,
            "summary_path": str(row["summary_path"]),
            "trace_path": str(row["trace_path"]),
            "created_at": str(row["created_at"]),
            "warnings": read_json_list(row["warnings_json"]),
            "errors": read_json_list(row["errors_json"]),
        }

    def _batch_job_payload(self, item: QueueItem) -> dict[str, Any]:
        metadata = self.job_store.read_job_metadata(item.job_id)
        error = None
        with self._connect() as connection:
            row = connection.execute("select last_error_json from queue_items where job_id = ?", (item.job_id,)).fetchone()
        if row is not None and row["last_error_json"]:
            error = json.loads(str(row["last_error_json"]))
        if error is None and metadata is not None:
            error = metadata.error
        return {
            "input": item.input_label,
            "job_id": item.job_id,
            "status": item.status,
            "attempts": item.attempts,
            "document": metadata.document_path if metadata is not None else None,
            "manifest": metadata.manifest_path if metadata is not None else None,
            "trace": metadata.trace_path if metadata is not None else None,
            "error": error,
        }

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute("pragma journal_mode = wal")
            connection.execute(
                """
                create table if not exists queue_items (
                  job_id text primary key,
                  batch_id text,
                  input_label text,
                  order_index integer,
                  status text not null,
                  attempts integer not null default 0,
                  max_attempts integer not null default 3,
                  worker_id text,
                  lease_expires_at real,
                  heartbeat_at real,
                  cancel_requested integer not null default 0,
                  last_error_json text,
                  created_at text not null,
                  updated_at text not null
                )
                """
            )
            connection.execute(
                """
                create table if not exists batches (
                  batch_id text primary key,
                  status text not null,
                  total integer not null,
                  workers integer not null,
                  summary_path text not null,
                  trace_path text not null,
                  warnings_json text not null,
                  errors_json text not null,
                  created_at text not null,
                  updated_at text not null,
                  finished_at text
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _set_queue_status(
        self,
        job_id: str,
        status: str,
        *,
        cancel_requested: bool | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        fields = ["status = ?", "worker_id = null", "lease_expires_at = null", "heartbeat_at = null", "updated_at = ?"]
        values: list[Any] = [status, now_iso()]
        if cancel_requested is not None:
            fields.append("cancel_requested = ?")
            values.append(1 if cancel_requested else 0)
        if error is not None:
            fields.append("last_error_json = ?")
            values.append(json.dumps(error, ensure_ascii=False))
        values.append(job_id)
        with self._connect() as connection:
            connection.execute(f"update queue_items set {', '.join(fields)} where job_id = ?", values)

    def _set_cancel_requested(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "update queue_items set cancel_requested = 1, updated_at = ? where job_id = ?",
                (now_iso(), job_id),
            )

    def _mark_job_canceled(self, job_id: str) -> None:
        error = InputError("Job canceled.", code="JOB_CANCELED")
        self._mark_job_failed_if_needed(job_id, error)

    def _mark_job_failed_if_needed(self, job_id: str, error: Exception) -> None:
        prepared = self.read_prepared_job(job_id)
        self.job_store.mark_job_failed(prepared.job, error)
        self.job_store.write_manifest(
            prepared.job,
            prepared.raw,
            status="failed",
            outputs={"trace": prepared.job.trace_path, "manifest": f"{prepared.job.job_dir}/manifest.json"},
            errors=[error_to_dict(error)],
        )
        TraceWriter(self.job_store.resolve_trace_path(prepared.job)).job_failed(prepared.job, error)

    def _write_job_queue_event(self, job_id: str, event: str, details: dict[str, Any]) -> None:
        prepared = self.read_prepared_job(job_id)
        TraceWriter(self.job_store.resolve_trace_path(prepared.job)).write(event, job_id=job_id, details=details)

    def _write_batch_item_event(self, batch_id: str, job_id: str) -> None:
        payload = self._batch_job_payload(self.get_item(job_id))
        with self._connect() as connection:
            row = connection.execute("select trace_path from batches where batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            return
        event = "input_canceled" if payload["status"] == "canceled" else "input_failed" if payload["status"] == "failed" else "input_finished"
        BatchTraceWriter(self.store_root / str(row["trace_path"]), batch_id=batch_id).write(event, status=payload["status"], details=payload)


def queue_item_from_row(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        job_id=str(row["job_id"]),
        batch_id=str(row["batch_id"]) if row["batch_id"] is not None else None,
        input_label=str(row["input_label"]) if row["input_label"] is not None else None,
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        cancel_requested=bool(row["cancel_requested"]),
        worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
    )


def compute_batch_status(*, total: int, counts: dict[str, int], errors: list[Any]) -> str:
    if total == 0:
        return "failed" if errors else "success"
    if counts["running"] > 0:
        return "running"
    if counts["queued"] > 0:
        finished = counts["success"] + counts["partial"] + counts["failed"] + counts["canceled"]
        return "running" if finished else "queued"
    if counts["canceled"] == total:
        return "canceled"
    if counts["failed"] == total:
        return "failed"
    if counts["failed"] or counts["partial"] or counts["canceled"]:
        return "partial"
    return "success"


def read_json_list(value: Any) -> list[Any]:
    if not isinstance(value, str):
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []
