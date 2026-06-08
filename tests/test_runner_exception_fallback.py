from __future__ import annotations

import json
import time

from swallow.core.config import IngestConfig
from swallow.core.models import WorkerInput, WorkerResult
from swallow.core.registry import WorkerRegistry
from swallow.core.runner import IngestRunner
from swallow.workers.base import BaseWorker


def test_runner_uses_later_fallback_after_timeout(tmp_path):
    registry = WorkerRegistry()
    registry.register(TimeoutURLWorker())
    registry.register(FallbackURLWorker())
    config = IngestConfig.from_mapping({"workers": {"timeout_url": {"params": {"timeout_seconds": 0.01}}}})
    runner = IngestRunner(store_root=tmp_path / "store", registry=registry, config=config, router=StaticRouter())

    result = runner.ingest_url("https://example.com/article")

    assert result.document.provenance.primary_worker == "fallback_url_worker"
    assert "timeout_url_worker@0.1.0" in result.document.provenance.worker_chain
    assert "fallback_url_worker@0.1.0" in result.document.provenance.worker_chain
    trace_events = [
        json.loads(line)
        for line in (tmp_path / "store" / result.trace_path).read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "worker_failed" and event["worker"] == "timeout_url_worker" for event in trace_events)
    fallback = next(event for event in trace_events if event["event"] == "fallback_selected")
    assert fallback["worker"] == "fallback_url_worker"
    assert fallback["details"]["quality_score"] == 0.0


class StaticRouter:
    def route(self, input: WorkerInput) -> list[str]:
        return ["timeout_url_worker", "quality_checker", "fallback:fallback_url_worker", "markdown_normalizer"]


class TimeoutURLWorker(BaseWorker):
    name = "timeout_url_worker"
    version = "0.1.0"

    def can_handle(self, input: WorkerInput) -> bool:
        return True

    def run(self, input: WorkerInput) -> WorkerResult:
        time.sleep(0.2)
        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown="late",
        )


class FallbackURLWorker(BaseWorker):
    name = "fallback_url_worker"
    version = "0.1.0"

    def can_handle(self, input: WorkerInput) -> bool:
        return True

    def run(self, input: WorkerInput) -> WorkerResult:
        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown="# Fallback\n\n" + "Fallback content. " * 40,
            metadata={"source_url": input.source_url},
        )
