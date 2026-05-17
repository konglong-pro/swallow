from __future__ import annotations

import os
from pathlib import Path

import pytest

from swallow.core.models import WorkerInput
from swallow.workers.firecrawl_worker import FirecrawlWorker


pytestmark = pytest.mark.network


def require_firecrawl_real_api() -> None:
    if os.getenv("RUN_FIRECRAWL_TESTS") != "1":
        pytest.skip("Set RUN_FIRECRAWL_TESTS=1 to run Firecrawl real API tests.")
    if not os.getenv("FIRECRAWL_API_KEY"):
        pytest.fail("RUN_FIRECRAWL_TESTS=1 requires FIRECRAWL_API_KEY.")


def test_firecrawl_real_api_scrapes_example_dot_com(tmp_path):
    require_firecrawl_real_api()
    job_dir = tmp_path / "job"
    input = WorkerInput(
        job_id="ing_firecrawl_real",
        raw_id="raw_firecrawl_real",
        input_path=str(tmp_path / "source.url.json"),
        mime_type="application/json",
        source_type="url",
        source_url="https://example.com/",
        metadata={"original_filename": "example.com.url", "job_dir": str(job_dir)},
    )

    result = FirecrawlWorker().run(input)

    assert result.status == "success", result.errors
    assert result.markdown
    assert "Example" in result.markdown
    assert result.metadata["source_url"] == "https://example.com/"
    assert result.metadata["crawler"] == "firecrawl"
    assert result.title or result.metadata.get("firecrawl_metadata")
    assert any(artifact["type"] == "html" for artifact in result.artifacts)
    for artifact in result.artifacts:
        assert (job_dir / Path(artifact["path"])).exists()
