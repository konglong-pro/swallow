from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence, cast

from swallow.core.config import load_config
from swallow.core.time import now_iso
from swallow.sdk.cli import CliIngestClient
from swallow.sdk.errors import (
    IngestFacadeRegistryError,
    IngestSdkConfigError,
    IngestSdkJobNotFound,
    IngestUnsupportedCapability,
)
from swallow.sdk.http import HttpIngestClient
from swallow.sdk.local import LocalIngestClient
from swallow.sdk.models import (
    BackendCapabilities,
    IngestBackend,
    IngestBackendSelection,
    IngestBatch,
    IngestBatchResult,
    IngestJob,
    IngestResult,
    ReturnContentMode,
    StorageMode,
)
from swallow.sdk.queue import QueueIngestClient
from swallow.sdk.security import default_allowed_roots, resolve_from_cwd


_CONCRETE_BACKENDS: set[str] = {"local", "cli", "http", "queue"}
_BACKEND_SELECTIONS: set[str] = {"auto", *_CONCRETE_BACKENDS}
_REGISTRY_KINDS = {"job", "batch"}

_CAPABILITIES: dict[IngestBackend, BackendCapabilities] = {
    "local": BackendCapabilities(
        backend="local",
        inputs=["file"],
    ),
    "cli": BackendCapabilities(
        backend="cli",
        inputs=["file", "url", "browser_capture", "archive"],
    ),
    "http": BackendCapabilities(
        backend="http",
        inputs=["file", "url", "browser_capture", "archive"],
        requires_service=True,
    ),
    "queue": BackendCapabilities(
        backend="queue",
        inputs=["file", "url", "browser_capture", "archive"],
        supports_batch=True,
        supports_cancel=True,
        supports_stats=True,
        requires_worker=True,
    ),
}


class IngestClient:
    """Unified facade over the Python SDK backends."""

    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        backend: IngestBackendSelection = "auto",
        config_path: Path | str | None = None,
        storage_mode: StorageMode = "persistent",
        prefer: IngestBackend | None = None,
        cwd: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        local: Mapping[str, Any] | None = None,
        cli: Mapping[str, Any] | None = None,
        http: Mapping[str, Any] | None = None,
        queue: Mapping[str, Any] | None = None,
    ) -> None:
        if backend not in _BACKEND_SELECTIONS:
            raise IngestSdkConfigError(f"Unsupported backend: {backend}")
        if prefer is not None and prefer not in _CONCRETE_BACKENDS:
            raise IngestSdkConfigError(f"Unsupported preferred backend: {prefer}")
        if storage_mode != "persistent":
            raise IngestSdkConfigError(f"Unsupported storage mode for IngestClient: {storage_mode}")

        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.store_root = resolve_from_cwd(store_root, self.cwd)
        self.config_path = resolve_from_cwd(config_path, self.cwd) if config_path is not None else None
        roots = allowed_roots if allowed_roots is not None else default_allowed_roots(self.cwd, self.store_root)
        self.allowed_roots = [resolve_from_cwd(root, self.cwd) for root in roots]
        self.backend = cast(IngestBackendSelection, backend)
        self.prefer = prefer
        self.storage_mode = storage_mode
        self._local_config = dict(local or {})
        self._cli_config = dict(cli or {})
        self._http_config = dict(http or {})
        self._queue_config = dict(queue or {})
        self._clients: dict[IngestBackend, Any] = {}
        self._registry = _FacadeRegistry(self.store_root)

    def capabilities(self) -> list[BackendCapabilities]:
        if self.backend == "auto":
            return [capability.model_copy(deep=True) for capability in _CAPABILITIES.values()]
        return [_CAPABILITIES[cast(IngestBackend, self.backend)].model_copy(deep=True)]

    def submit_file(self, path: Path | str) -> IngestJob:
        backend = self._select_backend("file")
        job = self._client(backend).submit_file(path)
        self._registry.record("job", job.job_id, backend)
        return job

    def submit_url(self, url: str) -> IngestJob:
        backend = self._select_backend("url")
        job = self._client(backend).submit_url(url)
        self._registry.record("job", job.job_id, backend)
        return job

    def submit_browser_capture(self, path: Path | str) -> IngestJob:
        backend = self._select_backend("browser_capture")
        job = self._client(backend).submit_browser_capture(path)
        self._registry.record("job", job.job_id, backend)
        return job

    def submit_archive(self, path: Path | str) -> IngestJob:
        backend = self._select_backend("archive")
        job = self._client(backend).submit_archive(path)
        self._registry.record("job", job.job_id, backend)
        return job

    def get_job(self, job_id: str) -> IngestJob:
        backend = self._backend_for_job(job_id)
        return self._client(backend).get_job(job_id)

    def get_result(self, job_id: str, *, content: ReturnContentMode = "preview") -> IngestResult:
        backend = self._backend_for_job(job_id)
        return self._client(backend).get_result(job_id, content=content)

    def wait(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.1,
        content: ReturnContentMode = "preview",
    ) -> IngestResult:
        backend = self._backend_for_job(job_id)
        return self._client(backend).wait(job_id, timeout=timeout, poll_interval=poll_interval, content=content)

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

    def submit_batch(self, patterns: Sequence[str], *, workers: int = 1) -> IngestBatch:
        backend = self._select_backend("batch")
        batch = self._client(backend).submit_batch(patterns, workers=workers)
        self._registry.record("batch", batch.batch_id, backend)
        return batch

    def get_batch(self, batch_id: str) -> IngestBatch:
        backend = self._backend_for_batch(batch_id)
        self._require_backend_support(backend, "batch")
        return self._client(backend).get_batch(batch_id)

    def get_batch_result(self, batch_id: str) -> IngestBatchResult:
        backend = self._backend_for_batch(batch_id)
        self._require_backend_support(backend, "batch")
        return self._client(backend).get_batch_result(batch_id)

    def wait_batch(
        self,
        batch_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.2,
    ) -> IngestBatchResult:
        backend = self._backend_for_batch(batch_id)
        self._require_backend_support(backend, "batch")
        return self._client(backend).wait_batch(batch_id, timeout=timeout, poll_interval=poll_interval)

    def cancel_job(self, job_id: str) -> IngestJob:
        backend = self._backend_for_job(job_id)
        self._require_backend_support(backend, "cancel")
        return self._client(backend).cancel_job(job_id)

    def cancel_batch(self, batch_id: str) -> IngestBatch:
        backend = self._backend_for_batch(batch_id)
        self._require_backend_support(backend, "cancel")
        return self._client(backend).cancel_batch(batch_id)

    def stats(self) -> dict[str, Any]:
        backend = self._select_backend("stats")
        return self._client(backend).stats()

    def shutdown(self, *, wait: bool = True) -> None:
        for client in list(self._clients.values()):
            shutdown = getattr(client, "shutdown", None)
            if shutdown is None:
                continue
            try:
                shutdown(wait=wait)
            except TypeError:
                shutdown()

    def __enter__(self) -> IngestClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown()

    def _select_backend(self, operation: str) -> IngestBackend:
        if self.backend != "auto":
            return self._require_backend_support(cast(IngestBackend, self.backend), operation)
        if self.prefer is not None:
            return self._require_backend_support(self.prefer, operation)
        if operation == "file":
            return "local"
        if operation in {"url", "browser_capture", "archive"}:
            return "cli"
        if operation in {"batch", "stats"}:
            return "queue"
        raise IngestUnsupportedCapability(f"No backend supports operation: {operation}")

    def _backend_for_job(self, job_id: str) -> IngestBackend:
        registered = self._registry.lookup("job", job_id)
        if registered is not None:
            return registered
        if self.backend != "auto":
            return cast(IngestBackend, self.backend)
        raise IngestSdkJobNotFound(f"Job not found in facade registry: {job_id}")

    def _backend_for_batch(self, batch_id: str) -> IngestBackend:
        registered = self._registry.lookup("batch", batch_id)
        if registered is not None:
            return registered
        if self.backend != "auto":
            return self._require_backend_support(cast(IngestBackend, self.backend), "batch")
        raise IngestSdkJobNotFound(f"Batch not found in facade registry: {batch_id}")

    def _require_backend_support(self, backend: IngestBackend, operation: str) -> IngestBackend:
        capability = _CAPABILITIES[backend]
        if operation in capability.inputs:
            return backend
        if operation == "batch" and capability.supports_batch:
            return backend
        if operation == "cancel" and capability.supports_cancel:
            return backend
        if operation == "stats" and capability.supports_stats:
            return backend
        raise IngestUnsupportedCapability(f"Backend {backend!r} does not support operation {operation!r}")

    def _client(self, backend: IngestBackend) -> Any:
        if backend not in self._clients:
            self._clients[backend] = self._create_client(backend)
        return self._clients[backend]

    def _create_client(self, backend: IngestBackend) -> Any:
        try:
            if backend == "local":
                return self._create_local_client()
            if backend == "cli":
                return self._create_cli_client()
            if backend == "http":
                return self._create_http_client()
            if backend == "queue":
                return self._create_queue_client()
        except TypeError as error:
            raise IngestSdkConfigError(f"Invalid {backend} backend configuration: {error}") from error
        raise IngestSdkConfigError(f"Unsupported backend: {backend}")

    def _create_local_client(self) -> LocalIngestClient:
        kwargs = dict(self._local_config)
        kwargs.setdefault("storage_mode", self.storage_mode)
        kwargs.setdefault("cwd", self.cwd)
        kwargs.setdefault("allowed_roots", self.allowed_roots)
        if "config" not in kwargs and self.config_path is not None:
            try:
                kwargs["config"] = load_config(self.config_path)
            except (OSError, ValueError) as error:
                raise IngestSdkConfigError(f"Failed to load local ingest config: {error}") from error
        return LocalIngestClient(store_root=self.store_root, **kwargs)

    def _create_cli_client(self) -> CliIngestClient:
        kwargs = dict(self._cli_config)
        kwargs.setdefault("config_path", self.config_path)
        kwargs.setdefault("cwd", self.cwd)
        kwargs.setdefault("allowed_roots", self.allowed_roots)
        return CliIngestClient(store_root=self.store_root, **kwargs)

    def _create_http_client(self) -> HttpIngestClient:
        kwargs = dict(self._http_config)
        start_local = bool(kwargs.pop("start_local", False))
        if start_local:
            kwargs.setdefault("store_root", self.store_root)
            kwargs.setdefault("config_path", self.config_path)
            kwargs.setdefault("cwd", self.cwd)
            return HttpIngestClient.start_local(**kwargs)
        kwargs.setdefault("cwd", self.cwd)
        kwargs.setdefault("allowed_roots", self.allowed_roots)
        return HttpIngestClient(**kwargs)

    def _create_queue_client(self) -> QueueIngestClient:
        kwargs = dict(self._queue_config)
        kwargs.setdefault("storage_mode", self.storage_mode)
        kwargs.setdefault("cwd", self.cwd)
        kwargs.setdefault("allowed_roots", self.allowed_roots)
        return QueueIngestClient(store_root=self.store_root, **kwargs)


def create_ingest_client(*args: Any, **kwargs: Any) -> IngestClient:
    return IngestClient(*args, **kwargs)


class _FacadeRegistry:
    def __init__(self, store_root: Path) -> None:
        self.path = store_root / "sdk" / "facade_registry.jsonl"
        self._lock = Lock()

    def record(self, kind: str, item_id: str, backend: IngestBackend) -> None:
        if kind not in _REGISTRY_KINDS:
            raise IngestFacadeRegistryError(f"Unsupported registry kind: {kind}")
        payload = {
            "kind": kind,
            "id": item_id,
            "backend": backend,
            "created_at": now_iso(),
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as error:
            raise IngestFacadeRegistryError(f"Failed to write facade registry: {self.path}") from error

    def lookup(self, kind: str, item_id: str) -> IngestBackend | None:
        backend: IngestBackend | None = None
        for record in self._read_records():
            if record["kind"] == kind and record["id"] == item_id:
                backend = cast(IngestBackend, record["backend"])
        return backend

    def _read_records(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        records: list[dict[str, str]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise IngestFacadeRegistryError(f"Failed to read facade registry: {self.path}") from error

        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise IngestFacadeRegistryError(
                    f"Invalid facade registry JSON at {self.path}:{line_number}"
                ) from error
            if not isinstance(payload, dict):
                raise IngestFacadeRegistryError(f"Facade registry record must be an object at {self.path}:{line_number}")
            kind = payload.get("kind")
            item_id = payload.get("id")
            backend = payload.get("backend")
            if kind not in _REGISTRY_KINDS or not isinstance(item_id, str) or backend not in _CONCRETE_BACKENDS:
                raise IngestFacadeRegistryError(f"Invalid facade registry record at {self.path}:{line_number}")
            records.append({"kind": str(kind), "id": item_id, "backend": str(backend)})
        return records
