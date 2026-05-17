from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from swallow.core.models import WorkerInput
from swallow.core.runner import IngestRunner
from swallow.workers import faster_whisper_worker as worker_module
from swallow.workers.faster_whisper_worker import FasterWhisperWorker, format_timestamp, resolve_ffmpeg_executable


def make_input(
    path: str,
    job_dir: Path,
    *,
    mime_type: str | None = "audio/mpeg",
    metadata: dict[str, Any] | None = None,
) -> WorkerInput:
    values = {"original_filename": Path(path).name, "job_dir": str(job_dir)}
    if metadata:
        values.update(metadata)
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=path,
        mime_type=mime_type,
        source_type="file",
        metadata=values,
    )


def test_faster_whisper_worker_reports_missing_dependency(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    source = tmp_path / "meeting.mp3"
    source.write_bytes(b"not real audio")

    result = FasterWhisperWorker().run(make_input(str(source), tmp_path / "job"))

    assert result.status == "failed"
    assert result.errors == ["missing_optional_dependency: install with `uv sync --extra asr`"]


def test_faster_whisper_worker_returns_transcript_markdown_and_artifacts(monkeypatch, tmp_path):
    created = install_fake_faster_whisper(monkeypatch)
    normalized: dict[str, Any] = {}
    monkeypatch.setattr(worker_module, "normalize_audio", capture_normalize_audio(normalized))
    source = tmp_path / "meeting.mp3"
    source.write_bytes(b"not real audio")
    job_dir = tmp_path / "job"

    result = FasterWhisperWorker().run(
        make_input(
            str(source),
            job_dir,
            metadata={
                "asr_model": "tiny",
                "asr_device": "cpu",
                "asr_compute_type": "int8",
                "timeout_seconds": 42,
                "ffmpeg_path": "C:/tools/ffmpeg.exe",
            },
        )
    )

    assert result.status == "success"
    assert result.worker_name == "faster_whisper_worker"
    assert normalized["timeout_seconds"] == 42
    assert normalized["ffmpeg_path"] == "C:/tools/ffmpeg.exe"
    assert created["model_name"] == "tiny"
    assert created["kwargs"] == {"device": "cpu", "compute_type": "int8"}
    assert created["transcribe_path"].endswith("intermediate\\audio\\normalized.wav") or created[
        "transcribe_path"
    ].endswith("intermediate/audio/normalized.wav")
    assert result.language == "zh"
    assert result.metadata["segments_count"] == 2
    assert result.metadata["duration_seconds"] == 65.5
    assert result.metadata["timeout_seconds"] == 42
    assert result.metadata["ffmpeg_path"] == "C:/tools/ffmpeg.exe"
    assert "## 00:00:00 - 00:01:00" in result.markdown
    assert "[00:00:01.240 --> 00:00:08.910] first segment" in result.markdown
    assert "## 00:01:00 - 00:02:00" in result.markdown
    assert "[00:01:01.000 --> 00:01:05.500] second minute" in result.markdown
    assert result.artifacts == [
        {"type": "normalized_audio", "path": "intermediate/audio/normalized.wav"},
        {"type": "transcript_json", "path": "intermediate/asr/transcript.json"},
    ]

    transcript = json.loads((job_dir / "intermediate" / "asr" / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["language"] == "zh"
    assert transcript["segments"] == [
        {"start": 1.24, "end": 8.91, "text": "first segment"},
        {"start": 61.0, "end": 65.5, "text": "second minute"},
    ]


def test_audio_file_ingests_with_mocked_faster_whisper(monkeypatch, tmp_path):
    install_fake_faster_whisper(monkeypatch)
    monkeypatch.setattr(worker_module, "normalize_audio", fake_normalize_audio)
    source = tmp_path / "meeting.mp3"
    source.write_bytes(b"not real audio")

    result = IngestRunner(store_root=tmp_path / "store").ingest_file(source)

    assert result.document.provenance.primary_worker == "faster_whisper_worker"
    assert "faster_whisper_worker@0.1.0" in result.document.provenance.worker_chain
    assert "# Transcript" in result.document.content.markdown
    assert "first segment" in result.document.content.markdown
    assert any(artifact["type"] == "normalized_audio" for artifact in result.document.provenance.artifacts)
    assert (tmp_path / "store" / result.document_path).exists()


def test_format_timestamp_keeps_millisecond_precision():
    assert format_timestamp(3723.4567) == "01:02:03.457"


def test_resolve_ffmpeg_executable_uses_preferred_path():
    assert resolve_ffmpeg_executable("C:/tools/ffmpeg.exe") == "C:/tools/ffmpeg.exe"


def test_resolve_ffmpeg_executable_falls_back_to_imageio_ffmpeg(monkeypatch):
    fake_module = types.ModuleType("imageio_ffmpeg")
    fake_module.get_ffmpeg_exe = lambda: "C:/imageio/ffmpeg.exe"
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_module)
    monkeypatch.setattr(worker_module.shutil, "which", lambda name: None)

    assert resolve_ffmpeg_executable() == "C:/imageio/ffmpeg.exe"


def install_fake_faster_whisper(monkeypatch) -> dict[str, Any]:
    created: dict[str, Any] = {}

    class FakeWhisperModel:
        def __init__(self, model_name: str, **kwargs: Any) -> None:
            created["model_name"] = model_name
            created["kwargs"] = kwargs

        def transcribe(self, path: str):
            created["transcribe_path"] = path
            segments = [
                SimpleNamespace(start=1.24, end=8.91, text=" first segment "),
                SimpleNamespace(start=61.0, end=65.5, text=" second minute "),
            ]
            return segments, SimpleNamespace(language="zh", duration=65.5)

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return created


def fake_normalize_audio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    timeout_seconds: int = 1800,
    ffmpeg_path: str | Path | None = None,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"fake wav")


def capture_normalize_audio(captured: dict[str, Any]):
    def normalize(
        input_path: str | Path,
        output_path: str | Path,
        *,
        timeout_seconds: int = 1800,
        ffmpeg_path: str | Path | None = None,
    ) -> None:
        captured["timeout_seconds"] = timeout_seconds
        captured["ffmpeg_path"] = ffmpeg_path
        fake_normalize_audio(input_path, output_path, timeout_seconds=timeout_seconds, ffmpeg_path=ffmpeg_path)

    return normalize
