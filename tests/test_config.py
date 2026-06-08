from __future__ import annotations

import json
import time
from pathlib import Path

from swallow.core.config import IngestConfig, load_config
from swallow.core.errors import SystemResourceError, WorkerTimeoutError
from swallow.core.models import WorkerInput, WorkerResult
from swallow.core.registry import WorkerRegistry, default_registry
from swallow.core.runner import IngestRunner
from swallow.workers.base import BaseWorker


def test_load_config_merges_worker_defaults(tmp_path):
    config_path = tmp_path / "swallow.config.yaml"
    config_path.write_text(
        """
workers:
  plain_text:
    enabled: false
  faster_whisper:
    model: tiny
    device: cpu
limits:
  max_file_size_bytes: 12
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.worker_enabled("plain_text_worker") is False
    assert config.worker_enabled("markitdown_worker") is True
    assert config.worker_params("faster_whisper_worker")["model"] == "tiny"
    assert config.worker_params("faster_whisper_worker")["device"] == "cpu"
    assert config.worker_params("faster_whisper_worker")["compute_type"] == "default"
    assert config.worker_params("faster_whisper_worker")["timeout_seconds"] == 1800
    assert config.limits.max_file_size_bytes == 12
    assert config.limits.max_pdf_size_bytes > config.limits.max_file_size_bytes


def test_registry_skips_disabled_workers():
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})

    registry = default_registry(config)

    assert registry.has("plain_text_worker") is False
    assert registry.has("markitdown_worker") is True


def test_opt_in_platform_workers_are_disabled_by_default():
    registry = default_registry(IngestConfig())

    assert registry.has("youtube_asr_worker") is False


def test_opt_in_platform_workers_register_when_enabled():
    config = IngestConfig.from_mapping({"workers": {"youtube_asr": {"enabled": True}}})
    registry = default_registry(config)

    assert registry.has("youtube_asr_worker") is True


def test_runner_injects_worker_config_params(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("hello\n", encoding="utf-8")
    registry = WorkerRegistry()
    registry.register(MetadataEchoWorker())
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"params": {"custom": "configured"}}}})

    result = IngestRunner(store_root=tmp_path / "store", registry=registry, config=config).ingest_file(source)

    assert result.document.content.markdown.strip() == "configured"


def test_runner_times_out_worker_from_config_params(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("hello\n", encoding="utf-8")
    registry = WorkerRegistry()
    registry.register(SlowWorker())
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"params": {"timeout_seconds": 0.01}}}})

    try:
        IngestRunner(store_root=tmp_path / "store", registry=registry, config=config).ingest_file(source)
    except WorkerTimeoutError as error:
        assert error.code == "WORKER_TIMEOUT"
    else:
        raise AssertionError("Expected WorkerTimeoutError")

    job_dir = next((tmp_path / "store" / "jobs").iterdir())
    metadata = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "failed"
    assert metadata["error"]["code"] == "WORKER_TIMEOUT"
    assert metadata["error"]["retryable"] is True
    assert metadata["error"]["fallback_allowed"] is True
    events = [json.loads(line) for line in (job_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-2]["event"] == "worker_failed"
    assert events[-2]["details"]["error"]["code"] == "WORKER_TIMEOUT"


def test_runner_rejects_files_over_sync_size_limit(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("too large\n", encoding="utf-8")
    config = IngestConfig.from_mapping({"limits": {"max_file_size_bytes": 4}})

    try:
        IngestRunner(store_root=tmp_path / "store", config=config).ingest_file(source)
    except SystemResourceError as error:
        assert error.code == "INPUT_TOO_LARGE_SYNC"
        assert "exceeds synchronous file limit" in str(error)
    else:
        raise AssertionError("Expected SystemResourceError")

    assert not (tmp_path / "store" / "raw_store").exists()


class MetadataEchoWorker(BaseWorker):
    name = "plain_text_worker"
    version = "0.1.0"

    def can_handle(self, input: WorkerInput) -> bool:
        return True

    def run(self, input: WorkerInput) -> WorkerResult:
        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=str(input.metadata["custom"]),
        )


class SlowWorker(BaseWorker):
    name = "plain_text_worker"
    version = "0.1.0"

    def can_handle(self, input: WorkerInput) -> bool:
        return True

    def run(self, input: WorkerInput) -> WorkerResult:
        time.sleep(0.2)
        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown="slow",
        )
