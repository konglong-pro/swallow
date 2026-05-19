from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.sdk import (
    CliIngestClient,
    IngestSdkConfigError,
    IngestSdkInputError,
    IngestSdkJobNotFound,
    IngestSdkProtocolError,
    IngestWaitTimeout,
)
from swallow.sdk.models import IngestOutputs, IngestResult
from swallow.sdk.result_mapper import DEFAULT_FULL_CONTENT_BYTES, DEFAULT_PREVIEW_BYTES


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


pytestmark = pytest.mark.transport


def test_cli_file_json_outputs_ingest_result(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("# Sample\n\n" + "hello swallow " * 40, encoding="utf-8")

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "file", str(sample), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["job_id"].startswith("ing_")
    assert payload["status"] == "success"
    assert payload["outputs"]["markdown_path"].startswith("jobs/")
    assert payload["preview"]["text"]
    assert payload["content"] is None


def test_cli_file_jsonl_outputs_submitted_and_terminal_events(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("# Sample\n\n" + "hello swallow " * 40, encoding="utf-8")

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "file", str(sample), "--jsonl"])

    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.output.splitlines()]
    assert [event["event"] for event in events] == ["job_submitted", "job_finished"]
    assert events[0]["job"]["job_id"] == events[1]["result"]["job_id"]
    assert events[1]["result"]["status"] == "success"


def test_cli_file_json_failure_outputs_failed_result_and_nonzero_exit(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow", encoding="utf-8")
    config = tmp_path / "swallow.config.yaml"
    config.write_text(
        """
workers:
  plain_text:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "--config", str(config), "file", str(sample), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["errors"][0]["code"] == "WORKER_NOT_REGISTERED"


def test_cli_ingest_client_file_success(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Sample\n\n" + "hello swallow " * 40, encoding="utf-8")

    with make_client(store) as client:
        job = client.submit_file(sample)
        assert job.status == "running"
        result = client.wait(job.job_id, timeout=10)

    assert result.status == "success"
    assert result.outputs.markdown_path is not None
    assert (store / result.outputs.markdown_path).exists()
    assert result.preview is not None


def test_cli_ingest_client_browser_capture_and_archive_success(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    capture = store / "capture.json"
    capture.write_text(json.dumps(sample_capture(), ensure_ascii=False), encoding="utf-8")
    archive = store / "chatgpt-export.zip"
    write_chatgpt_export(archive)

    with make_client(store) as client:
        capture_result = client.browser_capture(capture)
        archive_result = client.archive(archive)

    assert capture_result.status == "success"
    assert archive_result.status == "success"
    assert capture_result.outputs.markdown_path is not None
    assert archive_result.outputs.markdown_path is not None
    assert (store / capture_result.outputs.markdown_path).exists()
    assert (store / archive_result.outputs.markdown_path).exists()


def test_cli_ingest_client_url_failure_returns_failed_result_without_network(tmp_path):
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

    with make_client(store, config_path=config) as client:
        result = client.url("https://example.com/article", content="none")

    assert result.status == "failed"
    assert result.preview is None
    assert result.errors
    assert result.errors[0].code == "WORKER_NOT_REGISTERED"


def test_cli_ingest_client_invocation_failures_raise_exceptions(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Sample\n\n" + "hello swallow " * 40, encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with make_client(store) as client:
        with pytest.raises(IngestSdkInputError):
            client.submit_file(outside)
        with pytest.raises(IngestSdkJobNotFound):
            client.get_job("ing_missing")

        monkeypatch.setattr(
            client,
            "get_result",
            lambda job_id, content="preview": IngestResult(
                job_id=job_id,
                status="running",
                outputs=IngestOutputs(trace_path="jobs/ing_slow/trace.jsonl"),
            ),
        )
        with pytest.raises(IngestWaitTimeout):
            client.wait("ing_slow", timeout=0.001, poll_interval=0.001)

    with make_client(store, command="swallow-command-that-does-not-exist") as client:
        with pytest.raises(IngestSdkConfigError):
            client.submit_file(sample)

    malformed_command = [sys.executable, "-c", "print('not json', flush=True)"]
    with make_client(store, command=malformed_command) as client:
        with pytest.raises(IngestSdkProtocolError):
            client.submit_file(sample)


def test_cli_ingest_client_preview_and_full_content_are_capped(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "large.txt"
    sample.write_text("# Large\n\n" + ("x" * (DEFAULT_FULL_CONTENT_BYTES + 10_000)), encoding="utf-8")

    with make_client(store) as client:
        preview_result = client.file(sample, content="preview")
        full_result = client.get_result(preview_result.job_id, content="full")

    assert preview_result.preview is not None
    assert preview_result.preview.truncated is True
    assert preview_result.preview.bytes_read == DEFAULT_PREVIEW_BYTES
    assert preview_result.content is None
    assert full_result.content is not None
    assert full_result.content.truncated is True
    assert full_result.content.bytes_read == DEFAULT_FULL_CONTENT_BYTES


def make_client(store: Path, *, command=None, config_path: Path | None = None) -> CliIngestClient:
    return CliIngestClient(
        store_root=store,
        config_path=config_path,
        command=command or python_cli_command(),
        cwd=REPO_ROOT,
    )


def python_cli_command() -> list[str]:
    script = f"import sys; sys.path.insert(0, {str(SRC_ROOT)!r}); from swallow.cli.main import app; app()"
    return [sys.executable, "-c", script]


def sample_capture() -> dict:
    long_text = "Captured through the CLI SDK. " * 30
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/example",
        "title": "CLI SDK capture",
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
    long_text = "Archived through the CLI SDK. " * 30
    return [
        {
            "title": "CLI SDK archive",
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
