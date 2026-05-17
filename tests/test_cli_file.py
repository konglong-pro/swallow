from __future__ import annotations

import json

from typer.testing import CliRunner

from swallow.cli.main import app


def test_cli_file_ingest_creates_two_jobs_for_same_raw(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")

    runner = CliRunner()
    first = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    second = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output

    jobs = sorted((tmp_path / "jobs").iterdir())
    assert len(jobs) == 2

    first_doc = json.loads((jobs[0] / "ingest_document.json").read_text(encoding="utf-8"))
    second_doc = json.loads((jobs[1] / "ingest_document.json").read_text(encoding="utf-8"))
    first_job = json.loads((jobs[0] / "job.json").read_text(encoding="utf-8"))

    assert first_doc["raw_id"] == second_doc["raw_id"]
    assert first_doc["job_id"] != second_doc["job_id"]
    assert first_job["status"] == "success"
    assert first_job["document_path"].startswith("jobs/")
    assert first_job["manifest_path"].startswith("jobs/")
    assert first_job["ingest_document_id"] == first_doc["id"]
    assert (jobs[0] / "document.md").exists()
    assert (jobs[0] / "manifest.json").exists()
    assert (jobs[0] / "trace.jsonl").exists()
    assert (jobs[0] / "intermediate").is_dir()
    assert (jobs[0] / "artifacts").is_dir()
    assert (jobs[0] / "logs").is_dir()
    assert "Document:" in first.output
    assert "Trace:" in first.output


def test_cli_file_ingest_uses_config(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    config = tmp_path / "swallow.config.yaml"
    config.write_text(
        """
workers:
  plain_text:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "--config", str(config), "file", str(sample)])

    assert result.exit_code == 1
    assert "Worker is not registered: plain_text_worker" in result.output


def test_cli_jobs_reports_no_jobs(tmp_path):
    result = CliRunner().invoke(app, ["--store", str(tmp_path), "jobs"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "No jobs found."


def test_cli_jobs_lists_successful_jobs_as_json(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    first = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    second = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output

    result = runner.invoke(app, ["--store", str(tmp_path), "jobs", "--json", "--limit", "1"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["status"] == "success"
    assert payload[0]["source_type"] == "file"
    assert payload[0]["original_filename"] == "sample.txt"
    assert payload[0]["primary_worker"] == "plain_text_worker"
    assert payload[0]["document_path"].startswith("jobs/")


def test_cli_jobs_lists_failed_jobs_from_trace(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    config = tmp_path / "swallow.config.yaml"
    config.write_text(
        """
workers:
  plain_text:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "--config", str(config), "file", str(sample)])
    assert ingest.exit_code == 1

    result = runner.invoke(app, ["--store", str(tmp_path), "jobs", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["status"] == "failed"
    assert payload[0]["source_type"] == "file"
    assert payload[0]["original_filename"] == "sample.txt"
    assert payload[0]["raw_id"].startswith("raw_")
    assert payload[0]["trace_path"].endswith("/trace.jsonl")
    failed_job = payload[0]
    job_json = json.loads((tmp_path / "jobs" / failed_job["job_id"] / "job.json").read_text(encoding="utf-8"))
    assert job_json["status"] == "failed"
    assert job_json["error"]["type"] == "WorkerError"
    assert job_json["error"]["code"] == "WORKER_NOT_REGISTERED"
    assert job_json["error"]["retryable"] is False
    assert job_json["error"]["fallback_allowed"] is False
    manifest = json.loads((tmp_path / "jobs" / failed_job["job_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["errors"][0]["code"] == "WORKER_NOT_REGISTERED"


def test_cli_inspect_failed_job_uses_job_json_and_trace_tail(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    config = tmp_path / "swallow.config.yaml"
    config.write_text(
        """
workers:
  plain_text:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "--config", str(config), "file", str(sample)])
    assert ingest.exit_code == 1
    failed_job = json.loads(runner.invoke(app, ["--store", str(tmp_path), "jobs", "--json"]).output)[0]["job_id"]

    result = runner.invoke(app, ["--store", str(tmp_path), "inspect", failed_job])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["job_id"] == failed_job
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "WORKER_NOT_REGISTERED"
    assert payload["trace_path"].endswith("/trace.jsonl")
    assert payload["trace_tail"][-1]["event"] == "job_failed"
    assert (tmp_path / "jobs" / failed_job / "manifest.json").exists()


def test_cli_inspect_can_include_raw_and_artifacts(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)
    artifact = tmp_path / "jobs" / job_id / "intermediate" / "debug.txt"
    artifact.write_text("debug artifact", encoding="utf-8")

    result = runner.invoke(app, ["--store", str(tmp_path), "inspect", job_id, "--raw", "--artifacts"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["raw"]["original_filename"] == "sample.txt"
    assert payload["raw"]["exists"] is True
    assert payload["raw"]["meta_exists"] is True
    assert payload["raw"]["absolute_path"].endswith("original.txt")
    assert payload["artifacts"] == [
        {
            "type": "intermediate_file",
            "source": "discovered",
            "path": "intermediate/debug.txt",
            "absolute_path": str(artifact.resolve()),
            "exists": True,
            "size_bytes": len("debug artifact"),
        }
    ]


def test_cli_raw_command_outputs_raw_metadata(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)

    json_result = runner.invoke(app, ["--store", str(tmp_path), "raw", job_id, "--json"])
    human_result = runner.invoke(app, ["--store", str(tmp_path), "raw", job_id])

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["original_filename"] == "sample.txt"
    assert payload["exists"] is True
    assert payload["absolute_path"].endswith("original.txt")
    assert human_result.exit_code == 0, human_result.output
    assert "Raw ID: raw_" in human_result.output
    assert "Absolute:" in human_result.output


def test_cli_artifacts_command_outputs_artifacts(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)
    artifact = tmp_path / "jobs" / job_id / "intermediate" / "debug.txt"
    artifact.write_text("debug artifact", encoding="utf-8")

    json_result = runner.invoke(app, ["--store", str(tmp_path), "artifacts", job_id, "--json"])
    human_result = runner.invoke(app, ["--store", str(tmp_path), "artifacts", job_id])

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload[0]["path"] == "intermediate/debug.txt"
    assert payload[0]["exists"] is True
    assert human_result.exit_code == 0, human_result.output
    assert "Type\tSource\tExists\tSize\tPath" in human_result.output
    assert "intermediate/debug.txt" in human_result.output


def test_cli_open_resolves_document_raw_and_artifact_paths(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)
    artifact = tmp_path / "jobs" / job_id / "intermediate" / "debug.txt"
    artifact.write_text("debug artifact", encoding="utf-8")

    document = runner.invoke(app, ["--store", str(tmp_path), "open", job_id, "--json"])
    raw = runner.invoke(app, ["--store", str(tmp_path), "open", job_id, "--target", "raw", "--json"])
    artifact_result = runner.invoke(
        app,
        ["--store", str(tmp_path), "open", job_id, "--target", "artifact", "--artifact", "1", "--json"],
    )

    assert document.exit_code == 0, document.output
    assert json.loads(document.output) == {
        "target": "document",
        "path": str((tmp_path / "jobs" / job_id / "document.md").resolve()),
        "exists": True,
    }
    assert raw.exit_code == 0, raw.output
    raw_payload = json.loads(raw.output)
    assert raw_payload["target"] == "raw"
    assert raw_payload["path"].endswith("original.txt")
    assert raw_payload["exists"] is True
    assert artifact_result.exit_code == 0, artifact_result.output
    artifact_payload = json.loads(artifact_result.output)
    assert artifact_payload["target"] == "artifact"
    assert artifact_payload["path"] == str(artifact.resolve())
    assert artifact_payload["artifact"]["path"] == "intermediate/debug.txt"


def test_cli_open_rejects_missing_artifact_selector(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)

    result = runner.invoke(app, ["--store", str(tmp_path), "open", job_id, "--target", "artifact"])

    assert result.exit_code != 0
    assert "No artifacts found" in result.output


def test_cli_trace_can_tail_events(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)

    result = runner.invoke(app, ["--store", str(tmp_path), "trace", job_id, "--tail", "2"])

    assert result.exit_code == 0, result.output
    events = [json.loads(line)["event"] for line in result.output.splitlines()]
    assert events == ["document_written", "job_finished"]


def test_cli_trace_can_emit_json_tail(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert ingest.exit_code == 0, ingest.output
    job_id = parse_job_id(ingest.output)

    result = runner.invoke(app, ["--store", str(tmp_path), "trace", job_id, "--json", "--tail", "1"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["event"] == "job_finished"


def test_cli_rerun_can_recover_failed_job_from_job_json(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    config = tmp_path / "swallow.config.yaml"
    config.write_text(
        """
workers:
  plain_text:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    ingest = runner.invoke(app, ["--store", str(tmp_path), "--config", str(config), "file", str(sample)])
    assert ingest.exit_code == 1
    failed_job = json.loads(runner.invoke(app, ["--store", str(tmp_path), "jobs", "--json"]).output)[0]["job_id"]

    rerun = runner.invoke(app, ["--store", str(tmp_path), "rerun", failed_job])

    assert rerun.exit_code == 0, rerun.output
    rerun_job = parse_job_id(rerun.output)
    rerun_metadata = json.loads((tmp_path / "jobs" / rerun_job / "job.json").read_text(encoding="utf-8"))
    assert rerun_metadata["status"] == "success"
    assert rerun_metadata["raw_id"].startswith("raw_")
    assert rerun_metadata["original_filename"] == "sample.txt"


def test_cli_rerun_reuses_existing_raw_and_creates_new_job(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    first = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert first.exit_code == 0, first.output
    first_job = parse_job_id(first.output)

    rerun = runner.invoke(app, ["--store", str(tmp_path), "rerun", first_job])

    assert rerun.exit_code == 0, rerun.output
    second_job = parse_job_id(rerun.output)
    assert second_job != first_job
    jobs = sorted((tmp_path / "jobs").iterdir())
    assert len(jobs) == 2

    first_doc = json.loads((tmp_path / "jobs" / first_job / "ingest_document.json").read_text(encoding="utf-8"))
    second_doc = json.loads((tmp_path / "jobs" / second_job / "ingest_document.json").read_text(encoding="utf-8"))
    assert first_doc["raw_id"] == second_doc["raw_id"]
    assert second_doc["provenance"]["primary_worker"] == "plain_text_worker"
    assert (tmp_path / "jobs" / second_job / "trace.jsonl").exists()


def test_cli_rerun_can_force_worker(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    first = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert first.exit_code == 0, first.output

    rerun = runner.invoke(app, ["--store", str(tmp_path), "rerun", parse_job_id(first.output), "--worker", "plain_text_worker"])

    assert rerun.exit_code == 0, rerun.output
    doc = json.loads((tmp_path / "jobs" / parse_job_id(rerun.output) / "ingest_document.json").read_text(encoding="utf-8"))
    assert doc["provenance"]["worker_chain"] == [
        "plain_text_worker@0.1.0",
        "quality_checker@0.1.0",
        "markdown_normalizer@0.1.0",
    ]


def test_cli_rerun_reports_missing_job(tmp_path):
    result = CliRunner().invoke(app, ["--store", str(tmp_path), "rerun", "ing_missing"])

    assert result.exit_code == 1
    assert "Rerun failed: JOB_NOT_FOUND: Job not found: ing_missing" in result.output


def test_cli_rerun_reports_unknown_forced_worker_without_new_job(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    first = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert first.exit_code == 0, first.output

    result = runner.invoke(app, ["--store", str(tmp_path), "rerun", parse_job_id(first.output), "--worker", "missing_worker"])

    assert result.exit_code == 1
    assert "Rerun failed: WORKER_NOT_REGISTERED: Worker is not registered: missing_worker" in result.output
    assert len(list((tmp_path / "jobs").iterdir())) == 1


def test_cli_rerun_rejects_incompatible_forced_worker_without_new_job(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow\n", encoding="utf-8")
    runner = CliRunner()
    first = runner.invoke(app, ["--store", str(tmp_path), "file", str(sample)])
    assert first.exit_code == 0, first.output

    result = runner.invoke(app, ["--store", str(tmp_path), "rerun", parse_job_id(first.output), "--worker", "firecrawl_worker"])

    assert result.exit_code == 1
    assert "Rerun failed: WORKER_CANNOT_HANDLE: Worker cannot handle source: firecrawl_worker for file sample.txt" in result.output
    assert len(list((tmp_path / "jobs").iterdir())) == 1


def parse_job_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Job: "):
            return line.removeprefix("Job: ").strip()
    raise AssertionError(f"No job id in output:\n{output}")
