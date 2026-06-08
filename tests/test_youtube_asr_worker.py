from __future__ import annotations

import json
from pathlib import Path

from swallow.core.models import WorkerInput
from swallow.workers.youtube_asr_worker import YouTubeASRWorker


def make_input(url: str, job_dir: Path, *, allow_media_download: bool = False) -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(job_dir / "url.json"),
        mime_type="application/json",
        source_type="url",
        source_url=url,
        metadata={
            "job_dir": str(job_dir),
            "original_filename": "youtube.url",
            "allow_media_download": allow_media_download,
        },
    )


def test_youtube_asr_worker_requires_explicit_media_download_consent(tmp_path):
    result = YouTubeASRWorker().run(make_input("https://www.youtube.com/watch?v=abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "YOUTUBE_ASR_MEDIA_DOWNLOAD_NOT_ALLOWED"
    assert "youtube_asr_media_download_not_allowed" in result.warnings
    extraction = json.loads((tmp_path / "job" / "intermediate" / "youtube" / "platform_extraction.json").read_text(encoding="utf-8"))
    assert extraction["content"]["media_download_allowed"] is False
    assert extraction["content"]["asr_segment_count"] == 0


def test_youtube_asr_worker_is_policy_gate_until_download_is_implemented(tmp_path):
    result = YouTubeASRWorker().run(
        make_input("https://www.youtube.com/watch?v=abc", tmp_path / "job", allow_media_download=True)
    )

    assert result.status == "failed"
    assert result.metadata["error_code"] == "YOUTUBE_ASR_NOT_IMPLEMENTED"
    assert "youtube_asr_not_implemented" in result.warnings
