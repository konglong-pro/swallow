from __future__ import annotations

from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.url_classifier import UrlKind, classify_url
from swallow.workers.base import BaseWorker
from swallow.workers.platform_common import (
    build_platform_extraction,
    classify_platform_page_state,
    fetch_text,
    find_youtube_caption_tracks,
    metadata_from_extraction,
    parse_timedtext_payload,
    parse_youtube_video_id,
    render_platform_markdown,
    render_platform_page,
    write_platform_artifacts,
)
from swallow.workers.web_common import get_url, short_error


class YouTubeTranscriptWorker(BaseWorker):
    name = "youtube_transcript_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["youtube", "transcript", "subtitle_segments", "platform_extraction"],
        cost_level="medium",
        requires_gpu=False,
        requires_network=True,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        url = get_url(input)
        return input.source_type == "url" and bool(url) and classify_url(str(url)).kind == UrlKind.YOUTUBE_VIDEO

    def run(self, input: WorkerInput) -> WorkerResult:
        url = get_url(input)
        if not url:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_worker_input: source_url"],
            )

        video_id = parse_youtube_video_id(str(url))
        if not video_id:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["youtube_video_id_missing"],
                metadata={"platform": "youtube", "url_kind": UrlKind.YOUTUBE_VIDEO.value, "source_url": url},
            )

        timeout_seconds = to_positive_int(input.metadata.get("timeout_seconds"), default=120)
        try:
            page = render_platform_page(str(url), headless=True, timeout_seconds=timeout_seconds)
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra web`"],
                metadata={"platform": "youtube", "url_kind": UrlKind.YOUTUBE_VIDEO.value, "source_url": url},
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"youtube_render_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={"platform": "youtube", "url_kind": UrlKind.YOUTUBE_VIDEO.value, "source_url": url},
            )

        tracks = find_youtube_caption_tracks(page.html)
        segments: list[dict[str, Any]] = []
        language = None
        if tracks:
            track = select_caption_track(tracks)
            caption_url = track.get("baseUrl")
            language = track.get("languageCode")
            if isinstance(caption_url, str) and caption_url:
                try:
                    segments = parse_timedtext_payload(fetch_text(caption_url, timeout_seconds=timeout_seconds))
                except Exception:
                    segments = []

        quality_flags: list[str] = []
        if not segments:
            quality_flags.append("no_transcript")
        page_state = classify_platform_page_state(page.html, status_code=page.status_code) if not segments else None
        page_warning = page_state.warning if page_state is not None and page_state.state != "empty_shell" else None
        page_error_code = page_state.error_code if page_state is not None and page_state.state != "empty_shell" else None
        if page_warning:
            quality_flags.append(page_warning)

        extraction = build_platform_extraction(
            platform="youtube",
            url_kind=UrlKind.YOUTUBE_VIDEO.value,
            source_url=str(url),
            final_url=page.final_url,
            title=page.title or f"YouTube {video_id}",
            language=language,
            content={
                "type": "transcript",
                "text_char_count": sum(len(str(segment.get("text") or "")) for segment in segments),
                "subtitle_segment_count": len(segments),
                "video_id": video_id,
                "segments": segments,
            },
            method="youtube_caption_tracks",
            auth_mode=page_state.auth_mode if page_state is not None else "none",
            page_state=page_state.state if page_state is not None else "ok",
            worker=self.name,
            quality_flags=quality_flags,
        )
        artifacts = write_platform_artifacts(input, platform="youtube", extraction=extraction, rendered_html=page.html)
        metadata = metadata_from_extraction(extraction, status_code=page.status_code, html_length=len(page.html))
        markdown = render_platform_markdown(extraction)

        if not segments:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                markdown=markdown,
                title=page.title or f"YouTube {video_id}",
                artifacts=artifacts,
                metadata={**metadata, "error_code": page_error_code or "YOUTUBE_TRANSCRIPT_NOT_FOUND", "video_id": video_id},
                warnings=[page_warning or "platform_no_transcript"],
                errors=[f"youtube_{page_state.state}" if page_error_code and page_state is not None else "youtube_transcript_not_found"],
            )

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=page.title or f"YouTube {video_id}",
            language=language,
            artifacts=artifacts,
            metadata={**metadata, "video_id": video_id},
        )


def to_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def select_caption_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    manual_tracks = [track for track in tracks if isinstance(track, dict) and track.get("kind") != "asr"]
    candidates = manual_tracks or [track for track in tracks if isinstance(track, dict)]
    if not candidates:
        return {}
    return candidates[0]
