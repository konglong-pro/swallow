from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from swallow.capability import (
    ArtifactRef,
    CapabilityBatch,
    CapabilityConfigError,
    CapabilityPolicyError,
    CapabilityResult,
    ProviderManifest,
    SwallowCapabilityProvider,
)
from swallow.capability.schemas import CapabilityResult as CapabilityResultModel


pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def test_provider_discover_default_profile_and_capability_availability(tmp_path):
    provider = make_provider(tmp_path / "store")

    manifest = provider.discover()

    assert manifest.provider_id == "swallow"
    assert manifest.profile == "local_isolated"
    assert {capability.capability_id for capability in manifest.capabilities} == {
        "swallow.ingest.file",
        "swallow.ingest.url",
        "swallow.ingest.browser_capture",
        "swallow.ingest.archive",
        "swallow.ingest.batch",
    }
    assert "swallow.job.get" not in json.dumps(manifest.model_dump(mode="json"))
    assert "paddleocr_worker" not in json.dumps(manifest.model_dump(mode="json"))

    availability = {capability.capability_id: capability.available for capability in manifest.capabilities}
    assert availability["swallow.ingest.file"] is True
    assert availability["swallow.ingest.browser_capture"] is True
    assert availability["swallow.ingest.archive"] is True
    assert availability["swallow.ingest.url"] is False
    assert availability["swallow.ingest.batch"] is False

    url_capability = next(capability for capability in manifest.capabilities if capability.capability_id == "swallow.ingest.url")
    assert url_capability.supported is True
    assert url_capability.unavailable_reason == "url_disabled_by_profile"
    batch_capability = next(
        capability for capability in manifest.capabilities if capability.capability_id == "swallow.ingest.batch"
    )
    assert batch_capability.unavailable_reason == "batch_disabled_by_profile"


def test_provider_schema_and_manifest_snapshots_match_generated_output(tmp_path):
    provider = make_provider(tmp_path / "store")

    generated = {
        "schemas/capability-manifest.schema.json": ProviderManifest.model_json_schema(),
        "schemas/capability-result.schema.json": CapabilityResultModel.model_json_schema(),
        "schemas/artifact-ref.schema.json": ArtifactRef.model_json_schema(),
        "swallow.capabilities.json": provider.discover().model_dump(mode="json", by_alias=True, exclude_none=True),
    }

    for relative_path, payload in generated.items():
        expected = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
        assert payload == expected


def test_provider_artifact_generation_script_check_passes():
    result = subprocess.run(
        [sys.executable, "scripts/generate_capability_artifacts.py", "--check"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "up to date" in result.stdout


def test_provider_rejects_unknown_policy_keys(tmp_path):
    with pytest.raises(CapabilityConfigError):
        make_provider(tmp_path / "store", policy={"agent_selects_backend": True})


def test_provider_doctor_reports_missing_service_url_for_service_profile(tmp_path):
    provider = make_provider(tmp_path / "store", profile="service")

    result = asyncio.run(provider.doctor())

    assert result.profile == "service"
    assert result.status == "missing"
    assert any(check.name == "service_url" and check.status == "missing" for check in result.checks)


def test_provider_policy_denial_happens_before_job_creation(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Denied\n\nhello swallow", encoding="utf-8")
    provider = make_provider(store, policy={"allow_filesystem_read": False})

    plan = asyncio.run(
        provider.plan(
            {
                "capability_id": "swallow.ingest.file",
                "input": {"source_path": str(sample)},
            }
        )
    )

    assert plan.status == "requires_permission"
    assert "allow_filesystem_read" in plan.required_permissions
    with pytest.raises(CapabilityPolicyError):
        asyncio.run(
            provider.submit(
                {
                    "capability_id": "swallow.ingest.file",
                    "input": {"source_path": str(sample)},
                }
            )
        )
    assert not (store / "jobs").exists()


def test_local_light_profile_uses_local_backend(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Local\n\n" + "local provider " * 40, encoding="utf-8")
    provider = make_provider(store, profile="local_light")

    job = asyncio.run(
        provider.submit(
            {
                "capability_id": "swallow.ingest.file",
                "input": {"source_path": str(sample)},
            }
        )
    )
    result = asyncio.run(provider.wait(job.job_id, {"timeout": 5, "poll_interval": 0.01}))

    assert job.backend == "local"
    assert result.status == "success"
    assert result.provenance.backend == "local"


def test_provider_submit_wait_result_artifact_and_cancel(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Provider\n\n" + "hello provider " * 80, encoding="utf-8")
    provider = make_provider(store)

    job = asyncio.run(
        provider.submit(
            {
                "capability_id": "swallow.ingest.file",
                "input": {"source_path": str(sample)},
            }
        )
    )

    assert job.provider_id == "swallow"
    assert job.capability_id == "swallow.ingest.file"
    assert job.backend == "cli"
    assert job.profile == "local_isolated"
    assert job.status == "running"
    assert (store / "sdk" / "capability_provider_registry.jsonl").exists()

    result = asyncio.run(provider.wait(job.job_id, {"timeout": 10, "poll_interval": 0.01, "content_mode": "preview"}))

    assert isinstance(result, CapabilityResult)
    assert result.status == "success"
    assert result.summary.title == "Ingest completed"
    assert result.provenance.backend == "cli"
    assert result.provenance.profile == "local_isolated"
    assert result.provenance.input_sha256
    assert result.outputs.primary is not None
    assert result.outputs.primary.kind == "markdown_candidate"
    assert result.outputs.primary.preview is not None
    assert "hello provider" in result.outputs.primary.preview

    artifacts = {artifact.kind: artifact for artifact in result.artifacts}
    assert artifacts["markdown_candidate"].trust_level == "candidate"
    assert artifacts["markdown_candidate"].role == "primary"
    assert artifacts["trace"].role == "evidence"
    assert artifacts["manifest"].role == "evidence"
    assert artifacts["raw_original"].trust_level == "source"
    assert result.trace_refs[0].artifact_ref == artifacts["trace"].artifact_ref

    view = asyncio.run(
        provider.get_artifact(
            artifacts["markdown_candidate"].artifact_ref,
            {"content_mode": "preview", "max_bytes": 32},
        )
    )
    assert view.kind == "markdown_candidate"
    assert view.trust_level == "candidate"
    assert view.text is not None
    assert len(view.text.encode("utf-8")) <= 32
    assert view.local_path is None
    assert view.truncated is True

    with pytest.raises(CapabilityPolicyError):
        asyncio.run(provider.get_artifact(artifacts["raw_original"].artifact_ref))

    permissive_provider = make_provider(
        store,
        policy={"allow_raw_artifact_read": True, "allow_full_content": True, "expose_local_paths": True},
    )
    full_view = asyncio.run(
        permissive_provider.get_artifact(
            artifacts["markdown_candidate"].artifact_ref,
            {"content_mode": "full", "max_bytes": 2048},
        )
    )
    assert full_view.text is not None
    assert "hello provider" in full_view.text
    assert full_view.local_path is not None

    cancel = asyncio.run(provider.cancel(job.job_id))
    assert cancel.status == "already_terminal"
    assert cancel.job_status == "success"


def test_heavy_queue_profile_can_make_url_available_when_policy_allows_network(tmp_path):
    provider = make_provider(tmp_path / "store", profile="heavy_queue", policy={"allow_network": True})

    manifest = provider.discover()

    url_capability = next(capability for capability in manifest.capabilities if capability.capability_id == "swallow.ingest.url")
    assert url_capability.available is True
    batch_capability = next(
        capability for capability in manifest.capabilities if capability.capability_id == "swallow.ingest.batch"
    )
    assert batch_capability.available is True


def test_queue_profile_cancel_reports_canceled_for_pending_job(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    sample = store / "sample.txt"
    sample.write_text("# Queue\n\n" + "queue provider " * 40, encoding="utf-8")
    provider = make_provider(store, profile="heavy_queue")

    job = asyncio.run(
        provider.submit(
            {
                "capability_id": "swallow.ingest.file",
                "input": {"source_path": str(sample)},
            }
        )
    )
    cancel = asyncio.run(provider.cancel(job.job_id))

    assert job.backend == "queue"
    assert job.status == "queued"
    assert cancel.status == "canceled"


def test_queue_profile_submit_get_and_cancel_batch(tmp_path):
    store = tmp_path / "store"
    inputs = store / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "a.txt").write_text("# A\n\nbatch a", encoding="utf-8")
    (inputs / "b.txt").write_text("# B\n\nbatch b", encoding="utf-8")
    provider = make_provider(store, profile="heavy_queue")

    batch = asyncio.run(
        provider.submit_batch(
            {
                "capability_id": "swallow.ingest.batch",
                "input": {"patterns": [str(inputs / "*.txt")], "workers": 1},
            }
        )
    )
    read_back = asyncio.run(provider.get_batch(batch.batch_id))
    cancel = asyncio.run(provider.cancel_batch(batch.batch_id))
    result = asyncio.run(provider.get_batch_result(batch.batch_id))

    assert isinstance(batch, CapabilityBatch)
    assert batch.backend == "queue"
    assert batch.status == "queued"
    assert batch.total == 2
    assert batch.summary_ref == f"swallow://batch_runs/{batch.batch_id}/summary.json"
    assert read_back.batch_id == batch.batch_id
    assert cancel.batch_id == batch.batch_id
    assert cancel.status == "canceled"
    assert result.status == "canceled"
    assert {artifact.kind for artifact in result.artifacts} >= {"batch_summary", "trace"}


def make_provider(store: Path, **kwargs) -> SwallowCapabilityProvider:
    store.mkdir(exist_ok=True)
    return SwallowCapabilityProvider(
        store_root=store,
        command=python_cli_command(),
        cwd=REPO_ROOT,
        **kwargs,
    )


def python_cli_command() -> list[str]:
    script = f"import sys; sys.path.insert(0, {str(SRC_ROOT)!r}); from swallow.cli.main import app; app()"
    return [sys.executable, "-c", script]
