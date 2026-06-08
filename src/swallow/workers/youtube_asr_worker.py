from __future__ import annotations

from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.url_classifier import UrlKind, classify_url
from swallow.workers.base import BaseWorker
from swallow.workers.platform_common import (
    build_platform_extraction,
    metadata_from_extraction,
    parse_youtube_video_id,
    render_platform_markdown,
    write_platform_artifacts,
)
from swallow.workers.web_common import get_url


class YouTubeASRWorker(BaseWorker):
    name = "youtube_asr_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["youtube", "asr", "explicit_media_download_required", "platform_extraction"],
        cost_level="high",
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
        allow_media_download = to_bool(input.metadata.get("allow_media_download"), default=False)
        max_media_size_bytes = to_positive_int(input.metadata.get("max_media_size_bytes"), default=500 * 1024 * 1024)
        quality_flags = ["asr_not_run"]
        if not allow_media_download:
            quality_flags.append("media_download_not_allowed")

        extraction = build_platform_extraction(
            platform="youtube",
            url_kind=UrlKind.YOUTUBE_VIDEO.value,
            source_url=str(url),
            title=f"YouTube {video_id or 'video'} ASR",
            content={
                "type": "transcript",
                "text_char_count": 0,
                "subtitle_segment_count": 0,
                "asr_segment_count": 0,
                "video_id": video_id,
                "segments": [],
                "media_download_allowed": allow_media_download,
                "max_media_size_bytes": max_media_size_bytes,
            },
            method="youtube_asr_policy_gate",
            worker=self.name,
            quality_flags=quality_flags,
        )
        artifacts = write_platform_artifacts(input, platform="youtube", extraction=extraction)
        metadata = metadata_from_extraction(extraction)
        markdown = render_platform_markdown(extraction)

        if not allow_media_download:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                markdown=markdown,
                title=f"YouTube {video_id or 'video'} ASR",
                artifacts=artifacts,
                metadata={**metadata, "error_code": "YOUTUBE_ASR_MEDIA_DOWNLOAD_NOT_ALLOWED", "video_id": video_id},
                warnings=["youtube_asr_media_download_not_allowed"],
                errors=["youtube_asr_requires_explicit_media_download_consent"],
            )

        return WorkerResult(
            status="failed",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=f"YouTube {video_id or 'video'} ASR",
            artifacts=artifacts,
            metadata={**metadata, "error_code": "YOUTUBE_ASR_NOT_IMPLEMENTED", "video_id": video_id},
            warnings=["youtube_asr_not_implemented"],
            errors=["youtube_asr_download_and_transcription_not_implemented"],
        )


def to_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def to_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
