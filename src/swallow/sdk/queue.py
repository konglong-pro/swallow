from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from swallow.core.batch import expand_batch_inputs, path_label
from swallow.core.errors import InputError, error_to_dict
from swallow.core.job_store import JobStore
from swallow.core.queue_store import BATCH_TERMINAL_STATUSES, QueueStore
from swallow.core.raw_store import RawStore
from swallow.core.time import now_iso
from swallow.sdk.errors import IngestSdkConfigError, IngestSdkError, IngestSdkInputError, IngestWaitTimeout
from swallow.sdk.models import (
    BatchStatus,
    IngestBatch,
    IngestBatchJob,
    IngestBatchResult,
    IngestError,
    IngestJob,
    IngestResult,
    IngestWarning,
    ReturnContentMode,
    StorageMode,
)
from swallow.sdk.result_mapper import TERMINAL_STATUSES, map_error, map_job, map_result, map_warning
from swallow.sdk.security import default_allowed_roots, resolve_allowed_file, resolve_from_cwd, validate_ingest_url


class QueueIngestClient:
    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        storage_mode: StorageMode = "persistent",
        cwd: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if storage_mode != "persistent":
            raise IngestSdkConfigError(f"Unsupported storage mode for QueueIngestClient: {storage_mode}")
        if max_attempts < 1:
            raise IngestSdkConfigError("max_attempts must be at least 1")
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.store_root = resolve_from_cwd(store_root, self.cwd)
        roots = allowed_roots if allowed_roots is not None else default_allowed_roots(self.cwd, self.store_root)
        self.allowed_roots = [resolve_from_cwd(root, self.cwd) for root in roots]
        self.max_attempts = max_attempts
        self.raw_store = RawStore(self.store_root)
        self.job_store = JobStore(self.store_root)
        self.queue_store = QueueStore(self.store_root)

    def submit_file(self, path: Path | str) -> IngestJob:
        try:
            raw = self.raw_store.save_immutable(resolve_allowed_file(path, cwd=self.cwd, allowed_roots=self.allowed_roots))
        except FileNotFoundError as error:
            raise IngestSdkInputError(str(error)) from error
        job = self.job_store.create_job(raw, source_type="file")
        self.job_store.mark_job_queued(job, raw)
        self.queue_store.enqueue_job(job, max_attempts=self.max_attempts)
        return map_job(self.store_root, job.id)

    def submit_url(self, url: str) -> IngestJob:
        try:
            raw = self.raw_store.save_url_reference(validate_ingest_url(url))
        except ValueError as error:
            raise IngestSdkInputError(str(error)) from error
        job = self.job_store.create_job(raw, source_type="url", source_url=url)
        self.job_store.mark_job_queued(job, raw)
        self.queue_store.enqueue_job(job, max_attempts=self.max_attempts)
        return map_job(self.store_root, job.id)

    def submit_browser_capture(self, path: Path | str) -> IngestJob:
        try:
            raw = self.raw_store.save_immutable(resolve_allowed_file(path, cwd=self.cwd, allowed_roots=self.allowed_roots))
        except FileNotFoundError as error:
            raise IngestSdkInputError(str(error)) from error
        job = self.job_store.create_job(raw, source_type="browser_capture")
        self.job_store.mark_job_queued(job, raw)
        self.queue_store.enqueue_job(job, max_attempts=self.max_attempts)
        return map_job(self.store_root, job.id)

    def submit_archive(self, path: Path | str) -> IngestJob:
        try:
            raw = self.raw_store.save_immutable(resolve_allowed_file(path, cwd=self.cwd, allowed_roots=self.allowed_roots))
        except FileNotFoundError as error:
            raise IngestSdkInputError(str(error)) from error
        job = self.job_store.create_job(raw, source_type="export_archive")
        self.job_store.mark_job_queued(job, raw)
        self.queue_store.enqueue_job(job, max_attempts=self.max_attempts)
        return map_job(self.store_root, job.id)

    def submit_batch(self, patterns: Sequence[str], *, workers: int = 1) -> IngestBatch:
        if workers < 1:
            raise IngestSdkConfigError("workers must be at least 1")
        inputs, warnings = expand_batch_inputs(patterns)
        inputs = [
            resolve_allowed_file(path, cwd=self.cwd, allowed_roots=self.allowed_roots)
            for path in inputs
            if path.is_file()
        ]
        errors: list[dict[str, Any]] = []
        if not inputs:
            errors.append(error_to_dict(InputError("No batch input files matched.", code="NO_BATCH_INPUTS")))
        batch_id = self.queue_store.create_batch(patterns=patterns, total=len(inputs), workers=workers, warnings=warnings, errors=errors)
        for index, input_path in enumerate(inputs):
            try:
                raw = self.raw_store.save_immutable(input_path)
                job = self.job_store.create_job(raw, source_type="file")
                self.job_store.mark_job_queued(job, raw)
                self.queue_store.enqueue_job(
                    job,
                    max_attempts=self.max_attempts,
                    batch_id=batch_id,
                    input_label=path_label(input_path),
                    order_index=index,
                )
            except Exception as error:
                errors.append(error_to_dict(error))
        self.queue_store.refresh_batch(batch_id)
        return self.get_batch(batch_id)

    def get_job(self, job_id: str) -> IngestJob:
        return map_job(self.store_root, job_id)

    def get_result(self, job_id: str, *, content: ReturnContentMode = "preview") -> IngestResult:
        return map_result(self.store_root, job_id, content=content)

    def wait(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.1,
        content: ReturnContentMode = "preview",
    ) -> IngestResult:
        if poll_interval <= 0:
            raise IngestSdkConfigError("poll_interval must be greater than 0")
        started = time.monotonic()
        while True:
            result = self.get_result(job_id, content=content)
            if result.status in TERMINAL_STATUSES:
                return result
            if timeout is not None:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise IngestWaitTimeout(f"Timed out waiting for job {job_id} after {timeout:g}s")
                time.sleep(min(poll_interval, max(timeout - elapsed, 0)))
            else:
                time.sleep(poll_interval)

    def get_batch(self, batch_id: str) -> IngestBatch:
        try:
            payload = self.queue_store.get_batch(batch_id)
        except InputError as error:
            raise IngestSdkInputError(str(error)) from error
        return batch_from_payload(payload)

    def get_batch_result(self, batch_id: str) -> IngestBatchResult:
        try:
            payload = self.queue_store.get_batch(batch_id)
        except InputError as error:
            raise IngestSdkInputError(str(error)) from error
        return batch_result_from_payload(payload)

    def wait_batch(
        self,
        batch_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.2,
    ) -> IngestBatchResult:
        if poll_interval <= 0:
            raise IngestSdkConfigError("poll_interval must be greater than 0")
        started = time.monotonic()
        while True:
            result = self.get_batch_result(batch_id)
            if result.status in BATCH_TERMINAL_STATUSES:
                return result
            if timeout is not None:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise IngestWaitTimeout(f"Timed out waiting for batch {batch_id} after {timeout:g}s")
                time.sleep(min(poll_interval, max(timeout - elapsed, 0)))
            else:
                time.sleep(poll_interval)

    def cancel_job(self, job_id: str) -> IngestJob:
        try:
            self.queue_store.cancel_job(job_id)
        except InputError as error:
            raise IngestSdkInputError(str(error)) from error
        return self.get_job(job_id)

    def cancel_batch(self, batch_id: str) -> IngestBatch:
        try:
            self.queue_store.cancel_batch(batch_id)
        except InputError as error:
            raise IngestSdkInputError(str(error)) from error
        return self.get_batch(batch_id)

    def stats(self) -> dict[str, Any]:
        return self.queue_store.stats()

    def file(self, path: Path | str, *, wait: bool = True, content: ReturnContentMode = "preview") -> IngestResult | IngestJob:
        job = self.submit_file(path)
        if not wait:
            return job
        return self.wait(job.job_id, content=content)

    def url(self, url: str, *, wait: bool = True, content: ReturnContentMode = "preview") -> IngestResult | IngestJob:
        job = self.submit_url(url)
        if not wait:
            return job
        return self.wait(job.job_id, content=content)

    def browser_capture(
        self,
        path: Path | str,
        *,
        wait: bool = True,
        content: ReturnContentMode = "preview",
    ) -> IngestResult | IngestJob:
        job = self.submit_browser_capture(path)
        if not wait:
            return job
        return self.wait(job.job_id, content=content)

    def archive(self, path: Path | str, *, wait: bool = True, content: ReturnContentMode = "preview") -> IngestResult | IngestJob:
        job = self.submit_archive(path)
        if not wait:
            return job
        return self.wait(job.job_id, content=content)

    def __enter__(self) -> QueueIngestClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def batch_from_payload(payload: dict[str, Any]) -> IngestBatch:
    return IngestBatch(
        batch_id=str(payload["batch_id"]),
        status=read_batch_status(payload.get("status")),
        total=int(payload.get("total") or 0),
        queued=int(payload.get("queued") or 0),
        running=int(payload.get("running") or 0),
        success=int(payload.get("success") or 0),
        partial=int(payload.get("partial") or 0),
        failed=int(payload.get("failed") or 0),
        canceled=int(payload.get("canceled") or 0),
        job_ids=[str(job_id) for job_id in payload.get("job_ids", []) if job_id],
        created_at=str(payload["created_at"]) if payload.get("created_at") is not None else None,
        summary_path=str(payload["summary_path"]),
        trace_path=str(payload["trace_path"]),
    )


def batch_result_from_payload(payload: dict[str, Any]) -> IngestBatchResult:
    base = batch_from_payload(payload)
    return IngestBatchResult(
        **base.model_dump(mode="python"),
        jobs=[batch_job_from_payload(item) for item in payload.get("jobs", []) if isinstance(item, dict)],
        warnings=[batch_warning(item) for item in payload.get("warnings", [])],
        errors=[batch_error(item) for item in payload.get("errors", [])],
    )


def batch_job_from_payload(payload: dict[str, Any]) -> IngestBatchJob:
    error_payload = payload.get("error")
    return IngestBatchJob(
        input=str(payload["input"]) if payload.get("input") is not None else None,
        job_id=str(payload["job_id"]) if payload.get("job_id") is not None else None,
        status=read_batch_status(payload.get("status")),
        attempts=int(payload.get("attempts") or 0),
        document=str(payload["document"]) if payload.get("document") is not None else None,
        manifest=str(payload["manifest"]) if payload.get("manifest") is not None else None,
        trace=str(payload["trace"]) if payload.get("trace") is not None else None,
        error=batch_error(error_payload) if error_payload else None,
    )


def batch_warning(item: Any) -> IngestWarning:
    if isinstance(item, dict):
        return map_warning(item)
    return IngestWarning(message=str(item))


def batch_error(item: Any) -> IngestError:
    return map_error(item)


def read_batch_status(value: Any) -> BatchStatus:
    if value in {"queued", "running", "success", "partial", "failed", "canceled"}:
        return value
    raise IngestSdkError(f"Unsupported batch status: {value}")
