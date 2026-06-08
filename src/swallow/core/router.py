from __future__ import annotations

from pathlib import Path
from typing import Callable

from swallow.core.errors import InputError
from swallow.core.models import WorkerInput
from swallow.detectors.file_type import (
    is_audio_video_file,
    is_image_file,
    is_markitdown_candidate,
    is_pdf_file,
    is_plain_text_file,
)
from swallow.detectors.pdf_analyzer import PdfAnalysis, analyze_pdf
from swallow.detectors.url_classifier import UrlKind, classify_url

FILE_PIPELINE_SUFFIX = ["quality_checker", "markdown_normalizer"]
WEB_LEVEL_1_PIPELINE = [
    "firecrawl_worker",
    "quality_checker",
    "fallback:crawl4ai_worker",
    "quality_checker",
    "fallback:playwright_worker",
    "markdown_normalizer",
]
WEB_DYNAMIC_PIPELINE = [
    "crawl4ai_worker",
    "quality_checker",
    "fallback:playwright_worker",
    "markdown_normalizer",
]
WEB_LOGIN_PIPELINE = [
    "playwright_profile_worker",
    "quality_checker",
    "fallback:playwright_worker",
    "markdown_normalizer",
]
CHATGPT_SHARE_PIPELINE = [
    "chatgpt_share_worker",
    "quality_checker",
    "fallback:playwright_profile_worker",
    "markdown_normalizer",
]
GEMINI_SHARE_PIPELINE = [
    "gemini_share_worker",
    "quality_checker",
    "markdown_normalizer",
]
CLAUDE_SHARE_PIPELINE = [
    "claude_share_worker",
    "quality_checker",
    "fallback:playwright_profile_worker",
    "markdown_normalizer",
]
DEEPSEEK_SHARE_PIPELINE = [
    "deepseek_share_worker",
    "quality_checker",
    "fallback:playwright_profile_worker",
    "markdown_normalizer",
]
WECHAT_ARTICLE_PIPELINE = [
    "wechat_article_worker",
    "quality_checker",
    "fallback:playwright_worker",
    "markdown_normalizer",
]
YOUTUBE_VIDEO_PIPELINE = [
    "youtube_transcript_worker",
    "quality_checker",
    "fallback:playwright_worker",
    "markdown_normalizer",
]
YOUTUBE_VIDEO_WITH_ASR_PIPELINE = [
    "youtube_transcript_worker",
    "quality_checker",
    "fallback:youtube_asr_worker",
    "quality_checker",
    "fallback:playwright_worker",
    "markdown_normalizer",
]


class Router:
    def __init__(
        self,
        pdf_analyzer: Callable[[str], PdfAnalysis] = analyze_pdf,
        *,
        youtube_asr_enabled: bool = False,
    ) -> None:
        self.pdf_analyzer = pdf_analyzer
        self.youtube_asr_enabled = youtube_asr_enabled

    def route(self, input: WorkerInput) -> list[str]:
        if input.source_type == "url":
            return self.route_url(input.source_url)

        if input.source_type == "browser_capture":
            return ["browser_capture_worker", *FILE_PIPELINE_SUFFIX]

        if input.source_type == "export_archive":
            return ["export_archive_worker", *FILE_PIPELINE_SUFFIX]

        if input.source_type != "file":
            raise InputError(f"Not implemented in this phase: {input.source_type} ingest")

        if is_plain_text_file(input.input_path, input.mime_type):
            return ["plain_text_worker", *FILE_PIPELINE_SUFFIX]

        if is_pdf_file(input.input_path, input.mime_type):
            return self.route_pdf(input)

        if is_image_file(input.input_path, input.mime_type):
            return ["paddleocr_worker", *FILE_PIPELINE_SUFFIX]

        if is_audio_video_file(input.input_path, input.mime_type):
            return ["faster_whisper_worker", *FILE_PIPELINE_SUFFIX]

        if is_markitdown_candidate(input.input_path, input.mime_type):
            return ["markitdown_worker", *FILE_PIPELINE_SUFFIX]

        suffix = Path(input.input_path).suffix.lower()
        raise InputError(f"Not implemented in this phase: file ingest for {input.mime_type or suffix}")

    def route_pdf(self, input: WorkerInput) -> list[str]:
        analysis = self.pdf_analyzer(input.input_path)

        if analysis.is_scanned:
            return ["paddleocr_worker", *FILE_PIPELINE_SUFFIX]

        if analysis.text_density < 0.15:
            return ["paddleocr_worker", *FILE_PIPELINE_SUFFIX]

        return ["markitdown_worker", "quality_checker", "fallback:paddleocr_worker", "markdown_normalizer"]

    def route_url(self, url: str | None) -> list[str]:
        if not url:
            raise InputError("URL ingest requires source_url")

        classification = classify_url(url)
        if classification.kind == UrlKind.CHATGPT_SHARE:
            return CHATGPT_SHARE_PIPELINE

        if classification.kind == UrlKind.GEMINI_SHARE:
            return GEMINI_SHARE_PIPELINE

        if classification.kind == UrlKind.CLAUDE_SHARE:
            return CLAUDE_SHARE_PIPELINE

        if classification.kind == UrlKind.DEEPSEEK_SHARE:
            return DEEPSEEK_SHARE_PIPELINE

        if classification.kind == UrlKind.WECHAT_ARTICLE:
            return WECHAT_ARTICLE_PIPELINE

        if classification.kind == UrlKind.YOUTUBE_VIDEO:
            return YOUTUBE_VIDEO_WITH_ASR_PIPELINE if self.youtube_asr_enabled else YOUTUBE_VIDEO_PIPELINE

        if classification.kind in {UrlKind.LOGIN_REQUIRED_WEB, UrlKind.RESTRICTED_WEB}:
            return WEB_LOGIN_PIPELINE

        if classification.kind == UrlKind.SHORT_URL:
            return WEB_DYNAMIC_PIPELINE

        if classification.kind == UrlKind.DYNAMIC_WEB:
            return WEB_DYNAMIC_PIPELINE

        return WEB_LEVEL_1_PIPELINE
