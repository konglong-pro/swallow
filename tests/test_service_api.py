from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swallow.core.config import IngestConfig
from swallow.service.api import create_app
from swallow.workers.browser_capture_worker import BROWSER_CAPTURE_INVALID
from swallow.workers import firecrawl_worker as firecrawl_module

pytestmark = pytest.mark.api


def test_health_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_workers_endpoint_respects_config(tmp_path):
    config = IngestConfig.from_mapping({"workers": {"playwright": {"enabled": False}}})
    client = TestClient(create_app(tmp_path, config=config))

    response = client.get("/workers")

    assert response.status_code == 200
    names = {description["name"] for description in response.json()}
    assert "playwright_worker" not in names
    assert "firecrawl_worker" in names


def test_service_file_ingest_and_job_reads(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["document"].startswith("jobs/")

    inspect_response = client.get(f"/jobs/{payload['job']}")
    assert inspect_response.status_code == 200
    inspected = inspect_response.json()
    assert inspected["job_id"] == payload["job"]
    assert inspected["source"]["original_filename"] == "sample.txt"
    artifact = tmp_path / "jobs" / payload["job"] / "intermediate" / "debug.txt"
    artifact.write_text("debug artifact", encoding="utf-8")
    enriched_response = client.get(f"/jobs/{payload['job']}", params={"raw": True, "artifacts": True})
    assert enriched_response.status_code == 200
    enriched = enriched_response.json()
    assert enriched["raw"]["original_filename"] == "sample.txt"
    assert enriched["raw"]["exists"] is True
    assert enriched["artifacts"][0]["path"] == "intermediate/debug.txt"
    assert enriched["artifacts"][0]["exists"] is True
    job_metadata = json.loads((tmp_path / "jobs" / payload["job"] / "job.json").read_text(encoding="utf-8"))
    assert job_metadata["status"] == "success"
    assert job_metadata["document_path"] == payload["document"]
    assert job_metadata["manifest_path"].endswith("/manifest.json")
    assert job_metadata["ingest_document_id"] == payload["ingest_document_id"]
    manifest = json.loads((tmp_path / "jobs" / payload["job"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["job_id"] == payload["job"]
    assert manifest["raw_id"] == payload["raw_id"]
    assert manifest["outputs"]["markdown"] == payload["document"]
    assert manifest["outputs"]["json"] == payload["ingest_document"]
    assert manifest["outputs"]["trace"] == payload["trace"]

    trace_response = client.get(f"/jobs/{payload['job']}/trace")
    assert trace_response.status_code == 200
    assert "job_finished" in trace_response.text

    trace_tail_response = client.get(f"/jobs/{payload['job']}/trace", params={"tail": 1, "json": True})
    assert trace_tail_response.status_code == 200
    trace_tail = trace_tail_response.json()
    assert len(trace_tail) == 1
    assert trace_tail[0]["event"] == "job_finished"


def test_v1_file_ingest_can_read_job_document_manifest_and_trace(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/v1/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
        params={"wait": True},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    job_id = payload["job_id"]

    inspect_response = client.get(f"/v1/jobs/{job_id}")
    result_response = client.get(f"/v1/jobs/{job_id}/result")
    document_response = client.get(f"/v1/jobs/{job_id}/document")
    manifest_response = client.get(f"/v1/jobs/{job_id}/manifest")
    trace_response = client.get(f"/v1/jobs/{job_id}/trace")
    trace_json_response = client.get(f"/v1/jobs/{job_id}/trace", params={"json": True, "tail": 1})

    assert inspect_response.status_code == 200
    assert inspect_response.json()["job_id"] == job_id
    assert inspect_response.json()["status"] == "success"
    assert result_response.status_code == 200
    assert result_response.json()["outputs"]["markdown_path"].startswith("jobs/")
    assert document_response.status_code == 200
    assert "# Sample" in document_response.text
    assert manifest_response.status_code == 200
    assert manifest_response.json()["outputs"]["markdown"] == payload["outputs"]["markdown_path"]
    assert trace_response.status_code == 200
    assert "job_finished" in trace_response.text
    assert trace_json_response.status_code == 200
    assert trace_json_response.json()[0]["event"] == "job_finished"


def test_v1_file_ingest_defaults_to_async_job(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/v1/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )

    assert response.status_code == 200, response.text
    job = response.json()
    assert job["job_id"].startswith("ing_")
    assert job["status"] == "running"

    result = wait_for_v1_result(client, job["job_id"])
    assert result["status"] == "success"
    assert result["outputs"]["markdown_path"].startswith("jobs/")
    assert result["preview"]["text"]


def test_v1_file_ingest_worker_failure_returns_failed_result(tmp_path):
    disabled = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})
    client = TestClient(create_app(tmp_path, config=disabled))

    response = client.post(
        "/v1/ingest/file",
        params={"wait": True},
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["errors"][0]["code"] == "WORKER_NOT_REGISTERED"
    assert payload["outputs"]["trace_path"].endswith("/trace.jsonl")
    assert payload["outputs"]["manifest_path"].endswith("/manifest.json")


def test_v1_file_ingest_reports_pre_job_size_limit(tmp_path):
    config = IngestConfig.from_mapping({"limits": {"max_file_size_bytes": 4}})
    client = TestClient(create_app(tmp_path, config=config))

    response = client.post(
        "/v1/ingest/file",
        files={"file": ("sample.txt", b"too large", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INPUT_TOO_LARGE_SYNC"


def test_legacy_ingest_file_endpoint_advertises_v1_successor(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == '</v1/ingest/file>; rel="successor-version"'


def test_service_lists_jobs(tmp_path):
    client = TestClient(create_app(tmp_path))
    first_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    first_payload = first_response.json()
    rerun_response = client.post(f"/jobs/{first_payload['job']}/rerun")
    assert rerun_response.status_code == 200, rerun_response.text

    response = client.get("/jobs", params={"limit": 1})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["status"] == "success"
    assert payload[0]["source_type"] == "file"
    assert payload[0]["original_filename"] == "sample.txt"
    assert payload[0]["primary_worker"] == "plain_text_worker"
    assert payload[0]["document_path"].startswith("jobs/")


def test_service_rerun_reuses_raw_and_creates_new_job(tmp_path):
    client = TestClient(create_app(tmp_path))
    first_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    first_payload = first_response.json()

    rerun_response = client.post(f"/jobs/{first_payload['job']}/rerun")

    assert rerun_response.status_code == 200, rerun_response.text
    rerun_payload = rerun_response.json()
    assert rerun_payload["status"] == "success"
    assert rerun_payload["job"] != first_payload["job"]
    assert rerun_payload["raw_id"] == first_payload["raw_id"]

    rerun_doc = json.loads((tmp_path / rerun_payload["ingest_document"]).read_text(encoding="utf-8"))
    assert rerun_doc["provenance"]["primary_worker"] == "plain_text_worker"
    assert (tmp_path / rerun_payload["trace"]).exists()


def test_service_rerun_can_force_worker(tmp_path):
    client = TestClient(create_app(tmp_path))
    first_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    first_payload = first_response.json()

    rerun_response = client.post(f"/jobs/{first_payload['job']}/rerun", json={"worker": "plain_text_worker"})

    assert rerun_response.status_code == 200, rerun_response.text
    rerun_doc = json.loads((tmp_path / rerun_response.json()["ingest_document"]).read_text(encoding="utf-8"))
    assert rerun_doc["provenance"]["worker_chain"] == [
        "plain_text_worker@0.1.0",
        "quality_checker@0.1.0",
        "markdown_normalizer@0.1.0",
    ]


def test_service_rerun_missing_job_returns_404(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post("/jobs/ing_missing/rerun")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "type": "InputError",
        "code": "JOB_NOT_FOUND",
        "message": "Job not found: ing_missing",
        "retryable": False,
        "fallback_allowed": False,
    }


def test_service_rerun_unknown_worker_returns_400_without_new_job(tmp_path):
    client = TestClient(create_app(tmp_path))
    first_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    first_payload = first_response.json()

    response = client.post(f"/jobs/{first_payload['job']}/rerun", json={"worker": "missing_worker"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WORKER_NOT_REGISTERED"
    assert response.json()["detail"]["message"] == "Worker is not registered: missing_worker"
    assert response.json()["detail"]["fallback_allowed"] is False
    assert len(list((tmp_path / "jobs").iterdir())) == 1


def test_service_rerun_incompatible_worker_returns_400_without_new_job(tmp_path):
    client = TestClient(create_app(tmp_path))
    first_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    first_payload = first_response.json()

    response = client.post(f"/jobs/{first_payload['job']}/rerun", json={"worker": "firecrawl_worker"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WORKER_CANNOT_HANDLE"
    assert response.json()["detail"]["message"] == "Worker cannot handle source: firecrawl_worker for file sample.txt"
    assert len(list((tmp_path / "jobs").iterdir())) == 1


def test_service_rerun_failed_job_uses_job_json(tmp_path):
    disabled = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})
    client = TestClient(create_app(tmp_path, config=disabled))
    failed_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    assert failed_response.status_code == 400
    assert failed_response.json()["detail"]["code"] == "WORKER_NOT_REGISTERED"
    failed_job = list((tmp_path / "jobs").iterdir())[0].name
    failed_metadata = json.loads((tmp_path / "jobs" / failed_job / "job.json").read_text(encoding="utf-8"))
    assert failed_metadata["status"] == "failed"
    assert failed_metadata["error"]["code"] == "WORKER_NOT_REGISTERED"
    failed_manifest = json.loads((tmp_path / "jobs" / failed_job / "manifest.json").read_text(encoding="utf-8"))
    assert failed_manifest["status"] == "failed"
    assert failed_manifest["errors"][0]["code"] == "WORKER_NOT_REGISTERED"

    client = TestClient(create_app(tmp_path))
    rerun_response = client.post(f"/jobs/{failed_job}/rerun")

    assert rerun_response.status_code == 200, rerun_response.text
    rerun_payload = rerun_response.json()
    rerun_metadata = json.loads((tmp_path / "jobs" / rerun_payload["job"] / "job.json").read_text(encoding="utf-8"))
    assert rerun_metadata["status"] == "success"
    assert rerun_metadata["original_filename"] == "sample.txt"
    assert rerun_payload["raw_id"] == failed_metadata["raw_id"]


def test_service_inspect_failed_job_uses_job_json(tmp_path):
    disabled = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})
    client = TestClient(create_app(tmp_path, config=disabled))
    failed_response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"# Sample\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    assert failed_response.status_code == 400
    failed_job = list((tmp_path / "jobs").iterdir())[0].name

    response = client.get(f"/jobs/{failed_job}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job_id"] == failed_job
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "WORKER_NOT_REGISTERED"
    assert payload["trace_tail"][-1]["event"] == "job_failed"


def test_service_file_ingest_reports_size_limit(tmp_path):
    config = IngestConfig.from_mapping({"limits": {"max_file_size_bytes": 4}})
    client = TestClient(create_app(tmp_path, config=config))

    response = client.post(
        "/ingest/file",
        files={"file": ("sample.txt", b"too large", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INPUT_TOO_LARGE_SYNC"
    assert "exceeds synchronous file limit" in response.json()["detail"]["message"]


def test_service_url_ingest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "# API Article\n\n" + "API content. " * 40,
            "html": "<html><body>API content</body></html>",
            "metadata": {"title": "API Article", "statusCode": 200},
        },
    )
    client = TestClient(create_app(tmp_path))

    response = client.post("/ingest/url", json={"url": "https://example.com/article"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert (tmp_path / payload["document"]).exists()


def test_v1_url_browser_capture_and_archive_ingest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "# API Article\n\n" + "API content. " * 40,
            "html": "<html><body>API content</body></html>",
            "metadata": {"title": "API Article", "statusCode": 200},
        },
    )
    archive = tmp_path / "chatgpt-export.zip"
    write_chatgpt_export(archive)
    client = TestClient(create_app(tmp_path / "store"))

    url_response = client.post("/v1/ingest/url", params={"wait": True}, json={"url": "https://example.com/article"})
    capture_response = client.post("/v1/ingest/browser-capture", params={"wait": True}, json=sample_capture())
    archive_response = client.post(
        "/v1/ingest/archive",
        params={"wait": True},
        files={"file": ("chatgpt-export.zip", archive.read_bytes(), "application/zip")},
    )

    assert url_response.status_code == 200, url_response.text
    assert capture_response.status_code == 200, capture_response.text
    assert archive_response.status_code == 200, archive_response.text
    assert (tmp_path / "store" / url_response.json()["outputs"]["markdown_path"]).exists()
    assert (tmp_path / "store" / capture_response.json()["outputs"]["markdown_path"]).exists()
    assert (tmp_path / "store" / archive_response.json()["outputs"]["markdown_path"]).exists()


def test_service_browser_capture_ingest(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post("/ingest/browser-capture", json=sample_capture())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert (tmp_path / payload["document"]).exists()


def test_service_browser_capture_invalid_schema_returns_structured_error(tmp_path):
    client = TestClient(create_app(tmp_path))
    payload = sample_capture()
    payload["messages"] = [{"role": "user", "content": None}]

    response = client.post("/ingest/browser-capture", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == BROWSER_CAPTURE_INVALID
    assert response.json()["detail"]["retryable"] is False
    job = next((tmp_path / "jobs").iterdir())
    job_json = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert job_json["error"]["code"] == BROWSER_CAPTURE_INVALID


def test_service_archive_ingest(tmp_path):
    archive = tmp_path / "chatgpt-export.zip"
    write_chatgpt_export(archive)
    client = TestClient(create_app(tmp_path / "store"))

    response = client.post(
        "/ingest/archive",
        files={"file": ("chatgpt-export.zip", archive.read_bytes(), "application/zip")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert (tmp_path / "store" / payload["document"]).exists()


def test_service_missing_job_returns_404(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.get("/jobs/ing_missing")

    assert response.status_code == 404


def sample_capture() -> dict:
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/example",
        "title": "API capture",
        "captured_at": "2026-05-14T10:30:00-07:00",
        "messages": [
            {"role": "user", "content": "Capture this page."},
            {"role": "assistant", "content": "Captured through the service API."},
        ],
        "raw_dom": "<html><body>capture</body></html>",
    }


def write_chatgpt_export(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("conversations.json", json.dumps(sample_chatgpt_conversations(), ensure_ascii=False))


def sample_chatgpt_conversations() -> list[dict]:
    return [
        {
            "title": "API archive",
            "create_time": 1.0,
            "update_time": 4.0,
            "mapping": {
                "user": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 2.0,
                        "content": {"content_type": "text", "parts": ["Archive this conversation."]},
                    }
                },
                "assistant": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 3.0,
                        "content": {"content_type": "text", "parts": ["Archived through the service API."]},
                    }
                },
            },
        }
    ]


def wait_for_v1_result(client: TestClient, job_id: str, *, attempts: int = 50) -> dict:
    for _ in range(attempts):
        response = client.get(f"/v1/jobs/{job_id}/result")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"success", "partial", "failed"}:
            return payload
    raise AssertionError(f"Job did not finish: {job_id}")
