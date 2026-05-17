from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.models import WorkerInput
from swallow.core.runner import IngestRunner
from swallow.workers.export_archive_worker import ExportArchiveWorker, detect_archive_type, parse_chatgpt_export


def make_input(path: Path, job_dir: Path) -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(path),
        mime_type="application/zip",
        source_type="export_archive",
        metadata={"original_filename": path.name, "job_dir": str(job_dir)},
    )


def test_detect_archive_type_identifies_chatgpt_export(tmp_path):
    archive = tmp_path / "chatgpt-export.zip"
    write_chatgpt_export(archive)

    assert detect_archive_type(archive) == "chatgpt_export"


def test_parse_chatgpt_export_extracts_mapping_messages_in_time_order():
    conversations = parse_chatgpt_export(sample_chatgpt_conversations())

    assert conversations == [
        {
            "title": "Swallow plan",
            "created_at": 1.0,
            "updated_at": 4.0,
            "messages": [
                {"role": "user", "content": "Please design the ingest pipeline."},
                {"role": "assistant", "content": "Use RawStore and Trace."},
            ],
        }
    ]


def test_export_archive_worker_returns_markdown_and_payload_artifact(tmp_path):
    archive = tmp_path / "chatgpt-export.zip"
    write_chatgpt_export(archive)
    job_dir = tmp_path / "job"

    result = ExportArchiveWorker().run(make_input(archive, job_dir))

    assert result.status == "success"
    assert result.title == "ChatGPT Export"
    assert result.metadata["archive_type"] == "chatgpt_export"
    assert result.metadata["record_count"] == 1
    assert result.metadata["message_count"] == 2
    assert "# ChatGPT Export" in result.markdown
    assert "## Conversation 1: Swallow plan" in result.markdown
    assert "Please design the ingest pipeline." in result.markdown
    assert result.artifacts == [{"type": "chatgpt_conversations_json", "path": "intermediate/export_archive/conversations.json"}]
    assert (job_dir / "intermediate" / "export_archive" / "conversations.json").exists()


def test_archive_ingest_end_to_end(tmp_path):
    archive = tmp_path / "chatgpt-export.zip"
    write_chatgpt_export(archive)

    result = IngestRunner(store_root=tmp_path / "store").ingest_archive(archive)

    assert result.document.source.source_type == "export_archive"
    assert result.document.provenance.primary_worker == "export_archive_worker"
    assert "export_archive_worker@0.1.0" in result.document.provenance.worker_chain
    assert "Use RawStore and Trace." in result.document.content.markdown
    assert (tmp_path / "store" / result.document_path).exists()


def test_cli_archive_ingest(tmp_path):
    archive = tmp_path / "chatgpt-export.zip"
    write_chatgpt_export(archive)

    result = CliRunner().invoke(app, ["--store", str(tmp_path / "store"), "archive", str(archive)])

    assert result.exit_code == 0, result.output
    assert "Status: success" in result.output
    jobs = list((tmp_path / "store" / "jobs").iterdir())
    assert len(jobs) == 1
    assert (jobs[0] / "document.md").exists()
    assert (jobs[0] / "ingest_document.json").exists()
    assert (jobs[0] / "trace.jsonl").exists()


def write_chatgpt_export(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("conversations.json", json.dumps(sample_chatgpt_conversations(), ensure_ascii=False))


def sample_chatgpt_conversations() -> list[dict]:
    return [
        {
            "title": "Swallow plan",
            "create_time": 1.0,
            "update_time": 4.0,
            "mapping": {
                "root": {"message": None},
                "user": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 2.0,
                        "content": {"content_type": "text", "parts": ["Please design the ingest pipeline."]},
                    }
                },
                "assistant": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 3.0,
                        "content": {"content_type": "text", "parts": ["Use RawStore and Trace."]},
                    }
                },
            },
        }
    ]
