from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.errors import SecurityError
from swallow.core.runner import IngestRunner
from swallow.service.api import create_app, safe_upload_filename
from swallow.workers.export_archive_worker import ZIP_SLIP_BLOCKED, is_unsafe_archive_member
from swallow.workers.web_common import UNSAFE_ARTIFACT_PATH, save_text_artifact


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.security
def test_safe_upload_filename_strips_path_traversal_components():
    assert safe_upload_filename("../../evil.md") == "evil.md"
    assert safe_upload_filename("/absolute/path/evil.md") == "evil.md"
    assert safe_upload_filename(r"nested\..\..\evil.md") == "evil.md"
    assert safe_upload_filename("\x00..") == "upload.bin"


@pytest.mark.security
def test_service_upload_with_traversal_filename_stays_inside_store(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/v1/ingest/file",
        files={"file": ("../../evil.md", b"# Safe\n\n" + b"hello swallow " * 40, "text/markdown")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    raw_response = client.get(f"/v1/jobs/{payload['job']}", params={"raw": True})
    assert raw_response.status_code == 200
    raw = raw_response.json()["raw"]
    assert raw["original_filename"] == "evil.md"
    assert raw["path"].startswith("raw_store/")
    assert Path(raw["absolute_path"]).resolve().is_relative_to(tmp_path.resolve())
    assert not (tmp_path.parent / "evil.md").exists()


@pytest.mark.security
def test_artifact_writer_blocks_paths_outside_job_dir(tmp_path):
    job_dir = tmp_path / "jobs" / "ing_test"
    job_dir.mkdir(parents=True)

    with pytest.raises(SecurityError) as error:
        save_text_artifact(job_dir, "../evil.txt", "evil")

    assert error.value.code == UNSAFE_ARTIFACT_PATH
    assert not (tmp_path / "jobs" / "evil.txt").exists()


@pytest.mark.security
def test_archive_member_path_validator_detects_zip_slip_names():
    assert is_unsafe_archive_member("../../evil.txt") is True
    assert is_unsafe_archive_member("nested/../../../evil.txt") is True
    assert is_unsafe_archive_member("/absolute/path/evil.txt") is True
    assert is_unsafe_archive_member("C:/absolute/path/evil.txt") is True
    assert is_unsafe_archive_member("conversations.json") is False
    assert is_unsafe_archive_member("export/conversations.json") is False


@pytest.mark.security
def test_zip_slip_archive_fails_with_trace_and_manifest_security_error(tmp_path):
    store = tmp_path / "store"
    archive = FIXTURES / "bad" / "zip-slip.zip"

    with pytest.raises(Exception) as error:
        IngestRunner(store_root=store).ingest_archive(archive)

    assert getattr(error.value, "code", None) == ZIP_SLIP_BLOCKED
    job_dir = next((store / "jobs").iterdir())
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    trace_events = [json.loads(line) for line in (job_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]

    assert manifest["status"] == "failed"
    assert manifest["errors"][0]["code"] == ZIP_SLIP_BLOCKED
    assert any(event["event"] == "security_error" for event in trace_events)
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


@pytest.mark.security
def test_cli_archive_zip_slip_reports_structured_security_failure(tmp_path):
    result = CliRunner().invoke(
        app,
        ["--store", str(tmp_path / "store"), "archive", str(FIXTURES / "bad" / "zip-slip.zip")],
    )

    assert result.exit_code == 1
    assert ZIP_SLIP_BLOCKED in result.output
    job_dir = next((tmp_path / "store" / "jobs").iterdir())
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["errors"][0]["code"] == ZIP_SLIP_BLOCKED
