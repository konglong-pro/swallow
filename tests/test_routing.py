from __future__ import annotations

import pytest

from swallow.core.errors import InputError
from swallow.core.models import WorkerInput
from swallow.core.router import Router
from swallow.detectors.pdf_analyzer import PdfAnalysis


def make_input(path: str, mime_type: str | None = None, source_type: str = "file") -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=path,
        mime_type=mime_type,
        source_type=source_type,
    )


def test_routes_plain_text_to_plain_text_worker():
    assert Router().route(make_input("sample.txt", "text/plain")) == [
        "plain_text_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


@pytest.mark.parametrize(
    ("path", "mime_type"),
    [
        ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("page.html", "text/html"),
    ],
)
def test_routes_markitdown_candidates_to_markitdown_worker(path: str, mime_type: str):
    assert Router().route(make_input(path, mime_type)) == [
        "markitdown_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


def test_routes_text_pdf_to_markitdown_with_ocr_fallback():
    router = Router(pdf_analyzer=lambda path: pdf_analysis(text_density=0.4, chars_per_page=800))

    assert router.route(make_input("report.pdf", "application/pdf")) == [
        "markitdown_worker",
        "quality_checker",
        "fallback:paddleocr_worker",
        "markdown_normalizer",
    ]


def test_routes_scanned_pdf_to_paddleocr_worker():
    router = Router(pdf_analyzer=lambda path: pdf_analysis(is_scanned=True, text_density=0.0, chars_per_page=0))

    assert router.route(make_input("scan.pdf", "application/pdf")) == [
        "paddleocr_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


def test_routes_low_text_pdf_to_paddleocr_worker():
    router = Router(pdf_analyzer=lambda path: pdf_analysis(text_density=0.05, chars_per_page=100))

    assert router.route(make_input("sparse.pdf", "application/pdf")) == [
        "paddleocr_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


def test_routes_images_to_paddleocr_worker():
    assert Router().route(make_input("screenshot.png", "image/png")) == [
        "paddleocr_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


@pytest.mark.parametrize(
    ("path", "mime_type"),
    [
        ("meeting.mp3", "audio/mpeg"),
        ("recording.wav", "audio/wav"),
        ("video.mp4", "video/mp4"),
    ],
)
def test_routes_audio_video_to_faster_whisper_worker(path: str, mime_type: str):
    assert Router().route(make_input(path, mime_type)) == [
        "faster_whisper_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


def test_non_file_sources_are_not_implemented_yet():
    with pytest.raises(InputError, match="unknown ingest"):
        Router().route(make_input("payload.json", source_type="unknown"))


def test_routes_browser_capture_to_browser_capture_worker():
    assert Router().route(make_input("capture.json", "application/json", source_type="browser_capture")) == [
        "browser_capture_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


def test_routes_export_archive_to_export_archive_worker():
    assert Router().route(make_input("chatgpt-export.zip", "application/zip", source_type="export_archive")) == [
        "export_archive_worker",
        "quality_checker",
        "markdown_normalizer",
    ]


def test_routes_static_public_url_to_firecrawl_with_crawl4ai_fallback():
    assert Router().route(
        make_input("url.json", "application/json", source_type="url").model_copy(
            update={"source_url": "https://example.com/article"}
        )
    ) == [
        "firecrawl_worker",
        "quality_checker",
        "fallback:crawl4ai_worker",
        "quality_checker",
        "fallback:playwright_worker",
        "markdown_normalizer",
    ]


def test_routes_dynamic_url_to_crawl4ai_worker():
    assert Router().route(
        make_input("url.json", "application/json", source_type="url").model_copy(
            update={"source_url": "https://app.example.com/dashboard"}
        )
    ) == [
        "crawl4ai_worker",
        "quality_checker",
        "fallback:playwright_worker",
        "markdown_normalizer",
    ]


@pytest.mark.parametrize(
    ("url", "expected_first_worker"),
    [
        ("https://chatgpt.com/share/abc", "chatgpt_share_worker"),
        ("https://gemini.google.com/share/abc", "gemini_share_worker"),
        ("https://g.co/gemini/share/abc", "gemini_share_worker"),
        ("https://claude.ai/share/abc", "claude_share_worker"),
        ("https://chat.deepseek.com/share/abc", "deepseek_share_worker"),
        ("https://mp.weixin.qq.com/s/abc", "wechat_article_worker"),
        ("https://www.youtube.com/watch?v=abc", "youtube_transcript_worker"),
    ],
)
def test_routes_platform_urls_to_platform_workers(url: str, expected_first_worker: str):
    route = Router().route(
        make_input("url.json", "application/json", source_type="url").model_copy(update={"source_url": url})
    )

    assert route[0] == expected_first_worker


def test_routes_youtube_to_asr_fallback_only_when_enabled():
    route = Router(youtube_asr_enabled=True).route(
        make_input("url.json", "application/json", source_type="url").model_copy(
            update={"source_url": "https://www.youtube.com/watch?v=abc"}
        )
    )

    assert route == [
        "youtube_transcript_worker",
        "quality_checker",
        "fallback:youtube_asr_worker",
        "quality_checker",
        "fallback:playwright_worker",
        "markdown_normalizer",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://chatgpt.com/c/example",
    ],
)
def test_routes_login_or_restricted_url_to_local_profile_worker(url: str):
    assert Router().route(
        make_input("url.json", "application/json", source_type="url").model_copy(update={"source_url": url})
    ) == [
        "playwright_profile_worker",
        "quality_checker",
        "fallback:playwright_worker",
        "markdown_normalizer",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/example/status/123",
        "https://x.com/i/article/2061850535708483585",
        "https://www.xiaohongshu.com/explore/abc",
        "https://zhuanlan.zhihu.com/p/123",
        "https://www.zhihu.com/question/123",
        "https://www.zhihu.com/question/123/answer/456",
    ],
)
def test_removed_platform_urls_use_generic_web_route(url: str):
    assert Router().route(
        make_input("url.json", "application/json", source_type="url").model_copy(update={"source_url": url})
    ) == [
        "firecrawl_worker",
        "quality_checker",
        "fallback:crawl4ai_worker",
        "quality_checker",
        "fallback:playwright_worker",
        "markdown_normalizer",
    ]


def pdf_analysis(
    *,
    is_scanned: bool = False,
    text_density: float = 0.4,
    chars_per_page: float = 800,
) -> PdfAnalysis:
    return PdfAnalysis(
        page_count=1,
        extracted_text_chars=int(chars_per_page),
        chars_per_page=chars_per_page,
        text_density=text_density,
        is_scanned=is_scanned,
        has_many_images=False,
        image_count=0,
        warnings=[],
    )
