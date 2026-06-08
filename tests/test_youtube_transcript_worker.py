from __future__ import annotations

from pathlib import Path

from swallow.core.models import WorkerInput
from swallow.workers import youtube_transcript_worker as module
from swallow.workers.platform_common import PlatformPage
from swallow.workers.youtube_transcript_worker import YouTubeTranscriptWorker

FIXTURES = Path(__file__).parent / "fixtures" / "web"


def make_input(url: str, job_dir: Path) -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(job_dir / "url.json"),
        mime_type="application/json",
        source_type="url",
        source_url=url,
        metadata={"job_dir": str(job_dir), "original_filename": "youtube.url"},
    )


def test_youtube_transcript_worker_extracts_caption_segments(monkeypatch, tmp_path):
    html = (FIXTURES / "youtube_watch_with_captions.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Swallow Video", final_url=url, status_code=200),
    )
    monkeypatch.setattr(
        module,
        "fetch_text",
        lambda url, **_: '<transcript><text start="0" dur="2">Hello transcript.</text><text start="2" dur="2">Second line.</text></transcript>',
    )

    result = YouTubeTranscriptWorker().run(make_input("https://www.youtube.com/watch?v=abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["platform"] == "youtube"
    assert result.metadata["subtitle_segment_count"] == 2
    assert "[00:00:00] Hello transcript." in result.markdown


def test_youtube_transcript_worker_extracts_json3_caption_segments(monkeypatch, tmp_path):
    html = (FIXTURES / "youtube_watch_with_captions.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Swallow Video", final_url=url, status_code=200),
    )
    monkeypatch.setattr(
        module,
        "fetch_text",
        lambda url, **_: '{"events":[{"tStartMs":0,"dDurationMs":1200,"segs":[{"utf8":"JSON3 line."}]},{"tStartMs":1200,"dDurationMs":1200,"segs":[{"utf8":"Second "},{"utf8":"JSON3 line."}]}]}',
    )

    result = YouTubeTranscriptWorker().run(make_input("https://www.youtube.com/watch?v=abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["subtitle_segment_count"] == 2
    assert "[00:00:01] Second JSON3 line." in result.markdown


def test_youtube_transcript_worker_fails_without_captions(monkeypatch, tmp_path):
    html = (FIXTURES / "youtube_watch_no_captions.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="No Caption", final_url=url, status_code=200),
    )

    result = YouTubeTranscriptWorker().run(make_input("https://www.youtube.com/watch?v=abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "YOUTUBE_TRANSCRIPT_NOT_FOUND"
    assert "platform_no_transcript" in result.warnings


def test_youtube_transcript_worker_reports_region_block(monkeypatch, tmp_path):
    html = (FIXTURES / "youtube_region_blocked.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Video unavailable", final_url=url, status_code=403),
    )

    result = YouTubeTranscriptWorker().run(make_input("https://www.youtube.com/watch?v=abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_REGION_BLOCKED"
    assert result.metadata["page_state"] == "region_blocked"
    assert "platform_region_blocked" in result.warnings
