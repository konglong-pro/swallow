from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession

from swallow.capability import (
    ArtifactView,
    CapabilityBatch,
    CapabilityBatchResult,
    CapabilityJob,
    CapabilityProviderError,
    CapabilityResult,
    CancelResult,
    DoctorResult,
    ProviderManifest,
    SwallowCapabilityProvider,
)
from swallow.sdk import IngestSdkError
from swallow.sdk.models import (
    IngestError,
    IngestJob,
    IngestOutputs,
    IngestResult,
    IngestTextPayload,
    IngestWarning,
    ReturnContentMode,
)
from swallow.sdk.result_mapper import DEFAULT_FULL_CONTENT_BYTES, DEFAULT_PREVIEW_BYTES
from swallow.sdk.security import validate_ingest_url


_T = TypeVar("_T")
_CONTENT_MODES = {"none", "preview", "full"}


@dataclass
class SwallowMcpContext:
    provider: SwallowCapabilityProvider
    batch_provider: SwallowCapabilityProvider
    enable_url_ingest: bool


def create_mcp_server(
    *,
    store_root: Path | str = ".",
    config_path: Path | str | None = None,
    command: str | Sequence[str] = "swallow",
    cwd: Path | str | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
    max_processes: int = 2,
    enable_url_ingest: bool = False,
) -> FastMCP:
    """Create the Swallow MCP adapter backed by SwallowCapabilityProvider."""

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[SwallowMcpContext]:
        profile_overrides = None
        policy = None
        if enable_url_ingest:
            profile_overrides = {
                "allowed_source_types": ("file", "url", "browser_capture", "archive"),
                "allow_network": True,
            }
            policy = {"allow_network": True}
        provider = SwallowCapabilityProvider(
            store_root=store_root,
            config_path=config_path,
            command=command,
            cwd=cwd,
            allowed_roots=allowed_roots,
            max_processes=max_processes,
            profile_overrides=profile_overrides,
            policy=policy,
        )
        batch_provider = SwallowCapabilityProvider(
            store_root=store_root,
            config_path=config_path,
            command=command,
            cwd=cwd,
            allowed_roots=allowed_roots,
            max_processes=max_processes,
            profile="heavy_queue",
        )
        try:
            yield SwallowMcpContext(
                provider=provider,
                batch_provider=batch_provider,
                enable_url_ingest=enable_url_ingest,
            )
        finally:
            provider.shutdown(wait=False)
            batch_provider.shutdown(wait=False)

    server = FastMCP("Swallow", lifespan=lifespan)

    @server.tool(name="swallow_capabilities_list")
    def swallow_capabilities_list(ctx: Context[ServerSession, SwallowMcpContext]) -> ProviderManifest:
        """List Swallow provider capabilities for this adapter."""
        return _mcp_context(ctx).provider.discover()

    @server.tool(name="swallow_doctor")
    async def swallow_doctor(ctx: Context[ServerSession, SwallowMcpContext], deep: bool = False) -> DoctorResult:
        """Run Swallow provider diagnostics."""
        return await _call_provider(lambda: _mcp_context(ctx).provider.doctor({"deep": deep}))

    @server.tool(name="swallow_ingest_file")
    async def swallow_ingest_file(path: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit a local file ingest job."""
        job = await _call_provider(
            lambda: _mcp_context(ctx).provider.submit(
                {"capability_id": "swallow.ingest.file", "input": {"source_path": path}}
            )
        )
        return _legacy_job_from_capability(job)

    @server.tool(name="swallow_ingest_url")
    async def swallow_ingest_url(url: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit a URL ingest job when URL ingest is explicitly enabled."""
        context = _mcp_context(ctx)
        _validate_url_ingest(context, url)
        job = await _call_provider(
            lambda: context.provider.submit({"capability_id": "swallow.ingest.url", "input": {"url": url}})
        )
        return _legacy_job_from_capability(job)

    @server.tool(name="swallow_ingest_browser_capture")
    async def swallow_ingest_browser_capture(path: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit a browser capture ingest job."""
        job = await _call_provider(
            lambda: _mcp_context(ctx).provider.submit(
                {"capability_id": "swallow.ingest.browser_capture", "input": {"source_path": path}}
            )
        )
        return _legacy_job_from_capability(job)

    @server.tool(name="swallow_ingest_archive")
    async def swallow_ingest_archive(path: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit an export archive ingest job."""
        job = await _call_provider(
            lambda: _mcp_context(ctx).provider.submit(
                {"capability_id": "swallow.ingest.archive", "input": {"source_path": path}}
            )
        )
        return _legacy_job_from_capability(job)

    @server.tool(name="swallow_ingest_batch")
    async def swallow_ingest_batch(
        patterns: list[str],
        ctx: Context[ServerSession, SwallowMcpContext],
        workers: int = 1,
    ) -> CapabilityBatch:
        """Queue a local file batch ingest through the provider."""
        return await _call_provider(
            lambda: _mcp_context(ctx).batch_provider.submit_batch(
                {"capability_id": "swallow.ingest.batch", "input": {"patterns": patterns, "workers": workers}}
            )
        )

    @server.tool(name="swallow_get_job")
    async def swallow_get_job(job_id: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Read persisted ingest job state."""
        job = await _call_provider(lambda: _mcp_context(ctx).provider.get_job(job_id))
        return _legacy_job_from_capability(job)

    @server.tool(name="swallow_get_batch")
    async def swallow_get_batch(batch_id: str, ctx: Context[ServerSession, SwallowMcpContext]) -> CapabilityBatch:
        """Read provider-created batch state."""
        return await _call_provider(lambda: _mcp_context(ctx).batch_provider.get_batch(batch_id))

    @server.tool(name="swallow_get_result")
    async def swallow_get_result(
        job_id: str,
        ctx: Context[ServerSession, SwallowMcpContext],
        content: ReturnContentMode = "preview",
    ) -> IngestResult:
        """Read an ingest result with path-first output and optional capped content."""
        content_mode = _validate_content(content)
        result = await _call_provider(
            lambda: _mcp_context(ctx).provider.get_result(job_id, {"content_mode": content_mode})
        )
        return _legacy_result_from_capability(result, content_mode)

    @server.tool(name="swallow_get_batch_result")
    async def swallow_get_batch_result(
        batch_id: str,
        ctx: Context[ServerSession, SwallowMcpContext],
    ) -> CapabilityBatchResult:
        """Read a provider-created batch result."""
        return await _call_provider(lambda: _mcp_context(ctx).batch_provider.get_batch_result(batch_id))

    @server.tool(name="swallow_wait_for_result")
    async def swallow_wait_for_result(
        job_id: str,
        ctx: Context[ServerSession, SwallowMcpContext],
        timeout: float = 60.0,
        poll_interval: float = 0.5,
        content: ReturnContentMode = "preview",
    ) -> IngestResult:
        """Wait for an ingest result with a bounded default timeout."""
        if timeout <= 0:
            raise ToolError("timeout must be greater than 0")
        content_mode = _validate_content(content)
        result = await _call_provider(
            lambda: _mcp_context(ctx).provider.wait(
                job_id,
                {"timeout": timeout, "poll_interval": poll_interval, "content_mode": content_mode},
            )
        )
        return _legacy_result_from_capability(result, content_mode)

    @server.tool(name="swallow_wait_for_batch")
    async def swallow_wait_for_batch(
        batch_id: str,
        ctx: Context[ServerSession, SwallowMcpContext],
        timeout: float = 60.0,
        poll_interval: float = 0.2,
    ) -> CapabilityBatchResult:
        """Wait for a provider-created batch result."""
        if timeout <= 0:
            raise ToolError("timeout must be greater than 0")
        return await _call_provider(
            lambda: _mcp_context(ctx).batch_provider.wait_batch(
                batch_id,
                {"timeout": timeout, "poll_interval": poll_interval},
            )
        )

    @server.tool(name="swallow_get_artifact")
    async def swallow_get_artifact(
        artifact_ref: str,
        ctx: Context[ServerSession, SwallowMcpContext],
        content: ReturnContentMode = "preview",
        max_bytes: int = 65536,
    ) -> ArtifactView:
        """Read a bounded provider artifact view."""
        content_mode = _validate_content(content)
        return await _call_provider(
            lambda: _mcp_context(ctx).provider.get_artifact(
                artifact_ref,
                {"content_mode": content_mode, "max_bytes": max_bytes},
            )
        )

    @server.tool(name="swallow_cancel_job")
    async def swallow_cancel_job(job_id: str, ctx: Context[ServerSession, SwallowMcpContext]) -> CancelResult:
        """Request provider job cancellation when the backend supports it."""
        return await _call_provider(lambda: _mcp_context(ctx).provider.cancel(job_id))

    @server.tool(name="swallow_cancel_batch")
    async def swallow_cancel_batch(batch_id: str, ctx: Context[ServerSession, SwallowMcpContext]) -> CancelResult:
        """Request provider batch cancellation."""
        return await _call_provider(lambda: _mcp_context(ctx).batch_provider.cancel_batch(batch_id))

    return server


def _mcp_context(ctx: Context[ServerSession, SwallowMcpContext]) -> SwallowMcpContext:
    return ctx.request_context.lifespan_context


async def _call_provider(action: Callable[[], Awaitable[_T]]) -> _T:
    try:
        return await action()
    except (CapabilityProviderError, IngestSdkError) as error:
        raise ToolError(str(error)) from error


def _validate_content(content: str) -> ReturnContentMode:
    if content not in _CONTENT_MODES:
        raise ToolError("content must be one of: none, preview, full")
    return cast(ReturnContentMode, content)


def _validate_url_ingest(context: SwallowMcpContext, url: str) -> None:
    if not context.enable_url_ingest:
        raise ToolError("URL ingest is disabled; start the MCP server with --enable-url-ingest to allow it.")
    try:
        validate_ingest_url(url)
    except IngestSdkError as error:
        raise ToolError(str(error)) from error


def _legacy_job_from_capability(job: CapabilityJob) -> IngestJob:
    trace_path = _relative_path_from_artifact_ref(job.trace_refs[0].artifact_ref) if job.trace_refs else f"jobs/{job.job_id}/trace.jsonl"
    return IngestJob(
        job_id=job.job_id,
        status=cast(Any, job.status),
        source_type=_source_type_from_capability(job.capability_id),
        created_at=job.created_at,
        trace_path=trace_path,
    )


def _legacy_result_from_capability(result: CapabilityResult, content_mode: ReturnContentMode) -> IngestResult:
    artifact_paths = {artifact.kind: _relative_path_from_artifact_ref(artifact.artifact_ref) for artifact in result.artifacts}
    outputs = IngestOutputs(
        markdown_path=artifact_paths.get("markdown_candidate"),
        document_json_path=artifact_paths.get("ingest_document"),
        trace_path=artifact_paths.get("trace"),
        manifest_path=artifact_paths.get("manifest"),
    )
    preview = None
    content = None
    text_payload = _legacy_text_payload(result, content_mode)
    if content_mode == "preview":
        preview = text_payload
    elif content_mode == "full":
        content = text_payload
    return IngestResult(
        job_id=result.job_id,
        status=cast(Any, result.status),
        outputs=outputs,
        preview=preview,
        content=content,
        warnings=[
            IngestWarning(
                code=warning.code,
                message=warning.message,
                stage=warning.stage,
                worker=warning.worker,
                details=warning.details,
            )
            for warning in result.warnings
        ],
        errors=[
            IngestError(
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
    )


def _legacy_text_payload(result: CapabilityResult, content_mode: ReturnContentMode) -> IngestTextPayload | None:
    primary = result.outputs.primary
    if primary is None or primary.preview is None or content_mode == "none":
        return None
    limit = DEFAULT_FULL_CONTENT_BYTES if content_mode == "full" else DEFAULT_PREVIEW_BYTES
    return IngestTextPayload(
        text=primary.preview,
        truncated=primary.truncated,
        bytes_read=len(primary.preview.encode("utf-8")),
        limit_bytes=limit,
    )


def _relative_path_from_artifact_ref(artifact_ref: str) -> str:
    parsed = urlparse(artifact_ref)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc == "jobs" and len(parts) == 2:
        return f"jobs/{parts[0]}/{parts[1]}"
    if parsed.netloc == "batch_runs" and len(parts) == 2:
        return f"batch_runs/{parts[0]}/{parts[1]}"
    if parsed.netloc == "raw" and len(parts) == 2:
        return f"raw_store/{parts[0]}/{parts[1]}"
    return artifact_ref


def _source_type_from_capability(capability_id: str) -> str:
    return capability_id.removeprefix("swallow.ingest.")
