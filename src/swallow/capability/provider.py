from __future__ import annotations

import asyncio
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence

from swallow.capability.adapters import create_cli_client, create_http_client, create_local_client, create_queue_client
from swallow.capability.artifacts import (
    build_artifact_view,
    descriptor_for_batch_artifact,
    descriptor_for_job_artifact,
    descriptor_for_raw_artifact,
    resolve_artifact_ref,
)
from swallow.capability.errors import (
    CapabilityArtifactError,
    CapabilityConfigError,
    CapabilityInputError,
    CapabilityJobNotFound,
    CapabilityPolicyError,
    CapabilityProviderError,
    CapabilityRegistryError,
)
from swallow.capability.manifest import (
    PROVIDER_ID,
    ProviderProfile,
    build_provider_manifest,
    capability_availability,
    capability_for_source,
    load_profile,
    profile_policy_defaults,
    required_policy_for_source,
    risk_for_source,
    source_type_for_capability,
)
from swallow.capability.schemas import (
    ArtifactRef,
    ArtifactView,
    ArtifactViewOptions,
    BatchTraceRef,
    BatchWaitOptions,
    CapabilityBatch,
    CapabilityBatchJob,
    CapabilityBatchResult,
    CapabilityError,
    CapabilityJob,
    CapabilityPlan,
    CapabilityRequest,
    CapabilityResult,
    CapabilityWarning,
    CancelResult,
    DoctorCheck,
    DoctorInput,
    DoctorResult,
    OperationSummary,
    ProviderManifest,
    ProviderPolicy,
    ResultOptions,
    TraceRef,
    WaitOptions,
    CapabilityOutput,
    CapabilityOutputs,
    CapabilityProvenance,
)
from swallow.core import doctor as doctor_module
from swallow.core.job_store import JobStore
from swallow.core.time import now_iso
from swallow.sdk.errors import IngestSdkError, IngestSdkInputError, IngestUnsupportedCapability
from swallow.sdk.models import IngestBatch, IngestBatchResult, IngestJob, IngestResult
from swallow.sdk.result_mapper import TERMINAL_STATUSES
from swallow.sdk.security import default_allowed_roots, resolve_allowed_file, resolve_from_cwd, validate_ingest_url


class SwallowCapabilityProvider:
    provider_id = PROVIDER_ID

    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        profile: str = "local_isolated",
        profile_overrides: Mapping[str, Any] | None = None,
        policy: ProviderPolicy | Mapping[str, Any] | None = None,
        cwd: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        config_path: Path | str | None = None,
        command: str | Sequence[str] = "swallow",
        service_url: str | None = None,
        max_processes: int = 2,
    ) -> None:
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.store_root = resolve_from_cwd(store_root, self.cwd)
        self.config_path = resolve_from_cwd(config_path, self.cwd) if config_path is not None else None
        roots = allowed_roots if allowed_roots is not None else default_allowed_roots(self.cwd, self.store_root)
        self.allowed_roots = [resolve_from_cwd(root, self.cwd) for root in roots]
        self.profile = load_profile(profile, profile_overrides)
        self.policy = self._build_policy(policy)
        self.provider_version = read_provider_version()
        self.command = command
        self.service_url = service_url
        self.max_processes = max_processes
        self._clients: dict[str, Any] = {}
        self._registry = _ProviderRegistry(self.store_root)

    def discover(self) -> ProviderManifest:
        return build_provider_manifest(
            provider_version=self.provider_version,
            profile=self.profile,
            policy=self.policy,
        )

    async def doctor(self, input: DoctorInput | Mapping[str, Any] | None = None) -> DoctorResult:
        doctor_input = DoctorInput.model_validate(input or {})
        checks = [
            DoctorCheck(
                name="provider_profile",
                status="ok",
                detail=f"using {self.profile.name}",
            ),
            DoctorCheck(
                name="provider_default_backend",
                status="ok",
                detail=f"backend order: {', '.join(self.profile.backend_order)}",
            ),
        ]
        if self.profile.requires_service_url and not self.service_url:
            checks.append(
                DoctorCheck(
                    name="service_url",
                    status="missing",
                    detail="service profile requires service_url",
                )
            )
        report = await asyncio.to_thread(doctor_module.run_doctor, None, deep=doctor_input.deep)
        checks.extend(
            DoctorCheck(
                name=f"swallow_{check.name}",
                status=check.status,
                detail=check.detail,
                install_hint=check.install_hint,
            )
            for check in report.checks
        )
        status = "missing" if any(check.status == "missing" for check in checks) else "warning" if any(
            check.status == "warning" for check in checks
        ) else "ok"
        return DoctorResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            profile=self.profile.name,
            status=status,  # type: ignore[arg-type]
            checks=checks,
        )

    async def plan(self, request: CapabilityRequest | Mapping[str, Any]) -> CapabilityPlan:
        capability_request = CapabilityRequest.model_validate(request)
        source_type = source_type_for_capability(capability_request.capability_id)
        required = required_policy_for_source(source_type, self.profile)
        available, unavailable_reason = capability_availability(source_type, self.profile, self.policy, required)
        backend_candidates = [backend for backend in self.profile.backend_order if backend_supports(backend, source_type)]
        risk = risk_for_source(source_type)

        if available:
            await asyncio.to_thread(self._validate_request_input, capability_request)
            status = "ready"
            message = "Request is ready to submit."
        else:
            missing_policy = [permission for permission in required if not bool(getattr(self.policy, permission))]
            status = "requires_permission" if missing_policy else "unavailable"
            message = unavailable_reason or "Capability is unavailable under the current profile."

        manifest = self.discover()
        capability_manifest = next(item for item in manifest.capabilities if item.capability_id == capability_request.capability_id)
        return CapabilityPlan(
            provider_id=self.provider_id,
            capability_id=capability_request.capability_id,
            status=status,  # type: ignore[arg-type]
            profile=self.profile.name,
            backend_candidates=backend_candidates,  # type: ignore[arg-type]
            required_permissions=required,
            required_approvals=required,
            side_effects=capability_manifest.side_effects,
            artifact_kinds=capability_manifest.artifact_kinds,
            risk=risk,
            message=message,
        )

    async def submit(self, request: CapabilityRequest | Mapping[str, Any]) -> CapabilityJob:
        capability_request = CapabilityRequest.model_validate(request)
        source_type = source_type_for_capability(capability_request.capability_id)
        if source_type == "batch":
            raise CapabilityConfigError("Use submit_batch for swallow.ingest.batch requests.")
        plan = await self.plan(capability_request)
        if plan.status != "ready":
            raise CapabilityPolicyError(plan.message)
        backend = self._select_backend(source_type)
        client = self._client(backend)
        job = await asyncio.to_thread(self._submit_with_client, client, capability_request)
        record = self._registry.record(
            job_id=job.job_id,
            capability_id=capability_request.capability_id,
            profile=self.profile.name,
            backend=backend,
            provider_version=self.provider_version,
            store_root=self.store_root,
        )
        return capability_job_from_sdk(self.provider_id, job, record)

    async def submit_batch(self, request: CapabilityRequest | Mapping[str, Any]) -> CapabilityBatch:
        capability_request = CapabilityRequest.model_validate(request)
        if capability_request.capability_id != "swallow.ingest.batch":
            raise CapabilityInputError("submit_batch requires capability_id swallow.ingest.batch")
        plan = await self.plan(capability_request)
        if plan.status != "ready":
            raise CapabilityPolicyError(plan.message)
        backend = self._select_backend("batch")
        client = self._client(backend)
        batch = await asyncio.to_thread(self._submit_batch_with_client, client, capability_request)
        record = self._registry.record_batch(
            batch_id=batch.batch_id,
            profile=self.profile.name,
            backend=backend,
            provider_version=self.provider_version,
            store_root=self.store_root,
        )
        return capability_batch_from_sdk(self.provider_id, batch, record)

    async def get_job(self, job_id: str) -> CapabilityJob:
        record = self._registry.lookup(job_id)
        client = self._client(record["backend"])
        job = await asyncio.to_thread(client.get_job, job_id)
        return capability_job_from_sdk(self.provider_id, job, record)

    async def get_batch(self, batch_id: str) -> CapabilityBatch:
        record = self._registry.lookup_batch(batch_id)
        client = self._client(record["backend"])
        batch = await asyncio.to_thread(client.get_batch, batch_id)
        return capability_batch_from_sdk(self.provider_id, batch, record)

    async def wait(self, job_id: str, options: WaitOptions | Mapping[str, Any] | None = None) -> CapabilityResult:
        wait_options = WaitOptions.model_validate(options or {})
        record = self._registry.lookup(job_id)
        client = self._client(record["backend"])
        result = await asyncio.to_thread(
            client.wait,
            job_id,
            timeout=wait_options.timeout,
            poll_interval=wait_options.poll_interval,
            content=wait_options.content_mode,
        )
        return self._capability_result(result, record, content_mode=wait_options.content_mode)

    async def get_result(self, job_id: str, options: ResultOptions | Mapping[str, Any] | None = None) -> CapabilityResult:
        result_options = ResultOptions.model_validate(options or {})
        record = self._registry.lookup(job_id)
        client = self._client(record["backend"])
        result = await asyncio.to_thread(client.get_result, job_id, content=result_options.content_mode)
        return self._capability_result(result, record, content_mode=result_options.content_mode)

    async def wait_batch(
        self,
        batch_id: str,
        options: BatchWaitOptions | Mapping[str, Any] | None = None,
    ) -> CapabilityBatchResult:
        wait_options = BatchWaitOptions.model_validate(options or {})
        record = self._registry.lookup_batch(batch_id)
        client = self._client(record["backend"])
        result = await asyncio.to_thread(
            client.wait_batch,
            batch_id,
            timeout=wait_options.timeout,
            poll_interval=wait_options.poll_interval,
        )
        return capability_batch_result_from_sdk(self.provider_id, result, record)

    async def get_batch_result(self, batch_id: str) -> CapabilityBatchResult:
        record = self._registry.lookup_batch(batch_id)
        client = self._client(record["backend"])
        result = await asyncio.to_thread(client.get_batch_result, batch_id)
        return capability_batch_result_from_sdk(self.provider_id, result, record)

    async def get_artifact(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        options: ArtifactViewOptions | Mapping[str, Any] | None = None,
    ) -> ArtifactView:
        artifact_ref = ref if isinstance(ref, str) else ArtifactRef.model_validate(ref).artifact_ref
        view_options = ArtifactViewOptions.model_validate(options or {})
        resolved = resolve_artifact_ref(self.store_root, artifact_ref)
        return await asyncio.to_thread(build_artifact_view, resolved, options=view_options, policy=self.policy)

    async def cancel(self, job_id: str) -> CancelResult:
        record = self._registry.lookup(job_id)
        client = self._client(record["backend"])
        if record["backend"] == "queue":
            try:
                job = await asyncio.to_thread(client.cancel_job, job_id)
            except IngestSdkError as error:
                return CancelResult(provider_id=self.provider_id, job_id=job_id, status="failed", reason=str(error))
            queue_status = read_queue_status(client, job_id)
            return CancelResult(
                provider_id=self.provider_id,
                job_id=job_id,
                status="canceled" if queue_status == "canceled" else "cancel_requested",
                job_status=job.status,  # type: ignore[arg-type]
                reason="Queue backend accepted cancellation.",
            )

        job = await asyncio.to_thread(client.get_job, job_id)
        if job.status in TERMINAL_STATUSES:
            return CancelResult(
                provider_id=self.provider_id,
                job_id=job_id,
                status="already_terminal",
                job_status=job.status,  # type: ignore[arg-type]
                reason="Job is already terminal.",
            )
        return CancelResult(
            provider_id=self.provider_id,
            job_id=job_id,
            status="not_cancelable",
            job_status=job.status,  # type: ignore[arg-type]
            reason=f"Backend {record['backend']} does not support provider cancellation.",
        )

    async def cancel_batch(self, batch_id: str) -> CancelResult:
        record = self._registry.lookup_batch(batch_id)
        client = self._client(record["backend"])
        try:
            batch = await asyncio.to_thread(client.cancel_batch, batch_id)
        except IngestSdkError as error:
            return CancelResult(provider_id=self.provider_id, batch_id=batch_id, status="failed", reason=str(error))
        if batch.status == "canceled":
            status = "canceled"
            reason = "Queue backend canceled the batch."
        elif batch.status in {"success", "partial", "failed"}:
            status = "already_terminal"
            reason = "Batch is already terminal."
        else:
            status = "cancel_requested"
            reason = "Queue backend accepted batch cancellation."
        return CancelResult(
            provider_id=self.provider_id,
            batch_id=batch_id,
            status=status,  # type: ignore[arg-type]
            batch_status=batch.status,
            reason=reason,
        )

    def shutdown(self, *, wait: bool = True) -> None:
        for client in list(self._clients.values()):
            shutdown = getattr(client, "shutdown", None)
            if shutdown is None:
                continue
            try:
                shutdown(wait=wait)
            except TypeError:
                shutdown()

    def _build_policy(self, policy: ProviderPolicy | Mapping[str, Any] | None) -> ProviderPolicy:
        defaults = profile_policy_defaults(self.profile)
        if policy is None:
            return defaults
        if isinstance(policy, ProviderPolicy):
            return policy
        unknown = set(policy) - set(ProviderPolicy.model_fields)
        if unknown:
            raise CapabilityConfigError(f"Unsupported provider policy keys: {sorted(unknown)}")
        override = ProviderPolicy.model_validate(policy).model_dump(mode="python")
        explicit_override = {key: override[key] for key in policy}
        return defaults.model_copy(update=explicit_override)

    def _validate_request_input(self, request: CapabilityRequest) -> None:
        source_type = source_type_for_capability(request.capability_id)
        if source_type == "batch":
            patterns = request.input.get("patterns")
            if not isinstance(patterns, list) or not patterns or not all(isinstance(item, str) and item for item in patterns):
                raise CapabilityInputError("Batch ingest requires input.patterns with at least one pattern")
            workers = request.input.get("workers", 1)
            if not isinstance(workers, int) or workers < 1:
                raise CapabilityInputError("Batch ingest workers must be an integer greater than 0")
            return

        if source_type == "url":
            url = request.input.get("url")
            if not isinstance(url, str) or not url:
                raise CapabilityInputError("URL ingest requires input.url")
            try:
                validate_ingest_url(url)
            except IngestSdkInputError as error:
                raise CapabilityInputError(str(error)) from error
            return

        source_path = request.input.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            raise CapabilityInputError(f"{request.capability_id} requires input.source_path")
        try:
            resolve_allowed_file(source_path, cwd=self.cwd, allowed_roots=self.allowed_roots)
        except (FileNotFoundError, IngestSdkError) as error:
            raise CapabilityInputError(str(error)) from error

    def _select_backend(self, source_type: str) -> str:
        for backend in self.profile.backend_order:
            if backend_supports(backend, source_type):
                return backend
        raise CapabilityConfigError(f"No backend in profile {self.profile.name!r} supports {source_type!r}")

    def _client(self, backend: str) -> Any:
        if backend not in self._clients:
            self._clients[backend] = self._create_client(backend)
        return self._clients[backend]

    def _create_client(self, backend: str) -> Any:
        if backend == "local":
            return create_local_client(store_root=self.store_root, cwd=self.cwd, allowed_roots=self.allowed_roots)
        if backend == "cli":
            return create_cli_client(
                store_root=self.store_root,
                cwd=self.cwd,
                allowed_roots=self.allowed_roots,
                config_path=self.config_path,
                command=self.command,
                max_processes=self.max_processes,
            )
        if backend == "http":
            return create_http_client(base_url=self.service_url, cwd=self.cwd, allowed_roots=self.allowed_roots)
        if backend == "queue":
            return create_queue_client(store_root=self.store_root, cwd=self.cwd, allowed_roots=self.allowed_roots)
        raise CapabilityConfigError(f"Unsupported provider backend: {backend}")

    def _submit_with_client(self, client: Any, request: CapabilityRequest) -> IngestJob:
        source_type = source_type_for_capability(request.capability_id)
        try:
            if source_type == "file":
                return client.submit_file(request.input["source_path"])
            if source_type == "url":
                return client.submit_url(request.input["url"])
            if source_type == "browser_capture":
                return client.submit_browser_capture(request.input["source_path"])
            if source_type == "archive":
                return client.submit_archive(request.input["source_path"])
        except IngestUnsupportedCapability as error:
            raise CapabilityConfigError(str(error)) from error
        except IngestSdkError as error:
            raise CapabilityProviderError(str(error)) from error
        raise CapabilityConfigError(f"Unsupported source type: {source_type}")

    def _submit_batch_with_client(self, client: Any, request: CapabilityRequest) -> IngestBatch:
        try:
            return client.submit_batch(request.input["patterns"], workers=request.input.get("workers", 1))
        except IngestUnsupportedCapability as error:
            raise CapabilityConfigError(str(error)) from error
        except IngestSdkError as error:
            raise CapabilityProviderError(str(error)) from error

    def _capability_result(self, result: IngestResult, record: dict[str, str], *, content_mode: str) -> CapabilityResult:
        job_times = read_job_times(self.store_root, result.job_id)
        artifacts = descriptors_from_result(result)
        trace_refs = [
            TraceRef(system="swallow", job_id=result.job_id, artifact_ref=descriptor.artifact_ref)
            for descriptor in artifacts
            if descriptor.kind == "trace"
        ]
        primary = None
        if result.outputs.markdown_path:
            text_payload = result.preview if content_mode == "preview" else result.content if content_mode == "full" else None
            primary = CapabilityOutput(
                artifact_ref=f"swallow://jobs/{result.job_id}/document.md",
                kind="markdown_candidate",
                content_mode=content_mode,  # type: ignore[arg-type]
                preview=text_payload.text if text_payload is not None else None,
                truncated=bool(text_payload.truncated) if text_payload is not None else False,
            )

        return CapabilityResult(
            provider_id=self.provider_id,
            capability_id=record["capability_id"],  # type: ignore[arg-type]
            job_id=result.job_id,
            status=result.status,  # type: ignore[arg-type]
            started_at=job_times.get("created_at"),
            finished_at=job_times.get("finished_at"),
            summary=operation_summary(result),
            outputs=CapabilityOutputs(primary=primary),
            artifacts=artifacts,
            warnings=[
                CapabilityWarning(
                    code=warning.code,
                    message=warning.message,
                    stage=warning.stage,
                    worker=warning.worker,
                    details=warning.details,
                )
                for warning in result.warnings
            ],
            errors=[
                CapabilityError(
                    code=error.code,
                    message=error.message,
                    stage=error.stage,
                    worker=error.worker,
                    retryable=error.retryable,
                    recoverable=error.recoverable,
                    details=error.details,
                )
                for error in result.errors
            ],
            trace_refs=trace_refs,
            provenance=CapabilityProvenance(
                provider_version=self.provider_version,
                profile=record["profile"],
                backend=record["backend"],  # type: ignore[arg-type]
                input_sha256=result.document.sha256 if result.document else None,
                worker_chain=read_worker_chain(self.store_root, result.job_id),
            ),
        )


class _ProviderRegistry:
    def __init__(self, store_root: Path) -> None:
        self.path = store_root / "sdk" / "capability_provider_registry.jsonl"
        self._lock = Lock()

    def record(
        self,
        *,
        job_id: str,
        capability_id: str,
        profile: str,
        backend: str,
        provider_version: str,
        store_root: Path,
    ) -> dict[str, str]:
        payload = {
            "kind": "job",
            "job_id": job_id,
            "provider_id": PROVIDER_ID,
            "provider_version": provider_version,
            "capability_id": capability_id,
            "profile": profile,
            "backend": backend,
            "store_root": str(store_root),
            "created_at": now_iso(),
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as error:
            raise CapabilityRegistryError(f"Failed to write provider registry: {self.path}") from error
        return {key: str(value) for key, value in payload.items()}

    def record_batch(
        self,
        *,
        batch_id: str,
        profile: str,
        backend: str,
        provider_version: str,
        store_root: Path,
    ) -> dict[str, str]:
        payload = {
            "kind": "batch",
            "batch_id": batch_id,
            "provider_id": PROVIDER_ID,
            "provider_version": provider_version,
            "capability_id": "swallow.ingest.batch",
            "profile": profile,
            "backend": backend,
            "store_root": str(store_root),
            "created_at": now_iso(),
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as error:
            raise CapabilityRegistryError(f"Failed to write provider registry: {self.path}") from error
        return {key: str(value) for key, value in payload.items()}

    def lookup(self, job_id: str) -> dict[str, str]:
        return self._lookup("job", "job_id", job_id)

    def lookup_batch(self, batch_id: str) -> dict[str, str]:
        return self._lookup("batch", "batch_id", batch_id)

    def _lookup(self, kind: str, id_key: str, item_id: str) -> dict[str, str]:
        if not self.path.exists():
            raise CapabilityJobNotFound(f"{kind.title()} not found in provider registry: {item_id}")
        found: dict[str, str] | None = None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise CapabilityRegistryError(f"Failed to read provider registry: {self.path}") from error
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise CapabilityRegistryError(f"Invalid provider registry JSON at {self.path}:{line_number}") from error
            if isinstance(payload, dict) and payload.get("kind") == kind and payload.get(id_key) == item_id:
                found = {key: str(value) for key, value in payload.items()}
        if found is None:
            raise CapabilityJobNotFound(f"{kind.title()} not found in provider registry: {item_id}")
        return found


def backend_supports(backend: str, source_type: str) -> bool:
    if backend == "local":
        return source_type == "file"
    if backend in {"cli", "http"}:
        return source_type in {"file", "url", "browser_capture", "archive"}
    if backend == "queue":
        return source_type in {"file", "url", "browser_capture", "archive", "batch"}
    return False


def capability_job_from_sdk(provider_id: str, job: IngestJob, record: dict[str, str]) -> CapabilityJob:
    trace_refs = []
    if job.trace_path:
        trace_refs.append(TraceRef(system="swallow", job_id=job.job_id, artifact_ref=f"swallow://jobs/{job.job_id}/trace.jsonl"))
    return CapabilityJob(
        provider_id=provider_id,
        capability_id=record["capability_id"],  # type: ignore[arg-type]
        job_id=job.job_id,
        status=job.status,  # type: ignore[arg-type]
        created_at=job.created_at,
        profile=record["profile"],
        backend=record["backend"],  # type: ignore[arg-type]
        trace_refs=trace_refs,
    )


def capability_batch_from_sdk(provider_id: str, batch: IngestBatch, record: dict[str, str]) -> CapabilityBatch:
    trace_refs = []
    if batch.trace_path:
        trace_refs.append(
            BatchTraceRef(
                system="swallow",
                batch_id=batch.batch_id,
                artifact_ref=f"swallow://batch_runs/{batch.batch_id}/trace.jsonl",
            )
        )
    return CapabilityBatch(
        provider_id=provider_id,
        batch_id=batch.batch_id,
        status=batch.status,
        total=batch.total,
        queued=batch.queued,
        running=batch.running,
        success=batch.success,
        partial=batch.partial,
        failed=batch.failed,
        canceled=batch.canceled,
        job_ids=batch.job_ids,
        created_at=batch.created_at,
        profile=record["profile"],
        backend=record["backend"],  # type: ignore[arg-type]
        summary_ref=f"swallow://batch_runs/{batch.batch_id}/summary.json",
        trace_refs=trace_refs,
    )


def capability_batch_result_from_sdk(
    provider_id: str,
    batch: IngestBatchResult,
    record: dict[str, str],
) -> CapabilityBatchResult:
    base = capability_batch_from_sdk(provider_id, batch, record)
    return CapabilityBatchResult(
        **base.model_dump(mode="python"),
        summary=batch_operation_summary(batch),
        artifacts=descriptors_from_batch_result(batch),
        jobs=[
            CapabilityBatchJob(
                input=job.input,
                job_id=job.job_id,
                status=job.status,
                attempts=job.attempts,
                document=job.document,
                manifest=job.manifest,
                trace=job.trace,
                error=capability_error_from_sdk(job.error) if job.error else None,
            )
            for job in batch.jobs
        ],
        warnings=[capability_warning_from_sdk(warning) for warning in batch.warnings],
        errors=[capability_error_from_sdk(error) for error in batch.errors],
    )


def descriptors_from_result(result: IngestResult) -> list:
    descriptors = []
    if result.outputs.markdown_path:
        descriptors.append(descriptor_for_job_artifact(result.job_id, "document.md"))
    if result.outputs.document_json_path:
        descriptors.append(descriptor_for_job_artifact(result.job_id, "ingest_document.json"))
    if result.outputs.trace_path:
        descriptors.append(descriptor_for_job_artifact(result.job_id, "trace.jsonl"))
    if result.outputs.manifest_path:
        descriptors.append(descriptor_for_job_artifact(result.job_id, "manifest.json"))
    if result.document and result.document.raw_id:
        descriptors.append(descriptor_for_raw_artifact(result.document.raw_id, "original", result.document.mime_type))
        descriptors.append(descriptor_for_raw_artifact(result.document.raw_id, "original.meta.json", "application/json"))
    return descriptors


def descriptors_from_batch_result(batch: IngestBatchResult) -> list:
    descriptors = [
        descriptor_for_batch_artifact(batch.batch_id, "summary.json"),
        descriptor_for_batch_artifact(batch.batch_id, "trace.jsonl"),
    ]
    for job in batch.jobs:
        if not job.job_id:
            continue
        if job.document:
            descriptors.append(descriptor_for_job_artifact(job.job_id, "document.md"))
        if job.manifest:
            descriptors.append(descriptor_for_job_artifact(job.job_id, "manifest.json"))
        if job.trace:
            descriptors.append(descriptor_for_job_artifact(job.job_id, "trace.jsonl"))
    return descriptors


def operation_summary(result: IngestResult) -> OperationSummary:
    if result.status == "success":
        return OperationSummary(title="Ingest completed", message="Created a Markdown ingest candidate.")
    if result.status == "partial":
        return OperationSummary(title="Ingest completed with warnings", message="Created a partial Markdown ingest candidate.")
    if result.status == "failed":
        return OperationSummary(title="Ingest failed", message="Swallow recorded a failed ingest result.")
    if result.status == "queued":
        return OperationSummary(title="Ingest queued", message="Swallow accepted the ingest job.")
    return OperationSummary(title="Ingest running", message="Swallow is processing the ingest job.")


def batch_operation_summary(batch: IngestBatchResult) -> OperationSummary:
    if batch.status == "success":
        return OperationSummary(title="Batch completed", message="Created Markdown ingest candidates for all batch inputs.")
    if batch.status == "partial":
        return OperationSummary(title="Batch completed with failures", message="Created ingest candidates for part of the batch.")
    if batch.status == "failed":
        return OperationSummary(title="Batch failed", message="Swallow recorded a failed batch ingest result.")
    if batch.status == "canceled":
        return OperationSummary(title="Batch canceled", message="Swallow canceled the batch ingest.")
    if batch.status == "queued":
        return OperationSummary(title="Batch queued", message="Swallow accepted the batch ingest.")
    return OperationSummary(title="Batch running", message="Swallow is processing the batch ingest.")


def capability_warning_from_sdk(warning: Any) -> CapabilityWarning:
    return CapabilityWarning(
        code=warning.code,
        message=warning.message,
        stage=warning.stage,
        worker=warning.worker,
        details=warning.details,
    )


def capability_error_from_sdk(error: Any) -> CapabilityError:
    return CapabilityError(
        code=error.code,
        message=error.message,
        stage=error.stage,
        worker=error.worker,
        retryable=error.retryable,
        recoverable=error.recoverable,
        details=error.details,
    )


def read_job_times(store_root: Path, job_id: str) -> dict[str, str | None]:
    metadata = JobStore(store_root).read_job_metadata(job_id)
    if metadata is None:
        return {}
    return {"created_at": metadata.created_at, "finished_at": metadata.finished_at}


def read_worker_chain(store_root: Path, job_id: str) -> list[str]:
    manifest_path = store_root / "jobs" / job_id / "manifest.json"
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    chain: list[str] = []
    route = payload.get("route")
    if isinstance(route, list):
        chain.extend(str(item) for item in route if item)
    workers = payload.get("workers")
    if isinstance(workers, list):
        for item in workers:
            if isinstance(item, dict) and item.get("name"):
                chain.append(str(item["name"]))
            elif isinstance(item, str):
                chain.append(item)
    return chain


def read_provider_version() -> str:
    try:
        return version("swallow")
    except PackageNotFoundError:
        from swallow import __version__

        return __version__


def read_queue_status(client: Any, job_id: str) -> str | None:
    queue_store = getattr(client, "queue_store", None)
    if queue_store is None:
        return None
    try:
        item = queue_store.get_item(job_id)
    except Exception:
        return None
    return getattr(item, "status", None)
