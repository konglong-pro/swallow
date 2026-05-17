from __future__ import annotations

import json

from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.config import IngestConfig
from swallow.core.registry import default_registry


def test_default_registry_describes_worker_capabilities():
    descriptions = default_registry().describe()
    by_name = {description["name"]: description for description in descriptions}

    assert "markitdown_worker" in by_name
    assert "application/pdf" in by_name["markitdown_worker"]["capability"]["input_mime_types"]
    assert by_name["firecrawl_worker"]["capability"]["requires_network"] is True
    assert "local_browser_profile" in by_name["playwright_profile_worker"]["capability"]["strengths"]
    assert by_name["paddleocr_worker"]["capability"]["cost_level"] == "high"
    assert by_name["export_archive_worker"]["capability"]["supports_batch"] is True


def test_registry_describe_omits_disabled_workers():
    config = IngestConfig.from_mapping({"workers": {"firecrawl": {"enabled": False}}})

    names = {description["name"] for description in default_registry(config).describe()}

    assert "firecrawl_worker" not in names
    assert "crawl4ai_worker" in names


def test_cli_workers_outputs_capability_json(tmp_path):
    config = tmp_path / "swallow.config.yaml"
    config.write_text(
        """
workers:
  firecrawl:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["--config", str(config), "workers"])

    assert result.exit_code == 0, result.output
    descriptions = json.loads(result.output)
    names = {description["name"] for description in descriptions}
    assert "firecrawl_worker" not in names
    assert "playwright_worker" in names
