from __future__ import annotations

import json
import os
import sys
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.mcp import create_mcp_server
from swallow.sdk.errors import IngestWaitTimeout
from swallow.sdk.models import IngestJob, IngestOutputs, IngestResult
from swallow.sdk.result_mapper import DEFAULT_FULL_CONTENT_BYTES, DEFAULT_PREVIEW_BYTES


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
MCP_TOOL_NAMES = {
    "swallow_ingest_file",
    "swallow_ingest_url",
    "swallow_ingest_browser_capture",
    "swallow_ingest_archive",
    "swallow_get_job",
    "swallow_get_result",
    "swallow_wait_for_result",
}


pytestmark = [pytest.mark.adapter, pytest.mark.mcp]


def test_mcp_server_exposes_core_tool_set(tmp_path):
    server = make_server(tmp_path / "store")

    async def scenario(session: ClientSession) -> set[str]:
        tools = await session.list_tools()
        return {tool.name for tool in tools.tools}

    assert run_mcp(server, scenario) == MCP_TOOL_NAMES


def test_mcp_adapter_boundary_uses_sdk_client_not_core_workers():
    source = (REPO_ROOT / "src" / "swallow" / "mcp" / "server.py").read_text(encoding="utf-8")

    assert "CliIngestClient" in source
    assert "IngestRunner" not in source
    assert "swallow.workers" not in source


def test_mcp_file_ingest_submit_wait_and_result_caps(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "large.txt"
    sample.write_text("# Large\n\n" + ("x" * (DEFAULT_FULL_CONTENT_BYTES + 10_000)), encoding="utf-8")
    server = make_server(store)

    async def scenario(session: ClientSession) -> tuple[IngestJob, IngestResult, IngestResult]:
        submitted = await session.call_tool("swallow_ingest_file", {"path": str(sample)})
        job = IngestJob.model_validate(submitted.structuredContent)
        waited = await session.call_tool(
            "swallow_wait_for_result",
            {"job_id": job.job_id, "timeout": 10, "poll_interval": 0.01, "content": "preview"},
        )
        preview_result = IngestResult.model_validate(waited.structuredContent)
        full = await session.call_tool("swallow_get_result", {"job_id": job.job_id, "content": "full"})
        full_result = IngestResult.model_validate(full.structuredContent)
        return job, preview_result, full_result

    job, preview_result, full_result = run_mcp(server, scenario)

    assert job.status == "running"
    assert preview_result.status == "success"
    assert preview_result.outputs.markdown_path is not None
    assert (store / preview_result.outputs.markdown_path).exists()
    assert preview_result.preview is not None
    assert preview_result.preview.truncated is True
    assert preview_result.preview.bytes_read == DEFAULT_PREVIEW_BYTES
    assert preview_result.content is None
    assert full_result.content is not None
    assert full_result.content.truncated is True
    assert full_result.content.bytes_read == DEFAULT_FULL_CONTENT_BYTES


def test_mcp_browser_capture_and_archive_ingest(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    capture = store / "capture.json"
    capture.write_text(json.dumps(sample_capture(), ensure_ascii=False), encoding="utf-8")
    archive = store / "chatgpt-export.zip"
    write_chatgpt_export(archive)
    server = make_server(store)

    async def scenario(session: ClientSession) -> tuple[IngestResult, IngestResult]:
        capture_job = IngestJob.model_validate(
            (await session.call_tool("swallow_ingest_browser_capture", {"path": str(capture)})).structuredContent
        )
        archive_job = IngestJob.model_validate(
            (await session.call_tool("swallow_ingest_archive", {"path": str(archive)})).structuredContent
        )
        capture_result = IngestResult.model_validate(
            (
                await session.call_tool(
                    "swallow_wait_for_result",
                    {"job_id": capture_job.job_id, "timeout": 10, "poll_interval": 0.01},
                )
            ).structuredContent
        )
        archive_result = IngestResult.model_validate(
            (
                await session.call_tool(
                    "swallow_wait_for_result",
                    {"job_id": archive_job.job_id, "timeout": 10, "poll_interval": 0.01},
                )
            ).structuredContent
        )
        return capture_result, archive_result

    capture_result, archive_result = run_mcp(server, scenario)

    assert capture_result.status == "success"
    assert archive_result.status == "success"
    assert capture_result.outputs.markdown_path is not None
    assert archive_result.outputs.markdown_path is not None
    assert (store / capture_result.outputs.markdown_path).exists()
    assert (store / archive_result.outputs.markdown_path).exists()


def test_mcp_url_ingest_is_disabled_by_default(tmp_path):
    server = make_server(tmp_path / "store")

    async def scenario(session: ClientSession) -> str:
        result = await session.call_tool("swallow_ingest_url", {"url": "https://example.com/article"})
        assert result.isError is True
        return result.content[0].text

    assert "URL ingest is disabled" in run_mcp(server, scenario)


def test_mcp_url_enabled_failure_returns_failed_result_without_network(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    config = store / "swallow.config.yaml"
    config.write_text(
        """
workers:
  firecrawl:
    enabled: false
  crawl4ai:
    enabled: false
  playwright:
    enabled: false
  playwright_profile:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    server = make_server(store, config_path=config, enable_url_ingest=True)

    async def scenario(session: ClientSession) -> IngestResult:
        submitted = await session.call_tool("swallow_ingest_url", {"url": "https://example.com/article"})
        job = IngestJob.model_validate(submitted.structuredContent)
        waited = await session.call_tool(
            "swallow_wait_for_result",
            {"job_id": job.job_id, "timeout": 10, "poll_interval": 0.01, "content": "none"},
        )
        assert waited.isError is not True, waited.content[0].text
        return IngestResult.model_validate(waited.structuredContent)

    result = run_mcp(server, scenario)

    assert result.status == "failed"
    assert result.preview is None
    assert result.errors
    assert result.errors[0].code == "WORKER_NOT_REGISTERED"


def test_mcp_invocation_failures_return_tool_errors(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    server = make_server(store)

    async def scenario(session: ClientSession) -> dict[str, str]:
        outside_result = await session.call_tool("swallow_ingest_file", {"path": str(outside)})
        unknown_job = await session.call_tool("swallow_get_job", {"job_id": "ing_missing"})
        invalid_content = await session.call_tool(
            "swallow_get_result",
            {"job_id": "ing_missing", "content": "invalid"},
        )
        return {
            "outside": outside_result.content[0].text,
            "unknown": unknown_job.content[0].text,
            "content": invalid_content.content[0].text,
        }

    errors = run_mcp(server, scenario)

    assert "outside allowed roots" in errors["outside"]
    assert "Job not found" in errors["unknown"]
    assert "Input should be" in errors["content"]

    monkeypatch.setattr("swallow.mcp.server.CliIngestClient", SlowCliIngestClient)
    slow_server = make_server(store)

    async def timeout_scenario(session: ClientSession) -> str:
        result = await session.call_tool("swallow_wait_for_result", {"job_id": "ing_slow", "timeout": 1})
        assert result.isError is True
        return result.content[0].text

    assert "Timed out waiting for job ing_slow" in run_mcp(slow_server, timeout_scenario)


def test_mcp_url_invalid_scheme_returns_tool_error(tmp_path):
    server = make_server(tmp_path / "store", enable_url_ingest=True)

    async def scenario(session: ClientSession) -> str:
        result = await session.call_tool("swallow_ingest_url", {"url": "file:///etc/passwd"})
        assert result.isError is True
        return result.content[0].text

    assert "http and https" in run_mcp(server, scenario)


@pytest.mark.network
def test_mcp_url_ingest_real_network_example_dot_com(tmp_path):
    if os.getenv("RUN_SWALLOW_NETWORK_TESTS") != "1":
        pytest.skip("Set RUN_SWALLOW_NETWORK_TESTS=1 to run MCP real network smoke tests.")

    store = tmp_path / "store"
    server = make_server(store, enable_url_ingest=True)

    async def scenario(session: ClientSession) -> IngestResult:
        submitted = await session.call_tool("swallow_ingest_url", {"url": "https://example.com/"})
        job = IngestJob.model_validate(submitted.structuredContent)
        waited = await session.call_tool(
            "swallow_wait_for_result",
            {"job_id": job.job_id, "timeout": 60, "poll_interval": 0.5, "content": "preview"},
        )
        return IngestResult.model_validate(waited.structuredContent)

    result = run_mcp(server, scenario)

    assert result.status in {"success", "partial"}
    assert result.preview is not None
    assert "Example" in result.preview.text


def test_cli_mcp_serve_help_is_available():
    result = CliRunner().invoke(app, ["mcp", "serve", "--help"])

    assert result.exit_code == 0, result.output
    assert "stdio" in result.output
    assert "--enable-url-ingest" in result.output


def run_mcp(server, scenario: Callable[[ClientSession], Awaitable[object]]):
    async def wrapped():
        async with create_connected_server_and_client_session(server, raise_exceptions=True) as session:
            return await scenario(session)

    return anyio.run(wrapped)


def make_server(
    store: Path,
    *,
    config_path: Path | None = None,
    enable_url_ingest: bool = False,
):
    store.mkdir(exist_ok=True)
    return create_mcp_server(
        store_root=store,
        config_path=config_path,
        command=python_cli_command(),
        cwd=REPO_ROOT,
        enable_url_ingest=enable_url_ingest,
    )


def python_cli_command() -> list[str]:
    script = f"import sys; sys.path.insert(0, {str(SRC_ROOT)!r}); from swallow.cli.main import app; app()"
    return [sys.executable, "-c", script]


class SlowCliIngestClient:
    def __init__(self, *args, **kwargs):
        pass

    def wait(self, job_id: str, **kwargs):
        raise IngestWaitTimeout(f"Timed out waiting for job {job_id}")

    def shutdown(self, *, wait: bool = True) -> None:
        pass


def sample_capture() -> dict:
    long_text = "Captured through the MCP server. " * 30
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/example",
        "title": "MCP capture",
        "captured_at": "2026-05-14T10:30:00-07:00",
        "messages": [
            {"role": "user", "content": "Capture this page. " + long_text},
            {"role": "assistant", "content": "Captured successfully. " + long_text},
        ],
        "raw_dom": "<html><body>capture</body></html>",
    }


def write_chatgpt_export(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("conversations.json", json.dumps(sample_chatgpt_conversations(), ensure_ascii=False))


def sample_chatgpt_conversations() -> list[dict]:
    long_text = "Archived through the MCP server. " * 30
    return [
        {
            "title": "MCP archive",
            "create_time": 1.0,
            "update_time": 4.0,
            "mapping": {
                "user": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 2.0,
                        "content": {"content_type": "text", "parts": ["Archive this conversation. " + long_text]},
                    }
                },
                "assistant": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 3.0,
                        "content": {"content_type": "text", "parts": ["Archived successfully. " + long_text]},
                    }
                },
            },
        }
    ]
