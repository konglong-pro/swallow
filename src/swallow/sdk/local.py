from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Sequence

from swallow.core.config import IngestConfig
from swallow.core.errors import IngestError as CoreIngestError
from swallow.core.runner import IngestRunner, PreparedIngestJob
from swallow.sdk.errors import IngestSdkConfigError, IngestSdkError, IngestSdkInputError, IngestWaitTimeout
from swallow.sdk.models import IngestJob, IngestResult, ReturnContentMode, StorageMode
from swallow.sdk.result_mapper import TERMINAL_STATUSES, map_job, map_result
from swallow.sdk.security import default_allowed_roots, resolve_allowed_file, resolve_from_cwd


class LocalIngestClient:
    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        config: IngestConfig | None = None,
        storage_mode: StorageMode = "persistent",
        cwd: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        max_workers: int = 2,
    ) -> None:
        if storage_mode != "persistent":
            raise IngestSdkConfigError(f"Unsupported storage mode for LocalIngestClient: {storage_mode}")
        if max_workers < 1:
            raise IngestSdkConfigError("max_workers must be at least 1")
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.store_root = resolve_from_cwd(store_root, self.cwd)
        self.config = config or IngestConfig()
        self.storage_mode = storage_mode
        roots = allowed_roots if allowed_roots is not None else default_allowed_roots(self.cwd, self.store_root)
        self.allowed_roots = [resolve_from_cwd(root, self.cwd) for root in roots]
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="swallow-sdk-local")
        self._futures: dict[str, Future] = {}
        self._lock = Lock()

    def submit_file(self, path: Path | str) -> IngestJob:
        runner = self._make_runner()
        try:
            prepared = runner.prepare_file_job(resolve_allowed_file(path, cwd=self.cwd, allowed_roots=self.allowed_roots))
        except FileNotFoundError as error:
            raise IngestSdkInputError(str(error)) from error
        except CoreIngestError as error:
            raise IngestSdkInputError(str(error)) from error

        job = map_job(self.store_root, prepared.job.id)
        future = self._executor.submit(self._run_prepared_file, prepared)
        with self._lock:
            self._futures[prepared.job.id] = future
        return job

    def get_job(self, job_id: str) -> IngestJob:
        return map_job(self.store_root, job_id)

    def get_result(self, job_id: str, *, content: ReturnContentMode = "preview") -> IngestResult:
        result = map_result(self.store_root, job_id, content=content)
        if result.status == "running":
            self._raise_unrecorded_future_error(job_id)
            result = map_result(self.store_root, job_id, content=content)
        return result

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
                sleep_for = min(poll_interval, max(timeout - elapsed, 0))
            else:
                sleep_for = poll_interval
            time.sleep(sleep_for)

    def file(
        self,
        path: Path | str,
        *,
        wait: bool = True,
        content: ReturnContentMode = "preview",
    ) -> IngestResult | IngestJob:
        job = self.submit_file(path)
        if not wait:
            return job
        return self.wait(job.job_id, content=content)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> LocalIngestClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown()

    def _make_runner(self) -> IngestRunner:
        return IngestRunner(store_root=self.store_root, config=self.config)

    def _run_prepared_file(self, prepared: PreparedIngestJob):
        return self._make_runner().run_prepared_job(prepared)

    def _raise_unrecorded_future_error(self, job_id: str) -> None:
        with self._lock:
            future = self._futures.get(job_id)
        if future is None or not future.done():
            return
        try:
            future.result()
        except Exception as error:
            latest = map_result(self.store_root, job_id, content="none")
            if latest.status == "failed":
                return
            raise IngestSdkError(f"Background ingest failed before job state was finalized: {error}") from error
