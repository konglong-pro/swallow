from __future__ import annotations

import time

import pytest

from swallow.core.config import IngestConfig
from swallow.core.runner import IngestRunner
from swallow.sdk import (
    IngestSdkConfigError,
    IngestSdkInputError,
    IngestSdkJobNotFound,
    IngestWaitTimeout,
    LocalIngestClient,
)
from swallow.sdk.result_mapper import DEFAULT_FULL_CONTENT_BYTES, DEFAULT_PREVIEW_BYTES


pytestmark = [pytest.mark.contract, pytest.mark.local, pytest.mark.transport]


def test_local_sdk_submit_file_waits_for_success_and_artifacts(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("# Sample\n\n" + "hello swallow " * 40, encoding="utf-8")

    with LocalIngestClient(store_root=tmp_path) as client:
        job = client.submit_file(sample)
        assert job.job_id.startswith("ing_")
        assert job.status == "running"
        assert job.source_type == "file"
        assert job.trace_path.endswith("/trace.jsonl")

        result = client.wait(job.job_id, timeout=5)

    assert result.status == "success"
    assert result.outputs.markdown_path is not None
    assert result.outputs.document_json_path is not None
    assert result.outputs.trace_path is not None
    assert result.outputs.manifest_path is not None
    assert (tmp_path / result.outputs.markdown_path).exists()
    assert (tmp_path / result.outputs.document_json_path).exists()
    assert (tmp_path / result.outputs.trace_path).exists()
    assert (tmp_path / result.outputs.manifest_path).exists()
    assert result.document is not None
    assert result.document.raw_id is not None


def test_local_sdk_default_result_is_path_first_with_preview(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("# Preview\n\n" + "hello swallow " * 40, encoding="utf-8")

    with LocalIngestClient(store_root=tmp_path) as client:
        result = client.file(sample)

    assert result.status == "success"
    assert result.preview is not None
    assert "hello swallow" in result.preview.text
    assert result.content is None
    assert result.outputs.markdown_path is not None
    assert result.outputs.document_json_path is not None


def test_local_sdk_preview_and_full_content_are_capped(tmp_path):
    sample = tmp_path / "large.txt"
    sample.write_text("# Large\n\n" + ("x" * (DEFAULT_FULL_CONTENT_BYTES + 10_000)), encoding="utf-8")

    with LocalIngestClient(store_root=tmp_path) as client:
        preview_result = client.file(sample, content="preview")
        full_result = client.get_result(preview_result.job_id, content="full")

    assert preview_result.preview is not None
    assert preview_result.preview.truncated is True
    assert preview_result.preview.bytes_read == DEFAULT_PREVIEW_BYTES
    assert preview_result.content is None

    assert full_result.preview is None
    assert full_result.content is not None
    assert full_result.content.truncated is True
    assert full_result.content.bytes_read == DEFAULT_FULL_CONTENT_BYTES


def test_local_sdk_tool_failure_returns_failed_result(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello swallow", encoding="utf-8")
    config = IngestConfig.from_mapping({"workers": {"plain_text": {"enabled": False}}})

    with LocalIngestClient(store_root=tmp_path, config=config) as client:
        job = client.submit_file(sample)
        result = client.wait(job.job_id, timeout=5, content="none")

    assert result.status == "failed"
    assert result.preview is None
    assert result.errors
    assert result.errors[0].code == "WORKER_NOT_REGISTERED"
    assert result.outputs.trace_path is not None
    assert result.outputs.manifest_path is not None
    assert (tmp_path / result.outputs.trace_path).exists()
    assert (tmp_path / result.outputs.manifest_path).exists()


def test_local_sdk_invocation_failures_raise_exceptions(tmp_path, monkeypatch):
    with pytest.raises(IngestSdkConfigError):
        LocalIngestClient(store_root=tmp_path, storage_mode="memory")

    with LocalIngestClient(store_root=tmp_path) as client:
        with pytest.raises(IngestSdkInputError):
            client.submit_file(tmp_path / "missing.txt")
        with pytest.raises(IngestSdkJobNotFound):
            client.get_job("ing_missing")

    sample = tmp_path / "slow.txt"
    sample.write_text("# Slow\n\n" + "hello swallow " * 40, encoding="utf-8")
    original_run_prepared_job = IngestRunner.run_prepared_job

    def delayed_run_prepared_job(self, prepared, *, plan_override=None):
        time.sleep(0.05)
        return original_run_prepared_job(self, prepared, plan_override=plan_override)

    monkeypatch.setattr(IngestRunner, "run_prepared_job", delayed_run_prepared_job)

    with LocalIngestClient(store_root=tmp_path) as client:
        job = client.submit_file(sample)
        with pytest.raises(IngestWaitTimeout):
            client.wait(job.job_id, timeout=0.001, poll_interval=0.001)
        assert client.wait(job.job_id, timeout=5).status == "success"
