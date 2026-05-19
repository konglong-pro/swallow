from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession

from swallow.sdk import CliIngestClient, IngestSdkError
from swallow.sdk.models import IngestJob, IngestResult, ReturnContentMode
from swallow.sdk.security import validate_ingest_url


_T = TypeVar("_T")
_CONTENT_MODES = {"none", "preview", "full"}
@dataclass
class SwallowMcpContext:
    client: CliIngestClient
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
    """Create the Swallow MCP server backed by CliIngestClient."""

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[SwallowMcpContext]:
        client = CliIngestClient(
            store_root=store_root,
            config_path=config_path,
            command=command,
            cwd=cwd,
            allowed_roots=allowed_roots,
            max_processes=max_processes,
        )
        try:
            yield SwallowMcpContext(client=client, enable_url_ingest=enable_url_ingest)
        finally:
            client.shutdown(wait=False)

    server = FastMCP("Swallow", lifespan=lifespan)

    @server.tool(name="swallow_ingest_file")
    def swallow_ingest_file(path: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit a local file ingest job."""
        return _call_sdk(lambda: _mcp_context(ctx).client.submit_file(path))

    @server.tool(name="swallow_ingest_url")
    def swallow_ingest_url(url: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit a URL ingest job when URL ingest is explicitly enabled."""
        context = _mcp_context(ctx)
        _validate_url_ingest(context, url)
        return _call_sdk(lambda: context.client.submit_url(url))

    @server.tool(name="swallow_ingest_browser_capture")
    def swallow_ingest_browser_capture(path: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit a browser capture ingest job."""
        return _call_sdk(lambda: _mcp_context(ctx).client.submit_browser_capture(path))

    @server.tool(name="swallow_ingest_archive")
    def swallow_ingest_archive(path: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Submit an export archive ingest job."""
        return _call_sdk(lambda: _mcp_context(ctx).client.submit_archive(path))

    @server.tool(name="swallow_get_job")
    def swallow_get_job(job_id: str, ctx: Context[ServerSession, SwallowMcpContext]) -> IngestJob:
        """Read persisted ingest job state."""
        return _call_sdk(lambda: _mcp_context(ctx).client.get_job(job_id))

    @server.tool(name="swallow_get_result")
    def swallow_get_result(
        job_id: str,
        ctx: Context[ServerSession, SwallowMcpContext],
        content: ReturnContentMode = "preview",
    ) -> IngestResult:
        """Read an ingest result with path-first output and optional capped content."""
        content_mode = _validate_content(content)
        return _call_sdk(lambda: _mcp_context(ctx).client.get_result(job_id, content=content_mode))

    @server.tool(name="swallow_wait_for_result")
    def swallow_wait_for_result(
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
        return _call_sdk(
            lambda: _mcp_context(ctx).client.wait(
                job_id,
                timeout=timeout,
                poll_interval=poll_interval,
                content=content_mode,
            )
        )

    return server


def _mcp_context(ctx: Context[ServerSession, SwallowMcpContext]) -> SwallowMcpContext:
    return ctx.request_context.lifespan_context


def _call_sdk(action: Callable[[], _T]) -> _T:
    try:
        return action()
    except IngestSdkError as error:
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
