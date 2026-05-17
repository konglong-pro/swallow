from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from swallow.service.api import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local FastAPI service smoke test with TestClient.")
    parser.add_argument("--store", type=Path, help="Store root. Defaults to a temporary directory.")
    args = parser.parse_args()

    store = args.store or Path(tempfile.mkdtemp(prefix="swallow-service-smoke-"))
    store.mkdir(parents=True, exist_ok=True)
    client = TestClient(create_app(store))

    health = client.get("/health")
    assert_response(health.status_code, 200, health.text)

    file_response = client.post(
        "/v1/ingest/file",
        files={"file": ("sample.txt", b"# Service Smoke\n\n" + b"hello swallow " * 40, "text/plain")},
    )
    file_payload = assert_success_payload(file_response.status_code, file_response.json())
    rerun_response = client.post(f"/v1/jobs/{file_payload['job']}/rerun", json={"worker": "plain_text_worker"})
    rerun_payload = assert_success_payload(rerun_response.status_code, rerun_response.json())

    capture_response = client.post("/v1/ingest/browser-capture", json=sample_capture())
    capture_payload = assert_success_payload(capture_response.status_code, capture_response.json())

    archive_path = store / "chatgpt-export.zip"
    write_chatgpt_export(archive_path)
    archive_response = client.post(
        "/v1/ingest/archive",
        files={"file": ("chatgpt-export.zip", archive_path.read_bytes(), "application/zip")},
    )
    archive_payload = assert_success_payload(archive_response.status_code, archive_response.json())

    for payload in (file_payload, rerun_payload, capture_payload, archive_payload):
        inspect_response = client.get(f"/v1/jobs/{payload['job']}")
        assert_response(inspect_response.status_code, 200, inspect_response.text)
        document_response = client.get(f"/v1/jobs/{payload['job']}/document")
        assert_response(document_response.status_code, 200, document_response.text)
        manifest_response = client.get(f"/v1/jobs/{payload['job']}/manifest")
        assert_response(manifest_response.status_code, 200, manifest_response.text)
        trace_response = client.get(f"/v1/jobs/{payload['job']}/trace")
        assert_response(trace_response.status_code, 200, trace_response.text)
        job_metadata = json.loads((store / "jobs" / payload["job"] / "job.json").read_text(encoding="utf-8"))
        if job_metadata.get("status") != "success":
            raise AssertionError(f"Expected success job metadata, got {job_metadata}")
        manifest_path = store / "jobs" / payload["job"] / "manifest.json"
        if not manifest_path.exists():
            raise AssertionError(f"Expected manifest.json for job {payload['job']}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("outputs", {}).get("manifest") != payload.get("manifest"):
            raise AssertionError(f"Manifest output mismatch: {manifest}")

    jobs_response = client.get("/v1/jobs", params={"limit": 10})
    jobs_payload = assert_jobs_payload(jobs_response.status_code, jobs_response.json())
    if len(jobs_payload) < 4:
        raise AssertionError(f"Expected at least 4 jobs, got {len(jobs_payload)}")

    print(f"Store: {store}")
    print(f"File job: {file_payload['job']}")
    print(f"Rerun job: {rerun_payload['job']}")
    print(f"Browser capture job: {capture_payload['job']}")
    print(f"Archive job: {archive_payload['job']}")
    print("Service runtime smoke: success")
    return 0


def assert_response(status_code: int, expected: int, body: str) -> None:
    if status_code != expected:
        raise AssertionError(f"Expected HTTP {expected}, got {status_code}: {body}")


def assert_success_payload(status_code: int, payload: dict) -> dict:
    assert_response(status_code, 200, json.dumps(payload, ensure_ascii=False))
    if payload.get("status") != "success":
        raise AssertionError(f"Expected success payload, got {payload}")
    return payload


def assert_jobs_payload(status_code: int, payload: list[dict]) -> list[dict]:
    assert_response(status_code, 200, json.dumps(payload, ensure_ascii=False))
    if not isinstance(payload, list):
        raise AssertionError(f"Expected jobs list payload, got {payload}")
    for item in payload:
        if item.get("status") not in {"success", "failed", "unknown"}:
            raise AssertionError(f"Unexpected job summary status: {item}")
    return payload


def sample_capture() -> dict:
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/service-smoke",
        "title": "Service smoke",
        "captured_at": "2026-05-15T00:00:00Z",
        "messages": [
            {"role": "user", "content": "Run service smoke."},
            {"role": "assistant", "content": "Service smoke completed."},
        ],
        "raw_dom": "<html><body>service smoke</body></html>",
    }


def write_chatgpt_export(path: Path) -> None:
    payload = [
        {
            "title": "Service archive smoke",
            "mapping": {
                "user": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1,
                        "content": {"content_type": "text", "parts": ["Archive smoke."]},
                    }
                },
                "assistant": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 2,
                        "content": {"content_type": "text", "parts": ["Archive smoke completed."]},
                    }
                },
            },
        }
    ]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("conversations.json", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
