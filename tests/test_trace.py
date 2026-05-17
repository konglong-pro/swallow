from __future__ import annotations

import json

import pytest

from swallow.core.config import IngestConfig
from swallow.core.errors import IngestError
from swallow.core.runner import IngestRunner


def test_trace_contains_phase_one_events(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("trace me\n", encoding="utf-8")

    result = IngestRunner(store_root=tmp_path).ingest_file(sample)
    trace_path = tmp_path / result.trace_path
    events = [
        json.loads(line)["event"]
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]

    assert events == [
        "job_started",
        "raw_saved",
        "route_selected",
        "worker_started",
        "worker_finished",
        "worker_started",
        "worker_finished",
        "quality_checked",
        "worker_started",
        "worker_finished",
        "document_built",
        "document_written",
        "job_finished",
    ]


def test_trace_records_structured_job_failure(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("trace me\n", encoding="utf-8")
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})

    with pytest.raises(IngestError):
        IngestRunner(store_root=tmp_path, config=config).ingest_file(sample)

    job_dir = next((tmp_path / "jobs").iterdir())
    events = [json.loads(line) for line in (job_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    failure = events[-1]

    assert failure["event"] == "job_failed"
    assert failure["details"]["error"]["type"] == "WorkerError"
    assert failure["details"]["error"]["code"] == "WORKER_NOT_REGISTERED"
    assert failure["details"]["error"]["retryable"] is False
    assert failure["details"]["error"]["fallback_allowed"] is False


def test_trace_records_worker_params_hash_and_redacts_sensitive_values(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("trace params\n", encoding="utf-8")
    config = IngestConfig.from_mapping(
        {
            "workers": {
                "plain_text": {
                    "params": {
                        "custom": "visible",
                        "api_key": "secret-value",
                        "api_key_env": "SWALLOW_API_KEY",
                        "nested": {"password": "hidden", "mode": "fast"},
                    }
                }
            }
        }
    )

    result = IngestRunner(store_root=tmp_path, config=config).ingest_file(sample)

    events = [
        json.loads(line)
        for line in (tmp_path / result.trace_path).read_text(encoding="utf-8").splitlines()
    ]
    started = next(event for event in events if event["event"] == "worker_started" and event["worker"] == "plain_text_worker")
    finished = next(event for event in events if event["event"] == "worker_finished" and event["worker"] == "plain_text_worker")

    assert len(started["params_hash"]) == 64
    assert finished["params_hash"] == started["params_hash"]
    assert started["details"]["params"]["custom"] == "visible"
    assert started["details"]["params"]["api_key"] == "<redacted>"
    assert started["details"]["params"]["api_key_env"] == "SWALLOW_API_KEY"
    assert started["details"]["params"]["nested"]["password"] == "<redacted>"
    assert started["details"]["params"]["nested"]["mode"] == "fast"
    assert "secret-value" not in json.dumps(started, ensure_ascii=False)
    assert "hidden" not in json.dumps(finished, ensure_ascii=False)


def test_trace_worker_events_include_raw_input_and_timing_fields(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("trace fields\n", encoding="utf-8")

    result = IngestRunner(store_root=tmp_path).ingest_file(sample)

    events = [
        json.loads(line)
        for line in (tmp_path / result.trace_path).read_text(encoding="utf-8").splitlines()
    ]
    started = next(event for event in events if event["event"] == "worker_started" and event["worker"] == "plain_text_worker")
    finished = next(event for event in events if event["event"] == "worker_finished" and event["worker"] == "plain_text_worker")

    assert started["raw_id"] == result.raw.raw_id
    assert finished["raw_id"] == result.raw.raw_id
    assert started["input_path"].endswith("original.txt")
    assert finished["input_path"].endswith("original.txt")
    assert started["started_at"]
    assert finished["started_at"] == started["started_at"]
    assert finished["finished_at"]


def test_trace_records_fallback_selected_event(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from swallow.workers import firecrawl_worker as firecrawl_module
    from swallow.workers import crawl4ai_worker as crawl4ai_module

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

    events = [
        json.loads(line)
        for line in (tmp_path / result.trace_path).read_text(encoding="utf-8").splitlines()
    ]
    fallback = next(event for event in events if event["event"] == "fallback_selected")
    assert fallback["worker"] == "crawl4ai_worker"
    assert fallback["details"]["quality_score"] < 0.75
