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
