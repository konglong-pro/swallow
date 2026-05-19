from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swallow.core.config import IngestConfig
from swallow.sdk import (
    HttpIngestClient,
    IngestSdkConfigError,
    IngestSdkError,
    IngestSdkInputError,
    IngestSdkJobNotFound,
    IngestSdkProtocolError,
    IngestTransportError,
    IngestWaitTimeout,
)
from swallow.sdk.models import IngestOutputs, IngestResult
from swallow.sdk.result_mapper import DEFAULT_FULL_CONTENT_BYTES, DEFAULT_PREVIEW_BYTES
from swallow.service.api import create_app


pytestmark = [pytest.mark.http, pytest.mark.transport]


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_http_ingest_client_file_success_against_asgi_service(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Sample\n\n" + "hello swallow " * 40, encoding="utf-8")

    with make_client(store) as client:
        job = client.submit_file(sample)
        assert job.job_id.startswith("ing_")
        assert job.status == "running"
        result = client.wait(job.job_id, timeout=5)

    assert result.status == "success"
    assert result.outputs.markdown_path is not None
    assert result.preview is not None
    assert (store / result.outputs.markdown_path).exists()


def test_http_ingest_client_browser_capture_and_archive_success(tmp_path):
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


def test_http_ingest_client_url_failure_returns_failed_result_without_network(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    config = IngestConfig.from_mapping(
        {
            "workers": {
                "firecrawl": {"enabled": False},
                "crawl4ai": {"enabled": False},
                "playwright": {"enabled": False},
                "playwright_profile": {"enabled": False},
            }
        }
    )

    with make_client(store, config=config) as client:
        result = client.url("https://example.com/article", content="none")

    assert result.status == "failed"
    assert result.preview is None
    assert result.errors
    assert result.errors[0].code == "WORKER_NOT_REGISTERED"


def test_http_ingest_client_preview_and_full_content_are_capped(tmp_path):
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


def test_http_ingest_client_invocation_failures_raise_exceptions(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()

    with make_client(store) as client:
        with pytest.raises(IngestSdkInputError):
            client.submit_file(store / "missing.txt")
        with pytest.raises(IngestSdkJobNotFound):
            client.get_job("ing_missing")
        with pytest.raises(IngestSdkConfigError):
            client.get_result("ing_missing", content="invalid")

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

    malformed = HttpIngestClient(client=MalformedClient())
    with pytest.raises(IngestSdkProtocolError):
        malformed.get_job("ing_any")

    unavailable = HttpIngestClient(client=UnavailableClient())
    with pytest.raises(IngestSdkError):
        unavailable.health()

    timeout_client = HttpIngestClient(client=TimeoutClient())
    with pytest.raises(IngestTransportError):
        timeout_client.submit_url("https://example.com/article")


def test_http_ingest_client_start_local_smoke(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Local service\n\n" + "hello swallow " * 40, encoding="utf-8")

    with HttpIngestClient.start_local(store_root=store, cwd=REPO_ROOT, timeout=15) as client:
        assert client.health()["status"] == "ok"
        result = client.file(sample)

    assert result.status == "success"
    assert result.outputs.markdown_path is not None
    assert (store / result.outputs.markdown_path).exists()


def make_client(store: Path, *, config: IngestConfig | None = None) -> HttpIngestClient:
    app = create_app(store, config=config)
    return HttpIngestClient(client=TestClient(app), allowed_roots=[store])


def sample_capture() -> dict:
    long_text = "Captured through the HTTP SDK. " * 30
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/example",
        "title": "HTTP SDK capture",
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
    long_text = "Archived through the HTTP SDK. " * 30
    return [
        {
            "title": "HTTP SDK archive",
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


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"unexpected": True}


class MalformedClient:
    def get(self, *_args, **_kwargs):
        return FakeResponse()


class UnavailableClient:
    def get(self, *_args, **_kwargs):
        raise RuntimeError("service unavailable")


class TimeoutClient:
    def post(self, *_args, **_kwargs):
        raise TimeoutError("connect timed out")
