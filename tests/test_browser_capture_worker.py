from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.models import WorkerInput
from swallow.core.runner import IngestRunner
from swallow.workers.browser_capture_worker import BROWSER_CAPTURE_INVALID, BrowserCaptureWorker, load_browser_capture


def make_input(path: Path, job_dir: Path) -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(path),
        mime_type="application/json",
        source_type="browser_capture",
        metadata={"original_filename": path.name, "job_dir": str(job_dir)},
    )


def test_browser_capture_worker_returns_conversation_markdown_and_raw_dom_artifact(tmp_path):
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(sample_capture(), ensure_ascii=False), encoding="utf-8")
    job_dir = tmp_path / "job"

    result = BrowserCaptureWorker().run(make_input(capture, job_dir))

    assert result.status == "success"
    assert result.title == "Swallow design"
    assert result.metadata["platform"] == "chatgpt"
    assert result.metadata["message_count"] == 2
    assert "# Swallow design" in result.markdown
    assert "## 1. user" in result.markdown
    assert "Build the ingest core." in result.markdown
    assert "## 2. assistant" in result.markdown
    assert result.artifacts == [{"type": "raw_dom", "path": "intermediate/browser_capture/raw_dom.html"}]
    assert (job_dir / "intermediate" / "browser_capture" / "raw_dom.html").exists()


def test_browser_capture_loader_validates_required_schema(tmp_path):
    capture = tmp_path / "capture.json"
    payload = sample_capture()
    payload.pop("title")
    capture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    try:
        load_browser_capture(capture)
    except Exception as error:
        assert "title" in str(error)
    else:
        raise AssertionError("Expected schema validation to fail")


def test_browser_capture_worker_reports_schema_error(tmp_path):
    capture = tmp_path / "capture.json"
    payload = sample_capture()
    payload["messages"] = [{"role": "", "content": "missing role"}]
    capture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = BrowserCaptureWorker().run(make_input(capture, tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == BROWSER_CAPTURE_INVALID
    assert result.errors[0].startswith("browser_capture_invalid:")


def test_browser_capture_ingest_end_to_end(tmp_path):
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(sample_capture(), ensure_ascii=False), encoding="utf-8")

    result = IngestRunner(store_root=tmp_path / "store").ingest_browser_capture(capture)

    assert result.document.source.source_type == "browser_capture"
    assert result.document.provenance.primary_worker == "browser_capture_worker"
    assert "browser_capture_worker@0.1.0" in result.document.provenance.worker_chain
    assert "Build the ingest core." in result.document.content.markdown
    assert (tmp_path / "store" / result.document_path).exists()


def test_browser_capture_ingest_invalid_schema_writes_structured_error(tmp_path):
    capture = tmp_path / "capture.json"
    payload = sample_capture()
    payload["messages"] = "not a list"
    capture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    try:
        IngestRunner(store_root=tmp_path / "store").ingest_browser_capture(capture)
    except Exception as error:
        assert getattr(error, "code", None) == BROWSER_CAPTURE_INVALID
    else:
        raise AssertionError("Expected ingest to fail")

    job = next((tmp_path / "store" / "jobs").iterdir())
    job_json = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert job_json["status"] == "failed"
    assert job_json["error"]["code"] == BROWSER_CAPTURE_INVALID


def test_cli_browser_capture_ingest(tmp_path):
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(sample_capture(), ensure_ascii=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["--store", str(tmp_path / "store"), "browser-capture", str(capture)])

    assert result.exit_code == 0, result.output
    assert "Status: success" in result.output
    jobs = list((tmp_path / "store" / "jobs").iterdir())
    assert len(jobs) == 1
    assert (jobs[0] / "document.md").exists()
    assert (jobs[0] / "ingest_document.json").exists()
    assert (jobs[0] / "trace.jsonl").exists()


def test_cli_browser_capture_reports_schema_error(tmp_path):
    capture = tmp_path / "capture.json"
    payload = sample_capture()
    payload["url"] = ""
    capture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["--store", str(tmp_path / "store"), "browser-capture", str(capture)])

    assert result.exit_code == 1
    assert f"Ingest failed: {BROWSER_CAPTURE_INVALID}:" in result.output
    assert "Traceback" not in result.output


def sample_capture() -> dict:
    return {
        "platform": "chatgpt",
        "url": "https://chatgpt.com/c/example",
        "title": "Swallow design",
        "captured_at": "2026-05-14T10:30:00-07:00",
        "messages": [
            {"role": "user", "content": "Build the ingest core."},
            {"role": "assistant", "content": "RawStore, Trace, WorkerResult, and Markdown."},
        ],
        "raw_dom": "<html><body>capture</body></html>",
    }
