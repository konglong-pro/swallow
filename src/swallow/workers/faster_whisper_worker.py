from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.file_type import AUDIO_VIDEO_MIME_TYPES, is_audio_video_file
from swallow.workers.base import BaseWorker


class FasterWhisperWorker(BaseWorker):
    name = "faster_whisper_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=sorted(AUDIO_VIDEO_MIME_TYPES),
        source_types=["file"],
        strengths=["asr", "audio_transcription", "video_audio_extraction", "timestamped_transcript"],
        cost_level="high",
        requires_gpu=False,
        requires_network=False,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "file" and is_audio_video_file(input.input_path, input.mime_type)

    def run(self, input: WorkerInput) -> WorkerResult:
        job_dir = get_job_dir(input)
        if job_dir is None:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_worker_metadata: job_dir"],
            )

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra asr`"],
                metadata={"engine": "faster-whisper", "input_mime_type": input.mime_type},
            )

        engine_versions = get_engine_versions()
        intermediate_dir = job_dir / "intermediate"
        audio_dir = intermediate_dir / "audio"
        asr_dir = intermediate_dir / "asr"
        audio_dir.mkdir(parents=True, exist_ok=True)
        asr_dir.mkdir(parents=True, exist_ok=True)
        normalized_audio_path = audio_dir / "normalized.wav"
        timeout_seconds = to_positive_int(input.metadata.get("timeout_seconds"), default=1800)
        ffmpeg_path = input.metadata.get("ffmpeg_path") or input.metadata.get("ffmpeg") or os.getenv("SWALLOW_FFMPEG_PATH")

        try:
            normalize_audio(
                input.input_path,
                normalized_audio_path,
                timeout_seconds=timeout_seconds,
                ffmpeg_path=str(ffmpeg_path) if ffmpeg_path else None,
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"ffmpeg_normalize_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={
                    "engine": "faster-whisper",
                    "input_mime_type": input.mime_type,
                    "timeout_seconds": timeout_seconds,
                    "ffmpeg_path": str(ffmpeg_path) if ffmpeg_path else None,
                    **engine_versions,
                },
            )

        model_name = str(
            input.metadata.get("asr_model") or input.metadata.get("model") or os.getenv("SWALLOW_FASTER_WHISPER_MODEL", "large-v3")
        )
        device = str(input.metadata.get("asr_device") or input.metadata.get("device") or os.getenv("SWALLOW_FASTER_WHISPER_DEVICE", "auto"))
        compute_type = str(
            input.metadata.get("asr_compute_type")
            or input.metadata.get("compute_type")
            or os.getenv("SWALLOW_FASTER_WHISPER_COMPUTE_TYPE", "default")
        )

        try:
            model = create_whisper_model(WhisperModel, model_name=model_name, device=device, compute_type=compute_type)
            segments_iter, info = model.transcribe(str(normalized_audio_path))
            segments = [Segment.from_raw(segment) for segment in segments_iter]
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"faster_whisper_transcribe_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={
                    "engine": "faster-whisper",
                    "model": model_name,
                    "device": device,
                    "compute_type": compute_type,
                    "timeout_seconds": timeout_seconds,
                    "ffmpeg_path": str(ffmpeg_path) if ffmpeg_path else None,
                    **engine_versions,
                },
            )

        transcript_json_path = asr_dir / "transcript.json"
        transcript_json_path.write_text(
            json.dumps(
                {
                    "language": getattr(info, "language", None),
                    "duration_seconds": getattr(info, "duration", None),
                    "segments": [segment.to_json() for segment in segments],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        markdown = render_transcript_markdown(segments)
        warnings: list[str] = []
        if not segments:
            warnings.append("asr_no_segments")
        if len(markdown.strip()) < 100:
            warnings.append("asr_output_too_short")

        artifacts = [
            {"type": "normalized_audio", "path": relative_artifact_path(normalized_audio_path, job_dir)},
            {"type": "transcript_json", "path": relative_artifact_path(transcript_json_path, job_dir)},
        ]

        return WorkerResult(
            status="success" if segments else "failed",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown if segments else None,
            title=Path(input.metadata.get("original_filename", input.input_path)).stem,
            language=getattr(info, "language", None),
            artifacts=artifacts,
            metadata={
                "engine": "faster-whisper",
                "model": model_name,
                "device": device,
                "compute_type": compute_type,
                "timeout_seconds": timeout_seconds,
                "ffmpeg_path": str(ffmpeg_path) if ffmpeg_path else None,
                "language": getattr(info, "language", None),
                "duration_seconds": getattr(info, "duration", None),
                "segments_count": len(segments),
                **engine_versions,
            },
            warnings=warnings,
            errors=[] if segments else ["faster-whisper produced empty transcript"],
        )


class Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text

    @classmethod
    def from_raw(cls, raw: Any) -> Segment:
        return cls(
            start=float(getattr(raw, "start", 0.0)),
            end=float(getattr(raw, "end", 0.0)),
            text=str(getattr(raw, "text", "")).strip(),
        )

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


def get_job_dir(input: WorkerInput) -> Path | None:
    value = input.metadata.get("job_dir")
    if not value:
        return None
    return Path(value)


def normalize_audio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    timeout_seconds: int = 1800,
    ffmpeg_path: str | Path | None = None,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg_executable(ffmpeg_path)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(stderr or f"ffmpeg exited with code {completed.returncode}")


def resolve_ffmpeg_executable(preferred: str | Path | None = None) -> str:
    if preferred:
        return str(preferred)

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except ImportError as error:
        raise RuntimeError("ffmpeg not found on PATH and imageio-ffmpeg is not installed") from error

    return str(get_ffmpeg_exe())


def create_whisper_model(whisper_model_cls: Any, *, model_name: str, device: str, compute_type: str) -> Any:
    kwargs: dict[str, Any] = {}
    if device != "auto":
        kwargs["device"] = device
    if compute_type != "default":
        kwargs["compute_type"] = compute_type
    return whisper_model_cls(model_name, **kwargs)


def to_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def render_transcript_markdown(segments: list[Segment]) -> str:
    chunks = ["# Transcript"]
    current_bucket: int | None = None

    for segment in segments:
        bucket = int(segment.start // 60)
        if bucket != current_bucket:
            current_bucket = bucket
            chunks.append(f"## {format_clock(bucket * 60)} - {format_clock((bucket + 1) * 60)}")
        chunks.append(f"[{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}] {segment.text}")

    return "\n\n".join(chunks).strip() + "\n"


def format_clock(seconds: float) -> str:
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def relative_artifact_path(path: Path, job_dir: Path) -> str:
    return path.relative_to(job_dir).as_posix()


def get_engine_versions() -> dict[str, str | None]:
    return {"engine_version": package_version("faster-whisper")}


def package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def short_error(error: Exception, *, max_length: int = 200) -> str:
    message = str(error).replace("\n", " ").strip()
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."
