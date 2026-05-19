from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swallow.service.api import create_app
from swallow.sdk import (
    HttpIngestClient,
    IngestClient,
    IngestConfigError,
    IngestProtocolError,
    IngestResult,
    IngestSdkConfigError,
    IngestSdkProtocolError,
    IngestTimeoutError,
    IngestWaitTimeout,
    LocalIngestClient,
)


pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_RESULT_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sdk_results"


def test_ingest_result_includes_schema_and_sdk_metadata():
    result = IngestResult(job_id="ing_test", status="running", outputs={})

    assert result.meta.schema_version == 1
    assert result.meta.sdk_version


def test_sdk_exception_aliases_preserve_existing_python_names():
    assert IngestConfigError is IngestSdkConfigError
    assert IngestProtocolError is IngestSdkProtocolError
    assert IngestTimeoutError is IngestWaitTimeout


def test_core_sdk_code_does_not_define_tool_result_objects():
    sdk_root = REPO_ROOT / "src" / "swallow" / "sdk"
    matches: list[str] = []
    for path in sdk_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ToolResult" in text or "AgentToolResult" in text:
            matches.append(str(path.relative_to(REPO_ROOT)))

    assert matches == []


def test_local_and_http_results_are_shape_equivalent(tmp_path):
    local_store = tmp_path / "local"
    http_store = tmp_path / "http"
    local_store.mkdir()
    http_store.mkdir()
    local_sample = local_store / "sample.txt"
    http_sample = http_store / "sample.txt"
    text = "# Contract\n\n" + "same shape " * 40
    local_sample.write_text(text, encoding="utf-8")
    http_sample.write_text(text, encoding="utf-8")

    with LocalIngestClient(store_root=local_store) as local_client:
        local_result = local_client.file(local_sample)
    with HttpIngestClient(client=TestClient(create_app(http_store)), allowed_roots=[http_store]) as http_client:
        http_result = http_client.file(http_sample)

    assert set(local_result.model_dump(mode="python")) == set(http_result.model_dump(mode="python"))
    assert local_result.status == "success"
    assert http_result.status == "success"
    assert local_result.outputs.markdown_path and http_result.outputs.markdown_path
    assert local_result.outputs.document_json_path and http_result.outputs.document_json_path
    assert local_result.outputs.trace_path and http_result.outputs.trace_path
    assert isinstance(local_result.warnings, list)
    assert isinstance(http_result.warnings, list)
    assert isinstance(local_result.errors, list)
    assert isinstance(http_result.errors, list)
    assert local_result.preview is not None
    assert http_result.preview is not None
    assert local_result.content is None
    assert http_result.content is None


def test_facade_reserved_storage_modes_fail_clearly(tmp_path):
    with pytest.raises(IngestSdkConfigError):
        IngestClient(store_root=tmp_path, storage_mode="memory")
    with pytest.raises(IngestSdkConfigError):
        IngestClient(store_root=tmp_path, storage_mode="ephemeral")


@pytest.mark.parametrize("name", ["success", "partial", "failed", "running", "queued"])
def test_ingest_result_schema_fixtures_validate(name):
    payload = json.loads((SDK_RESULT_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    result = IngestResult.model_validate(payload)

    assert result.status == name
    assert result.outputs.trace_path
    assert isinstance(result.warnings, list)
    assert isinstance(result.errors, list)
    assert result.meta.schema_version == 1
    if name in {"success", "partial"}:
        assert result.outputs.markdown_path
        assert result.outputs.document_json_path
    if name == "failed":
        assert result.errors


def test_core_docs_do_not_describe_swallow_as_an_agent_framework():
    docs = [REPO_ROOT / "AGENTS.md", REPO_ROOT / "docs" / "sdk-agent-project-plan.md"]
    forbidden_positive_claims = [
        "swallow is an agent framework",
        "swallow is a tool framework",
        "swallow is a rag system",
        "swallow is a memory system",
    ]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in docs)

    assert all(claim not in text for claim in forbidden_positive_claims)
