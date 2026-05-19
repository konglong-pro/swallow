from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swallow.core.runner import IngestRunner
from swallow.core.queue_worker import QueueWorker
from swallow.sdk import (
    IngestClient,
    IngestFacadeRegistryError,
    IngestSdkConfigError,
    IngestSdkJobNotFound,
    IngestWaitTimeout,
    IngestUnsupportedCapability,
    create_ingest_client,
)
from swallow.service.api import create_app


pytestmark = [pytest.mark.contract, pytest.mark.transport]


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def test_facade_auto_file_uses_local_and_registry_survives(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Facade\n\n" + "hello swallow " * 40, encoding="utf-8")

    with IngestClient(store_root=store, backend="auto") as client:
        job = client.submit_file(sample)
        result = client.wait(job.job_id, timeout=5)

    assert result.status == "success"
    assert registry_backend(store, kind="job", item_id=job.job_id) == "local"

    with create_ingest_client(store_root=store, backend="auto") as client:
        restored = client.get_result(job.job_id)

    assert restored.status == "success"
    assert restored.outputs.markdown_path is not None
    assert (store / restored.outputs.markdown_path).exists()


def test_facade_auto_url_routes_to_cli_failed_result_without_network(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    config = store / "swallow.config.yaml"
    config.write_text(
        """
workers:
  firecrawl:
    enabled: false
  crawl4ai:
    enabled: false
  playwright:
    enabled: false
  playwright_profile:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    with IngestClient(
        store_root=store,
        backend="auto",
        config_path=config,
        cwd=REPO_ROOT,
        cli={"command": python_cli_command()},
    ) as client:
        result = client.url("https://example.com/article", content="none")

    assert result.status == "failed"
    assert result.errors
    assert result.errors[0].code == "WORKER_NOT_REGISTERED"
    assert registry_backend(store, kind="job", item_id=result.job_id) == "cli"


def test_facade_auto_batch_uses_queue(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    inputs = store / "inputs"
    inputs.mkdir()
    (inputs / "one.txt").write_text("# One\n\n" + "hello swallow " * 40, encoding="utf-8")
    (inputs / "two.txt").write_text("# Two\n\n" + "hello swallow " * 40, encoding="utf-8")

    with IngestClient(store_root=store, backend="auto") as client:
        batch = client.submit_batch([str(inputs / "*.txt")])
        assert batch.status == "queued"
        assert registry_backend(store, kind="batch", item_id=batch.batch_id) == "queue"

        assert QueueWorker(store_root=store).run_once()["status"] == "success"
        assert QueueWorker(store_root=store).run_once()["status"] == "success"
        result = client.wait_batch(batch.batch_id, timeout=5)

    assert result.status == "success"
    assert result.total == 2
    assert result.success == 2
    assert len(result.job_ids) == 2


def test_facade_explicit_cli_http_and_queue_smoke(tmp_path):
    cli_store = tmp_path / "cli-store"
    cli_store.mkdir()
    cli_sample = cli_store / "cli.txt"
    cli_sample.write_text("# CLI\n\n" + "hello swallow " * 40, encoding="utf-8")

    with IngestClient(
        store_root=cli_store,
        backend="cli",
        cwd=REPO_ROOT,
        cli={"command": python_cli_command()},
    ) as client:
        cli_result = client.file(cli_sample)

    assert cli_result.status == "success"
    assert registry_backend(cli_store, kind="job", item_id=cli_result.job_id) == "cli"

    http_store = tmp_path / "http-store"
    http_store.mkdir()
    http_sample = http_store / "http.txt"
    http_sample.write_text("# HTTP\n\n" + "hello swallow " * 40, encoding="utf-8")

    with IngestClient(
        store_root=http_store,
        backend="http",
        http={"client": TestClient(create_app(http_store))},
    ) as client:
        http_result = client.file(http_sample)

    assert http_result.status == "success"
    assert registry_backend(http_store, kind="job", item_id=http_result.job_id) == "http"

    queue_store = tmp_path / "queue-store"
    queue_store.mkdir()
    queue_sample = queue_store / "queue.txt"
    queue_sample.write_text("# Queue\n\n" + "hello swallow " * 40, encoding="utf-8")

    with IngestClient(store_root=queue_store, backend="queue") as client:
        queue_job = client.submit_file(queue_sample)
        assert queue_job.status == "queued"
        assert QueueWorker(store_root=queue_store).run_once()["status"] == "success"
        queue_result = client.wait(queue_job.job_id, timeout=5)

    assert queue_result.status == "success"
    assert registry_backend(queue_store, kind="job", item_id=queue_result.job_id) == "queue"


def test_facade_capabilities_and_unsupported_operations(tmp_path):
    client = IngestClient(store_root=tmp_path, backend="auto")
    capabilities = {capability.backend: capability for capability in client.capabilities()}

    assert set(capabilities) == {"local", "cli", "http", "queue"}
    assert capabilities["local"].inputs == ["file"]
    assert capabilities["queue"].supports_batch is True
    assert capabilities["queue"].requires_worker is True
    assert capabilities["http"].requires_service is True

    with pytest.raises(IngestUnsupportedCapability):
        IngestClient(store_root=tmp_path, backend="local").submit_url("https://example.com")
    with pytest.raises(IngestUnsupportedCapability):
        IngestClient(store_root=tmp_path, backend="auto", prefer="local").submit_url("https://example.com")
    with pytest.raises(IngestSdkConfigError):
        IngestClient(store_root=tmp_path, backend="mcp")
    with pytest.raises(IngestSdkConfigError):
        IngestClient(store_root=tmp_path, storage_mode="memory")


def test_facade_registry_errors_and_unknown_auto_job(tmp_path):
    store = tmp_path / "store"
    store.mkdir()

    with IngestClient(store_root=store, backend="auto") as client:
        with pytest.raises(IngestSdkJobNotFound):
            client.get_job("ing_missing")

    registry = store / "sdk" / "facade_registry.jsonl"
    registry.parent.mkdir(parents=True)
    registry.write_text("not json\n", encoding="utf-8")

    with IngestClient(store_root=store, backend="auto") as client:
        with pytest.raises(IngestFacadeRegistryError):
            client.get_job("ing_any")


@pytest.mark.concurrency
def test_facade_concurrent_submit_and_wait_uses_isolated_jobs(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    inputs = []
    for index in range(10):
        path = store / f"input-{index}.txt"
        path.write_text(f"# Input {index}\n\n" + "facade concurrency " * 30, encoding="utf-8")
        inputs.append(path)

    with IngestClient(store_root=store, backend="auto", local={"max_workers": 4}) as client:
        with ThreadPoolExecutor(max_workers=5) as executor:
            jobs = [future.result() for future in as_completed([executor.submit(client.submit_file, path) for path in inputs])]
        results = [client.wait(job.job_id, timeout=10) for job in jobs]

    assert len({job.job_id for job in jobs}) == 10
    assert all(result.status == "success" for result in results)
    assert len(list((store / "jobs").iterdir())) == 10
    assert all(registry_backend(store, kind="job", item_id=job.job_id) == "local" for job in jobs)


def test_facade_wait_timeout_does_not_mark_job_failed(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "slow.txt"
    sample.write_text("# Slow\n\n" + "timeout contract " * 40, encoding="utf-8")
    original_run_prepared_job = IngestRunner.run_prepared_job

    def delayed_run_prepared_job(self, prepared, *, plan_override=None):
        time.sleep(0.05)
        return original_run_prepared_job(self, prepared, plan_override=plan_override)

    monkeypatch.setattr(IngestRunner, "run_prepared_job", delayed_run_prepared_job)

    with IngestClient(store_root=store, backend="auto") as client:
        job = client.submit_file(sample)
        with pytest.raises(IngestWaitTimeout):
            client.wait(job.job_id, timeout=0.001, poll_interval=0.001)
        running = client.get_result(job.job_id, content="none")
        assert running.status in {"running", "success"}
        assert client.wait(job.job_id, timeout=5).status == "success"


def registry_backend(store: Path, *, kind: str, item_id: str) -> str:
    registry = store / "sdk" / "facade_registry.jsonl"
    records = [json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()]
    matches = [record["backend"] for record in records if record["kind"] == kind and record["id"] == item_id]
    assert matches
    return matches[-1]


def python_cli_command() -> list[str]:
    script = f"import sys; sys.path.insert(0, {str(SRC_ROOT)!r}); from swallow.cli.main import app; app()"
    return [sys.executable, "-c", script]
