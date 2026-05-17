from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from swallow.core.config import IngestConfig
from swallow.core.errors import IngestError
from swallow.core.runner import IngestRunner
from swallow.workers import crawl4ai_worker as crawl4ai_module
from swallow.workers import firecrawl_worker as firecrawl_module


def test_p0_successful_ingest_has_required_release_artifacts(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("# Acceptance\n\nhello swallow " * 40, encoding="utf-8")

    result = IngestRunner(store_root=tmp_path).ingest_file(sample)
    job_dir = tmp_path / "jobs" / result.job.id
    raw_dir = tmp_path / "raw_store" / result.raw.sha256

    assert (raw_dir / "original.txt").read_bytes() == sample.read_bytes()
    assert (raw_dir / "original.meta.json").exists()
    assert (job_dir / "document.md").exists()
    assert (job_dir / "ingest_document.json").exists()
    assert (job_dir / "trace.jsonl").exists()
    assert (job_dir / "manifest.json").exists()
    assert (job_dir / "intermediate").is_dir()
    assert (job_dir / "artifacts").is_dir()
    assert (job_dir / "logs").is_dir()

    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["job_id"] == result.job.id
    assert manifest["status"] == "success"
    assert manifest["raw_id"] == result.raw.raw_id
    assert manifest["input"]["sha256"] == result.raw.sha256
    assert manifest["outputs"]["markdown"] == result.document_path
    assert manifest["outputs"]["json"] == result.ingest_document_path
    assert manifest["outputs"]["trace"] == result.trace_path
    assert manifest["outputs"]["manifest"] == result.manifest_path
    assert manifest["route"] == ["plain_text_worker", "quality_checker", "markdown_normalizer"]
    assert [worker["worker"] for worker in manifest["workers"]] == [
        "plain_text_worker",
        "quality_checker",
        "markdown_normalizer",
    ]

    document = (job_dir / "document.md").read_text(encoding="utf-8")
    assert document.startswith("---\n")
    assert "ingest_document_id:" in document
    assert "worker_chain:" in document

    events = [json.loads(line)["event"] for line in (job_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "job_started" in events
    assert "raw_saved" in events
    assert "route_selected" in events
    assert "worker_started" in events
    assert "worker_finished" in events
    assert "quality_checked" in events
    assert "document_written" in events
    assert "job_finished" in events


def test_p0_raw_store_dedupes_by_content_not_filename(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    changed = tmp_path / "first-copy.txt"
    first.write_text("same content", encoding="utf-8")
    second.write_text("same content", encoding="utf-8")
    changed.write_text("different content", encoding="utf-8")

    runner = IngestRunner(store_root=tmp_path / "store")
    first_result = runner.ingest_file(first)
    second_result = runner.ingest_file(second)
    changed_result = runner.ingest_file(changed)

    assert first_result.raw.raw_id == second_result.raw.raw_id
    assert first_result.raw.sha256 == second_result.raw.sha256
    assert first_result.job.id != second_result.job.id
    assert changed_result.raw.sha256 != first_result.raw.sha256
    assert len(list((tmp_path / "store" / "raw_store").iterdir())) == 2


def test_p0_failed_ingest_has_manifest_trace_and_structured_error(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow", encoding="utf-8")
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})

    with pytest.raises(IngestError):
        IngestRunner(store_root=tmp_path / "store", config=config).ingest_file(sample)

    job_dir = next((tmp_path / "store" / "jobs").iterdir())
    assert (job_dir / "job.json").exists()
    assert (job_dir / "trace.jsonl").exists()
    assert (job_dir / "manifest.json").exists()
    assert not (job_dir / "document.md").exists()

    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["errors"][0]["code"] == "WORKER_NOT_REGISTERED"
    events = [json.loads(line)["event"] for line in (job_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1] == "job_failed"


def test_p0_fallback_is_explicitly_traceable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "too short",
            "html": "<html></html>",
            "metadata": {"title": "Short", "statusCode": 200},
        },
    )
    monkeypatch.setattr(
        crawl4ai_module,
        "crawl4ai_fetch",
        lambda url, **_: SimpleNamespace(
            markdown="# Fallback\n\n" + "Fallback content. " * 40,
            html="<html><body>Fallback content</body></html>",
            metadata={"title": "Fallback", "status_code": 200},
        ),
    )

    result = IngestRunner(store_root=tmp_path).ingest_url("https://example.com/article")

    job_dir = tmp_path / "jobs" / result.job.id
    events = [json.loads(line) for line in (job_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    fallback = next(event for event in events if event["event"] == "fallback_selected")
    assert fallback["worker"] == "crawl4ai_worker"
    assert result.document.provenance.primary_worker == "crawl4ai_worker"

    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "fallback:crawl4ai_worker" in manifest["route"]
    assert any(worker["worker"] == "firecrawl_worker" for worker in manifest["workers"])
    assert any(worker["worker"] == "crawl4ai_worker" for worker in manifest["workers"])
