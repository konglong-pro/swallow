from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from swallow.cli.main import app

pytestmark = pytest.mark.batch


def test_cli_batch_writes_summary_and_continues_after_failed_file(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "good.txt").write_text("# Good\n\n" + "hello swallow " * 40, encoding="utf-8")
    (inputs / "partial.txt").write_text("short\n", encoding="utf-8")
    (inputs / "unsupported.bin").write_bytes(b"\x00\x01\x02")

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "batch", str(inputs / "*")])

    assert result.exit_code == 2, result.output
    summary_path = parse_output_path(result.output, "Summary")
    trace_path = parse_output_path(result.output, "Trace")
    assert summary_path.startswith("batch_runs/")
    assert trace_path.startswith("batch_runs/")
    summary = json.loads((tmp_path / summary_path).read_text(encoding="utf-8"))
    assert summary["status"] == "partial"
    assert summary["total"] == 3
    assert summary["success"] == 1
    assert summary["partial"] == 1
    assert summary["failed"] == 1

    jobs_by_status = {job["status"]: job for job in summary["jobs"]}
    assert jobs_by_status["success"]["job_id"].startswith("ing_")
    assert jobs_by_status["success"]["document"].startswith("jobs/")
    assert jobs_by_status["partial"]["job_id"].startswith("ing_")
    assert jobs_by_status["failed"]["job_id"].startswith("ing_")
    assert jobs_by_status["failed"]["error"]["code"] == "INPUT_ERROR"
    for job in summary["jobs"]:
        assert job["manifest"].startswith("jobs/")
        assert job["trace"].startswith("jobs/")
        assert (tmp_path / job["manifest"]).exists()
        assert (tmp_path / job["trace"]).exists()
        if job["status"] != "failed":
            assert (tmp_path / job["document"]).exists()

    trace_events = [json.loads(line)["event"] for line in (tmp_path / trace_path).read_text(encoding="utf-8").splitlines()]
    assert trace_events[0] == "batch_started"
    assert "input_failed" in trace_events
    assert trace_events[-1] == "batch_finished"


def test_cli_batch_concurrent_jobs_have_unique_job_dirs(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for index in range(8):
        (inputs / f"file-{index}.txt").write_text("# File\n\n" + f"content {index} " * 40, encoding="utf-8")

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "batch", str(inputs), "--workers", "4", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = json.loads((tmp_path / payload["summary_path"]).read_text(encoding="utf-8"))
    assert summary["status"] == "success"
    assert summary["total"] == 8
    assert summary["success"] == 8
    job_ids = [job["job_id"] for job in summary["jobs"]]
    assert len(job_ids) == len(set(job_ids))
    for job_id in job_ids:
        assert (tmp_path / "jobs" / job_id).is_dir()


def test_cli_batch_no_matches_writes_failed_summary(tmp_path):
    result = CliRunner().invoke(app, ["--store", str(tmp_path), "batch", str(tmp_path / "missing" / "*")])

    assert result.exit_code == 1, result.output
    summary_path = parse_output_path(result.output, "Summary")
    summary = json.loads((tmp_path / summary_path).read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["total"] == 0
    assert summary["errors"][0]["code"] == "NO_BATCH_INPUTS"
    assert summary["warnings"][0].startswith("batch_pattern_matched_no_files:")


def parse_output_path(output: str, label: str) -> str:
    prefix = f"{label}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    raise AssertionError(f"No {label} path in output:\n{output}")
