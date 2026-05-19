from __future__ import annotations

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.errors import SystemResourceError
from swallow.core.queue_store import QueueStore
from swallow.core.queue_worker import QueueWorker
from swallow.core.runner import IngestRunner
from swallow.sdk import QueueIngestClient


def test_queue_worker_retries_retryable_failure_with_same_job_id(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "retry.txt"
    sample.write_text("# Retry\n\n" + "hello swallow " * 40, encoding="utf-8")
    calls = {"count": 0}
    original = IngestRunner.run_prepared_job

    def flaky_run(self, prepared, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise SystemResourceError("temporary resource pressure", code="TEMPORARY_RESOURCE_PRESSURE")
        return original(self, prepared, *args, **kwargs)

    monkeypatch.setattr(IngestRunner, "run_prepared_job", flaky_run)

    with QueueIngestClient(store_root=store, max_attempts=3) as client:
        job = client.submit_file(sample)
        first = QueueWorker(store_root=store).run_once()
        second = QueueWorker(store_root=store).run_once()
        third = QueueWorker(store_root=store).run_once()
        item = QueueStore(store).get_item(job.job_id)
        result = client.get_result(job.job_id)

    assert first["status"] == "queued"
    assert second["status"] == "queued"
    assert third["status"] == "success"
    assert item.job_id == job.job_id
    assert item.attempts == 3
    assert result.status == "success"


def test_queue_worker_non_retryable_failure_does_not_retry(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Failure\n\n" + "hello swallow " * 40, encoding="utf-8")
    config = store / "swallow.config.yaml"
    config.write_text(
        """
workers:
  plain_text:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    with QueueIngestClient(store_root=store, max_attempts=3) as client:
        job = client.submit_file(sample)
        result = CliRunner().invoke(
            app,
            ["--store", str(store), "--config", str(config), "queue", "worker", "--once", "--json"],
        )
        item = QueueStore(store).get_item(job.job_id)
        ingest = client.get_result(job.job_id, content="none")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert item.attempts == 1
    assert ingest.status == "failed"
    assert ingest.errors[0].code == "WORKER_NOT_REGISTERED"


def test_queue_worker_recovers_stale_lease(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "lease.txt"
    sample.write_text("# Lease\n\n" + "hello swallow " * 40, encoding="utf-8")

    with QueueIngestClient(store_root=store, max_attempts=3) as client:
        job = client.submit_file(sample)
        queue = QueueStore(store)
        claimed = queue.claim_next(worker_id="stale-worker", lease_seconds=0.1)
        assert claimed is not None
        time.sleep(0.2)

        recovered = queue.recover_stale_leases()
        item = queue.get_item(job.job_id)
        worker_result = QueueWorker(store_root=store).run_once()
        result = client.get_result(job.job_id)

    assert recovered == 1
    assert item.status == "queued"
    assert worker_result["status"] == "success"
    assert result.status == "success"


def test_queue_cancel_running_job_prevents_retry(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "cancel-running.txt"
    sample.write_text("# Cancel\n\n" + "hello swallow " * 40, encoding="utf-8")

    with QueueIngestClient(store_root=store, max_attempts=3) as client:
        job = client.submit_file(sample)
        queue = QueueStore(store)
        claimed = queue.claim_next(worker_id="worker", lease_seconds=60)
        assert claimed is not None
        client.cancel_job(job.job_id)

        status = queue.fail_or_retry_job(
            job.job_id,
            SystemResourceError("would otherwise retry", code="WOULD_RETRY"),
        )
        item = queue.get_item(job.job_id)

    assert status == "canceled"
    assert item.status == "canceled"
    assert item.attempts == 1


def test_queue_cli_stats_and_cancel(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "cancel.txt"
    sample.write_text("# Cancel\n\n" + "hello swallow " * 40, encoding="utf-8")

    with QueueIngestClient(store_root=store) as client:
        job = client.submit_file(sample)

    stats = CliRunner().invoke(app, ["--store", str(store), "queue", "stats", "--json"])
    cancel = CliRunner().invoke(app, ["--store", str(store), "queue", "cancel", job.job_id, "--json"])
    idle = CliRunner().invoke(app, ["--store", str(store), "queue", "worker", "--once", "--json"])

    assert stats.exit_code == 0, stats.output
    assert json.loads(stats.output)["jobs"]["queued"] == 1
    assert cancel.exit_code == 0, cancel.output
    assert json.loads(cancel.output)["status"] == "failed"
    assert idle.exit_code == 0, idle.output
    assert json.loads(idle.output)["status"] == "idle"
