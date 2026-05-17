from __future__ import annotations

import sys
import types

from swallow.core.models import WorkerInput
from swallow.workers.markitdown_worker import MarkItDownWorker


def make_input(path: str, mime_type: str | None = "application/pdf") -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=path,
        mime_type=mime_type,
        source_type="file",
        metadata={"original_filename": "report.pdf"},
    )


def test_markitdown_worker_reports_missing_dependency(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "markitdown", None)
    sample = tmp_path / "report.pdf"
    sample.write_text("not really a pdf", encoding="utf-8")

    result = MarkItDownWorker().run(make_input(str(sample)))

    assert result.status == "failed"
    assert result.worker_name == "markitdown_worker"
    assert result.errors == ["missing_optional_dependency: install with `uv sync --extra markitdown`"]


def test_markitdown_worker_returns_standard_worker_result(monkeypatch, tmp_path):
    class FakeMarkItDown:
        def convert(self, path: str):
            return types.SimpleNamespace(
                markdown="# Converted\n\n" + ("body text " * 20),
                title="Converted",
            )

    monkeypatch.setitem(sys.modules, "markitdown", types.SimpleNamespace(MarkItDown=FakeMarkItDown))
    sample = tmp_path / "report.pdf"
    sample.write_text("not really a pdf", encoding="utf-8")

    result = MarkItDownWorker().run(make_input(str(sample)))

    assert result.status == "success"
    assert result.worker_name == "markitdown_worker"
    assert result.worker_version == "0.1.0"
    assert result.title == "Converted"
    assert result.markdown is not None
    assert result.metadata["converter"] == "markitdown"
