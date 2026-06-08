from __future__ import annotations

from swallow.core.models import WorkerInput, WorkerResult
from swallow.core.quality import check_quality


def test_url_quality_detects_auth_wall_for_login_required_source():
    result = WorkerResult(
        status="success",
        worker_name="playwright_profile_worker",
        worker_version="0.1.0",
        markdown="# Log in\n\nPlease log in to continue. Continue with Google. Forgot password?",
        metadata={
            "source_url": "https://chatgpt.com/c/example",
            "final_url": "https://chatgpt.com/auth/login",
            "status_code": 200,
        },
    )
    input = WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path="url.json",
        mime_type="application/json",
        source_type="url",
        source_url="https://chatgpt.com/c/example",
    )

    quality = check_quality(result, input)

    assert quality.score < 0.45
    assert quality.metrics["auth_wall_detected"] is True
    assert "auth_wall_detected" in quality.warnings


def test_url_quality_does_not_treat_single_sign_in_nav_link_as_auth_wall():
    result = WorkerResult(
        status="success",
        worker_name="firecrawl_worker",
        worker_version="0.1.0",
        markdown="# Article\n\n" + "Useful public article content. " * 30 + "\n\nSign in",
        metadata={"source_url": "https://example.com/article", "final_url": "https://example.com/article"},
    )
    input = WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path="url.json",
        mime_type="application/json",
        source_type="url",
        source_url="https://example.com/article",
    )

    quality = check_quality(result, input)

    assert quality.metrics["auth_wall_detected"] is False
    assert "auth_wall_detected" not in quality.warnings
    assert quality.score >= 0.75


def test_platform_quality_rejects_chatgpt_share_without_messages():
    result = WorkerResult(
        status="success",
        worker_name="playwright_worker",
        worker_version="0.1.0",
        markdown="# Shared Conversation\n\nSign in to view this conversation.",
        metadata={
            "source_url": "https://chatgpt.com/share/abc",
            "final_url": "https://chatgpt.com/share/abc",
            "url_kind": "chatgpt_share",
            "platform": "chatgpt",
            "message_count": 0,
        },
    )
    input = WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path="url.json",
        mime_type="application/json",
        source_type="url",
        source_url="https://chatgpt.com/share/abc",
    )

    quality = check_quality(result, input)

    assert quality.score < 0.45
    assert quality.metrics["platform_required_content_missing"] == "platform_missing_messages"
    assert "platform_missing_messages" in quality.warnings


def test_platform_quality_accepts_short_complete_conversation_extraction():
    result = WorkerResult(
        status="success",
        worker_name="gemini_share_worker",
        worker_version="0.1.0",
        markdown="# Shared Conversation\n\n## user\n\nWhat is an ADR?\n\n## assistant\n\nA decision record.",
        metadata={
            "source_url": "https://gemini.google.com/share/abc",
            "final_url": "https://gemini.google.com/share/abc",
            "url_kind": "gemini_share",
            "platform": "gemini",
            "message_count": 2,
            "text_char_count": 37,
            "platform_extraction_schema": "platform_extraction.v1",
        },
    )
    input = WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path="url.json",
        mime_type="application/json",
        source_type="url",
        source_url="https://gemini.google.com/share/abc",
    )

    quality = check_quality(result, input)

    assert quality.score >= 0.75
    assert "text_length_below_300" not in quality.warnings


def test_platform_quality_rejects_youtube_page_without_transcript():
    result = WorkerResult(
        status="success",
        worker_name="playwright_worker",
        worker_version="0.1.0",
        markdown="# Video Title\n\nDescription only. " * 40,
        metadata={
            "source_url": "https://www.youtube.com/watch?v=abc",
            "final_url": "https://www.youtube.com/watch?v=abc",
            "url_kind": "youtube_video",
            "platform": "youtube",
            "subtitle_segment_count": 0,
        },
    )
    input = WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path="url.json",
        mime_type="application/json",
        source_type="url",
        source_url="https://www.youtube.com/watch?v=abc",
    )

    quality = check_quality(result, input)

    assert quality.score < 0.45
    assert quality.metrics["platform_required_content_missing"] == "platform_no_transcript"
    assert "platform_no_transcript" in quality.warnings
