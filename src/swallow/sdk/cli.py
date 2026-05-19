from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Sequence

from pydantic import ValidationError

from swallow.sdk.errors import (
    IngestSandboxError,
    IngestSdkConfigError,
    IngestSdkInputError,
    IngestSdkProtocolError,
    IngestWaitTimeout,
)
from swallow.sdk.models import IngestJob, IngestResult, ReturnContentMode
from swallow.sdk.result_mapper import TERMINAL_STATUSES, map_job, map_result
from swallow.sdk.security import is_relative_to, validate_ingest_url


@dataclass
class _CliProcess:
    process: subprocess.Popen[str]
    released: bool = False
    finalized: bool = False


class CliIngestClient:
    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        config_path: Path | str | None = None,
        command: str | Sequence[str] = "swallow",
        cwd: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        max_processes: int = 2,
    ) -> None:
        if max_processes < 1:
            raise IngestSdkConfigError("max_processes must be at least 1")
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.store_root = self._resolve_from_cwd(store_root)
        self.config_path = self._resolve_from_cwd(config_path) if config_path is not None else None
        self.command = [command] if isinstance(command, str) else [str(part) for part in command]
        if not self.command:
            raise IngestSdkConfigError("command must not be empty")
        roots = allowed_roots if allowed_roots is not None else [self.cwd, self.store_root]
        self.allowed_roots = [self._resolve_from_cwd(root) for root in roots]
        self._semaphore = BoundedSemaphore(max_processes)
        self._processes: dict[str, _CliProcess] = {}
        self._lock = Lock()

    def submit_file(self, path: Path | str) -> IngestJob:
        return self._submit(["file", str(self._resolve_allowed_file(path))])

    def submit_url(self, url: str) -> IngestJob:
        return self._submit(["url", validate_ingest_url(url)])

    def submit_browser_capture(self, path: Path | str) -> IngestJob:
        return self._submit(["browser-capture", str(self._resolve_allowed_file(path))])

    def submit_archive(self, path: Path | str) -> IngestJob:
        return self._submit(["archive", str(self._resolve_allowed_file(path))])

    def get_job(self, job_id: str) -> IngestJob:
        return map_job(self.store_root, job_id)

    def get_result(self, job_id: str, *, content: ReturnContentMode = "preview") -> IngestResult:
        result = map_result(self.store_root, job_id, content=content)
        if result.status in TERMINAL_STATUSES:
            self._finalize_process(job_id, expected_status=result.status)
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

            if self._process_exited(job_id):
                self._finalize_process(job_id)
                result = self.get_result(job_id, content=content)
                if result.status in TERMINAL_STATUSES:
                    return result
                raise IngestSdkProtocolError(f"CLI process exited before job reached a terminal state: {job_id}")

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

    def url(
        self,
        url: str,
        *,
        wait: bool = True,
        content: ReturnContentMode = "preview",
    ) -> IngestResult | IngestJob:
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

    def archive(
        self,
        path: Path | str,
        *,
        wait: bool = True,
        content: ReturnContentMode = "preview",
    ) -> IngestResult | IngestJob:
        job = self.submit_archive(path)
        if not wait:
            return job
        return self.wait(job.job_id, content=content)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            records = list(self._processes.items())
        for job_id, record in records:
            if wait:
                self._finalize_process(job_id)
                continue
            if record.process.poll() is None:
                record.process.terminate()
            self._release_process(record)

    def __enter__(self) -> CliIngestClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown()

    def _submit(self, command_args: list[str]) -> IngestJob:
        if not self._semaphore.acquire(blocking=False):
            raise IngestSdkConfigError("Maximum running CLI processes reached")

        args = self._command_args(command_args)
        try:
            process = subprocess.Popen(
                args,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as error:
            self._semaphore.release()
            raise IngestSdkConfigError(f"CLI command not found: {self.command[0]}") from error

        line = process.stdout.readline() if process.stdout is not None else ""
        if not line:
            stderr = self._communicate_after_startup_failure(process)
            self._semaphore.release()
            if "Config failed:" in stderr:
                raise IngestSdkConfigError(stderr.strip())
            raise IngestSdkProtocolError(f"CLI did not emit a job_submitted event. stderr: {stderr.strip()}")

        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            stderr = self._communicate_after_startup_failure(process)
            self._semaphore.release()
            raise IngestSdkProtocolError(f"Invalid CLI JSONL event: {line.strip()}; stderr: {stderr.strip()}") from error

        if not isinstance(event, dict) or event.get("event") != "job_submitted" or not isinstance(event.get("job"), dict):
            stderr = self._communicate_after_startup_failure(process)
            self._semaphore.release()
            raise IngestSdkProtocolError(f"Expected job_submitted event, got: {line.strip()}; stderr: {stderr.strip()}")

        try:
            job = IngestJob.model_validate(event["job"])
        except ValidationError as error:
            stderr = self._communicate_after_startup_failure(process)
            self._semaphore.release()
            raise IngestSdkProtocolError(f"Invalid job_submitted payload: {line.strip()}; stderr: {stderr.strip()}") from error

        with self._lock:
            self._processes[job.job_id] = _CliProcess(process=process)
        return job

    def _command_args(self, command_args: list[str]) -> list[str]:
        args = [
            *self.command,
            "--store",
            str(self.store_root),
        ]
        if self.config_path is not None:
            args.extend(["--config", str(self.config_path)])
        return [*args, *command_args, "--jsonl"]

    def _resolve_from_cwd(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.cwd / candidate
        return candidate.resolve(strict=False)

    def _resolve_allowed_file(self, path: Path | str) -> Path:
        candidate = self._resolve_from_cwd(path)
        if not candidate.is_file():
            raise IngestSdkInputError(f"Input file does not exist: {candidate}")
        if not any(is_relative_to(candidate, root) for root in self.allowed_roots):
            roots = ", ".join(str(root) for root in self.allowed_roots)
            raise IngestSandboxError(f"Input path is outside allowed roots: {candidate}; allowed roots: {roots}")
        reject_sensitive_input(candidate)
        return candidate

    def _process_exited(self, job_id: str) -> bool:
        record = self._process_record(job_id)
        return record is not None and not record.finalized and record.process.poll() is not None

    def _process_record(self, job_id: str) -> _CliProcess | None:
        with self._lock:
            return self._processes.get(job_id)

    def _finalize_process(self, job_id: str, *, expected_status: str | None = None) -> None:
        record = self._process_record(job_id)
        if record is None or record.finalized:
            return

        try:
            stdout, stderr = record.process.communicate(timeout=5)
        except subprocess.TimeoutExpired as error:
            record.process.kill()
            stdout, stderr = record.process.communicate()
            self._release_process(record)
            record.finalized = True
            raise IngestSdkProtocolError(f"CLI process did not exit after job reached terminal state: {job_id}") from error

        final_result = self._read_final_result(stdout, stderr)
        final_status = final_result.get("status")
        if expected_status is not None and final_status != expected_status:
            self._release_process(record)
            record.finalized = True
            raise IngestSdkProtocolError(
                f"CLI final status mismatch for {job_id}: event={final_status}, persisted={expected_status}; stderr: {stderr.strip()}"
            )
        expected_codes = {"success": 0, "partial": 2, "failed": 1}
        expected_code = expected_codes.get(str(final_status))
        if expected_code is None or record.process.returncode != expected_code:
            self._release_process(record)
            record.finalized = True
            raise IngestSdkProtocolError(
                f"Unexpected CLI exit code for {job_id}: {record.process.returncode}; status={final_status}; stderr: {stderr.strip()}"
            )

        self._release_process(record)
        record.finalized = True

    def _read_final_result(self, stdout: str, stderr: str) -> dict[str, Any]:
        for line in reversed(stdout.splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise IngestSdkProtocolError(f"Invalid CLI JSONL event: {line}; stderr: {stderr.strip()}") from error
            if isinstance(event, dict) and isinstance(event.get("result"), dict):
                return event["result"]
        raise IngestSdkProtocolError(f"CLI did not emit a terminal result event. stderr: {stderr.strip()}")

    def _release_process(self, record: _CliProcess) -> None:
        if record.released:
            return
        self._semaphore.release()
        record.released = True

    def _communicate_after_startup_failure(self, process: subprocess.Popen[str]) -> str:
        try:
            _stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate()
        return stderr or ""

def reject_sensitive_input(path: Path) -> None:
    from swallow.sdk.security import reject_sensitive_path

    try:
        reject_sensitive_path(path)
    except IngestSandboxError:
        raise
