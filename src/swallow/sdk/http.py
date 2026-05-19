from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from swallow.sdk.errors import (
    IngestSdkConfigError,
    IngestSdkError,
    IngestSdkInputError,
    IngestSdkJobNotFound,
    IngestSdkProtocolError,
    IngestTransportError,
    IngestWaitTimeout,
)
from swallow.sdk.models import IngestJob, IngestResult, ReturnContentMode
from swallow.sdk.result_mapper import TERMINAL_STATUSES
from swallow.sdk.security import resolve_allowed_file, resolve_from_cwd, validate_ingest_url


class HttpIngestClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: Any | None = None,
        timeout: float = 30.0,
        process: subprocess.Popen[str] | None = None,
        cwd: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
    ) -> None:
        if client is None and not base_url:
            raise IngestSdkConfigError("base_url is required when no HTTP client is provided")
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._process = process
        self._owns_client = client is None
        self._client = client or self._build_httpx_client()
        self.cwd = Path(cwd or Path.cwd()).resolve()
        roots = allowed_roots if allowed_roots is not None else [self.cwd]
        self.allowed_roots = [resolve_from_cwd(root, self.cwd) for root in roots]

    @classmethod
    def start_local(
        cls,
        *,
        store_root: Path | str,
        config_path: Path | str | None = None,
        host: str = "127.0.0.1",
        port: int | None = None,
        cwd: Path | str | None = None,
        timeout: float = 30.0,
    ) -> HttpIngestClient:
        selected_port = port or find_free_port(host)
        base_url = f"http://{host}:{selected_port}"
        env = os.environ.copy()
        env["SWALLOW_STORE"] = str(Path(store_root).resolve())
        if config_path is not None:
            env["SWALLOW_CONFIG"] = str(Path(config_path).resolve())

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "swallow.service.api:app",
                "--host",
                host,
                "--port",
                str(selected_port),
                "--log-level",
                "warning",
            ],
            cwd=str(Path(cwd or Path.cwd()).resolve()),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        allowed_roots = [Path(cwd or Path.cwd()).resolve(), Path(store_root).resolve()]
        client = cls(base_url, timeout=timeout, process=process, cwd=cwd, allowed_roots=allowed_roots)
        try:
            client._wait_for_health(timeout=timeout)
        except Exception:
            client.shutdown()
            raise
        return client

    def health(self) -> dict[str, Any]:
        response = self._request("get", "/health")
        payload = self._read_json(response)
        if not isinstance(payload, dict):
            raise IngestSdkProtocolError("Health response must be a JSON object")
        return payload

    def submit_file(self, path: Path | str) -> IngestJob:
        file_path = self._resolve_file(path)
        with file_path.open("rb") as file:
            response = self._request("post", "/v1/ingest/file", files={"file": (file_path.name, file)})
        return self._parse_job_response(response)

    def submit_url(self, url: str) -> IngestJob:
        response = self._request("post", "/v1/ingest/url", json={"url": validate_ingest_url(url)})
        return self._parse_job_response(response)

    def submit_browser_capture(self, path: Path | str) -> IngestJob:
        file_path = self._resolve_file(path)
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise IngestSdkInputError(f"Browser capture JSON is invalid: {file_path}") from error
        if not isinstance(payload, dict):
            raise IngestSdkInputError(f"Browser capture JSON must be an object: {file_path}")
        response = self._request("post", "/v1/ingest/browser-capture", json=payload)
        return self._parse_job_response(response)

    def submit_archive(self, path: Path | str) -> IngestJob:
        file_path = self._resolve_file(path)
        with file_path.open("rb") as file:
            response = self._request("post", "/v1/ingest/archive", files={"file": (file_path.name, file)})
        return self._parse_job_response(response)

    def get_job(self, job_id: str) -> IngestJob:
        response = self._request("get", f"/v1/jobs/{job_id}")
        return self._parse_job_response(response)

    def get_result(self, job_id: str, *, content: ReturnContentMode = "preview") -> IngestResult:
        if content not in {"none", "preview", "full"}:
            raise IngestSdkConfigError(f"Unsupported content mode: {content}")
        response = self._request("get", f"/v1/jobs/{job_id}/result", params={"content": content})
        return self._parse_result_response(response)

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

    def shutdown(self) -> None:
        close = getattr(self._client, "close", None)
        if self._owns_client and close is not None:
            close()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

    def __enter__(self) -> HttpIngestClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown()

    def _build_httpx_client(self) -> Any:
        try:
            import httpx
        except ImportError as error:
            raise IngestSdkConfigError("HttpIngestClient requires httpx; install the service extra.") from error
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = getattr(self._client, method)(path, **kwargs)
        except Exception as error:
            raise IngestTransportError(f"HTTP request failed: {error}") from error
        if response.status_code >= 400:
            self._raise_for_error_response(response)
        return response

    def _raise_for_error_response(self, response: Any) -> None:
        detail = self._read_error_detail(response)
        message = detail.get("message") or getattr(response, "text", "") or f"HTTP {response.status_code}"
        if response.status_code == 404:
            raise IngestSdkJobNotFound(str(message))
        if response.status_code == 400:
            raise IngestSdkInputError(str(message))
        raise IngestTransportError(str(message))

    def _read_error_detail(self, response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            return {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return detail if isinstance(detail, dict) else {}

    def _read_json(self, response: Any) -> Any:
        try:
            return response.json()
        except Exception as error:
            raise IngestSdkProtocolError("HTTP response was not valid JSON") from error

    def _parse_job_response(self, response: Any) -> IngestJob:
        payload = self._read_json(response)
        try:
            return IngestJob.model_validate(payload)
        except ValidationError as error:
            raise IngestSdkProtocolError(f"HTTP response did not match IngestJob: {payload}") from error

    def _parse_result_response(self, response: Any) -> IngestResult:
        payload = self._read_json(response)
        try:
            return IngestResult.model_validate(payload)
        except ValidationError as error:
            raise IngestSdkProtocolError(f"HTTP response did not match IngestResult: {payload}") from error

    def _wait_for_health(self, *, timeout: float) -> None:
        started = time.monotonic()
        while True:
            if self._process is not None and self._process.poll() is not None:
                _stdout, stderr = self._process.communicate()
                raise IngestSdkError(f"Local service exited before health check passed: {stderr.strip()}")
            try:
                if self.health().get("status") == "ok":
                    return
            except IngestSdkError:
                pass

            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                raise IngestWaitTimeout(f"Timed out waiting for local service health after {timeout:g}s")
            time.sleep(0.1)

    def _resolve_file(self, path: Path | str) -> Path:
        try:
            return resolve_allowed_file(path, cwd=self.cwd, allowed_roots=self.allowed_roots)
        except FileNotFoundError as error:
            raise IngestSdkInputError(str(error)) from error


def resolve_existing_file(path: Path | str) -> Path:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise IngestSdkInputError(f"Input file does not exist: {file_path}")
    return file_path


def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
