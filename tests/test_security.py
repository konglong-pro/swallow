from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.errors import SecurityError
from swallow.core.runner import IngestRunner
from swallow.sdk import CliIngestClient, HttpIngestClient, IngestClient, IngestSandboxError, LocalIngestClient, QueueIngestClient
from swallow.service.api import create_app, safe_upload_filename
from swallow.workers.export_archive_worker import ZIP_SLIP_BLOCKED, is_unsafe_archive_member
from swallow.workers.web_common import UNSAFE_ARTIFACT_PATH, save_text_artifact


FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


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
        params={"wait": True},
        files={"file": ("../../evil.md", b"# Safe\n\n" + b"hello swallow " * 40, "text/markdown")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    raw_response = client.get(f"/jobs/{payload['job_id']}", params={"raw": True})
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


@pytest.mark.security
def test_sdk_facade_rejects_paths_outside_allowed_roots(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with IngestClient(store_root=store, backend="auto", allowed_roots=[store]) as client:
        with pytest.raises(IngestSandboxError):
            client.submit_file(outside)


@pytest.mark.security
@pytest.mark.parametrize(
    "client_factory",
    [
        lambda store: LocalIngestClient(store_root=store, allowed_roots=[store]),
        lambda store: CliIngestClient(store_root=store, command=python_cli_command(), cwd=REPO_ROOT, allowed_roots=[store]),
        lambda store: HttpIngestClient(client=TestClient(create_app(store)), allowed_roots=[store]),
        lambda store: QueueIngestClient(store_root=store, allowed_roots=[store]),
    ],
)
def test_concrete_sdk_clients_reject_paths_outside_allowed_roots(tmp_path, client_factory):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with client_factory(store) as client:
        with pytest.raises(IngestSandboxError):
            client.submit_file(outside)


@pytest.mark.security
def test_sdk_facade_rejects_sensitive_dotenv_inside_allowed_root(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    secret = store / ".env"
    secret.write_text("SECRET=value\n", encoding="utf-8")

    with IngestClient(store_root=store, backend="auto") as client:
        with pytest.raises(IngestSandboxError):
            client.submit_file(secret)


@pytest.mark.security
def test_sdk_facade_rejects_symlink_escape(tmp_path):
    store = tmp_path / "store"
    outside_dir = tmp_path / "outside"
    store.mkdir()
    outside_dir.mkdir()
    outside = outside_dir / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = store / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation is not available: {error}")

    with IngestClient(store_root=store, backend="auto", allowed_roots=[store]) as client:
        with pytest.raises(IngestSandboxError):
            client.submit_file(link)


@pytest.mark.security
def test_sdk_url_sandbox_rejects_dangerous_urls(tmp_path):
    for url in [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "http://localhost:8000/",
        "http://127.0.0.1:8000/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://169.254.169.254/",
        "http://metadata.google.internal/",
    ]:
        for client in [
            CliIngestClient(store_root=tmp_path / "cli", command=python_cli_command(), cwd=REPO_ROOT),
            HttpIngestClient(client=TestClient(create_app(tmp_path / "http")), allowed_roots=[tmp_path / "http"]),
            QueueIngestClient(store_root=tmp_path / "queue"),
        ]:
            with pytest.raises(IngestSandboxError):
                client.submit_url(url)

    allowed = QueueIngestClient(store_root=tmp_path / "allowed").submit_url("https://example.com/article")
    assert allowed.status == "queued"


@pytest.mark.security
def test_service_url_endpoint_rejects_ssrf_hosts(tmp_path):
    client = TestClient(create_app(tmp_path / "store"))

    response = client.post("/v1/ingest/url", json={"url": "http://169.254.169.254/latest/meta-data"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SDK_SANDBOX_REJECTED"


def python_cli_command() -> list[str]:
    script = f"import sys; sys.path.insert(0, {str(SRC_ROOT)!r}); from swallow.cli.main import app; app()"
    return [sys.executable, "-c", script]
