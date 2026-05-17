from __future__ import annotations

import sys

from typer.testing import CliRunner

from swallow.cli.main import app


def test_cli_reports_missing_markitdown_extra_without_traceback(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "markitdown", None)
    sample = tmp_path / "page.html"
    sample.write_text("<html><body><h1>Report</h1></body></html>", encoding="utf-8")

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "file", str(sample)])

    assert result.exit_code == 1
    assert "Ingest failed: WORKER_RESULT_FAILED: missing_optional_dependency" in result.output
    assert "Traceback" not in result.output
