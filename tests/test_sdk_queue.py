from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from swallow.core.config import IngestConfig
from swallow.core.queue_worker import QueueWorker
from swallow.sdk import IngestSdkConfigError, QueueIngestClient


pytestmark = pytest.mark.transport


def test_queue_sdk_file_submit_worker_wait_success(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Queue\n\n" + "hello swallow " * 40, encoding="utf-8")

    with QueueIngestClient(store_root=store) as client:
        job = client.submit_file(sample)
        assert job.status == "queued"

        worker_result = QueueWorker(store_root=store).run_once()
        result = client.wait(job.job_id, timeout=5)

    assert worker_result["status"] == "success"
    assert result.status == "success"
    assert result.outputs.markdown_path is not None
    assert result.outputs.document_json_path is not None
    assert result.outputs.trace_path is not None
    assert result.outputs.manifest_path is not None
    assert (store / result.outputs.markdown_path).exists()
    assert (store / result.outputs.document_json_path).exists()
    assert (store / result.outputs.trace_path).exists()
    assert (store / result.outputs.manifest_path).exists()


def test_queue_sdk_browser_capture_archive_and_url_failed_result(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    capture = store / "capture.json"
    capture.write_text(json.dumps(sample_capture(), ensure_ascii=False), encoding="utf-8")
    archive = store / "chatgpt-export.zip"
    write_chatgpt_export(archive)
    web_disabled = IngestConfig.from_mapping(
        {
            "workers": {
                "firecrawl": {"enabled": False},
                "crawl4ai": {"enabled": False},
                "playwright": {"enabled": False},
                "playwright_profile": {"enabled": False},
            }
        }
    )

    with QueueIngestClient(store_root=store) as client:
        capture_job = client.submit_browser_capture(capture)
        archive_job = client.submit_archive(archive)
        url_job = client.submit_url("https://example.com/article")

        assert QueueWorker(store_root=store).run_once()["status"] == "success"
        assert QueueWorker(store_root=store).run_once()["status"] == "success"
        assert QueueWorker(store_root=store, config=web_disabled).run_once()["status"] == "failed"

        capture_result = client.get_result(capture_job.job_id)
        archive_result = client.get_result(archive_job.job_id)
        url_result = client.get_result(url_job.job_id, content="none")

    assert capture_result.status == "success"
    assert archive_result.status == "success"
    assert url_result.status == "failed"
    assert url_result.errors
    assert url_result.errors[0].code == "WORKER_NOT_REGISTERED"


def test_queue_sdk_worker_config_controls_execution(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Queue\n\n" + "hello swallow " * 40, encoding="utf-8")
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})

    with QueueIngestClient(store_root=store) as client:
        job = client.submit_file(sample)
        worker_result = QueueWorker(store_root=store, config=config).run_once()
        result = client.get_result(job.job_id, content="none")

    assert worker_result["status"] == "failed"
    assert result.status == "failed"
    assert result.errors[0].code == "WORKER_NOT_REGISTERED"


def test_queue_sdk_batch_snapshot_and_cancel(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    inputs = store / "inputs"
    inputs.mkdir()
    (inputs / "one.txt").write_text("# One\n\n" + "hello swallow " * 40, encoding="utf-8")
    (inputs / "two.txt").write_text("# Two\n\n" + "hello swallow " * 40, encoding="utf-8")

    with QueueIngestClient(store_root=store) as client:
        batch = client.submit_batch([str(inputs / "*.txt")], workers=1)
        (inputs / "three.txt").write_text("# Three\n\n" + "late file " * 40, encoding="utf-8")

        assert batch.status == "queued"
        assert batch.total == 2
        assert len(batch.job_ids) == 2

        canceled = client.cancel_batch(batch.batch_id)
        result = client.get_batch_result(batch.batch_id)

    assert canceled.status == "canceled"
    assert result.status == "canceled"
    assert result.canceled == 2
    assert result.total == 2
    assert (store / result.summary_path).exists()
    assert (store / result.trace_path).exists()


def test_queue_sdk_no_match_batch_fails_without_worker(tmp_path):
    store = tmp_path / "store"

    with QueueIngestClient(store_root=store) as client:
        batch = client.submit_batch([str(tmp_path / "missing" / "*.txt")])
        result = client.get_batch_result(batch.batch_id)

    assert batch.status == "failed"
    assert result.status == "failed"
    assert result.total == 0
    assert result.errors[0].code == "NO_BATCH_INPUTS"


def test_queue_sdk_rejects_non_persistent_storage(tmp_path):
    with pytest.raises(IngestSdkConfigError):
        QueueIngestClient(store_root=tmp_path, storage_mode="memory")


def sample_capture() -> dict:
    long_text = "Captured through the queue SDK. " * 30
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/example",
        "title": "Queue SDK capture",
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
    long_text = "Archived through the queue SDK. " * 30
    return [
        {
            "title": "Queue SDK archive",
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
