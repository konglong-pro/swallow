from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from swallow.cli.main import app
from swallow.core.errors import QualityError
from swallow.core.models import WorkerInput
from swallow.core.runner import IngestRunner
from swallow.workers import crawl4ai_worker as crawl4ai_module
from swallow.workers import firecrawl_worker as firecrawl_module
from swallow.workers import playwright_profile_worker as playwright_profile_module
from swallow.workers import playwright_worker as playwright_module
from swallow.workers.crawl4ai_worker import Crawl4AIWorker
from swallow.workers.firecrawl_worker import FirecrawlWorker
from swallow.workers.playwright_profile_worker import PlaywrightProfileWorker
from swallow.workers.playwright_worker import PlaywrightWorker, RenderedPage


def make_url_input(url: str, job_dir: Path, *, metadata: dict[str, object] | None = None) -> WorkerInput:
    values: dict[str, object] = {"original_filename": "example.url", "job_dir": str(job_dir)}
    if metadata:
        values.update(metadata)
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(job_dir / "raw_url.json"),
        mime_type="application/json",
        source_type="url",
        source_url=url,
        metadata=values,
    )


def test_firecrawl_worker_returns_markdown_and_html_artifact(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_scrape(url: str, *, api_key_env: str = "FIRECRAWL_API_KEY"):
        seen["url"] = url
        seen["api_key_env"] = api_key_env
        return {
            "markdown": "# Example\n\n" + "Useful content. " * 30,
            "html": "<html><body><h1>Example</h1></body></html>",
            "metadata": {"title": "Example", "statusCode": 200},
        }

    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        fake_scrape,
    )

    result = FirecrawlWorker().run(
        make_url_input(
            "https://example.com/article",
            tmp_path / "job",
            metadata={"api_key_env": "CUSTOM_FIRECRAWL_KEY"},
        )
    )

    assert result.status == "success"
    assert seen == {"url": "https://example.com/article", "api_key_env": "CUSTOM_FIRECRAWL_KEY"}
    assert result.title == "Example"
    assert result.metadata["crawler"] == "firecrawl"
    assert result.metadata["api_key_env"] == "CUSTOM_FIRECRAWL_KEY"
    assert result.metadata["status_code"] == 200
    assert result.artifacts == [{"path": "intermediate/firecrawl/page.html", "type": "html"}]
    assert (tmp_path / "job" / "intermediate" / "firecrawl" / "page.html").exists()


def test_firecrawl_worker_reports_missing_dependency(monkeypatch, tmp_path):
    def missing_dependency(url: str, **_: object):
        raise ImportError("firecrawl missing")

    monkeypatch.setattr(firecrawl_module, "firecrawl_scrape", missing_dependency)

    result = FirecrawlWorker().run(make_url_input("https://example.com/article", tmp_path / "job"))

    assert result.status == "failed"
    assert result.errors == ["missing_optional_dependency: install with `uv sync --extra web`"]


def test_crawl4ai_worker_returns_markdown_and_html_artifact(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_fetch(url: str, *, timeout_seconds: int = 180):
        seen["url"] = url
        seen["timeout_seconds"] = timeout_seconds
        return SimpleNamespace(
            markdown="# Example\n\n" + "Rendered content. " * 30,
            html="<html><body><h1>Example</h1></body></html>",
            metadata={"title": "Rendered", "status_code": 200},
        )

    monkeypatch.setattr(
        crawl4ai_module,
        "crawl4ai_fetch",
        fake_fetch,
    )

    result = Crawl4AIWorker().run(
        make_url_input("https://app.example.com/page", tmp_path / "job", metadata={"timeout_seconds": 7})
    )

    assert result.status == "success"
    assert seen == {"url": "https://app.example.com/page", "timeout_seconds": 7}
    assert result.title == "Rendered"
    assert result.metadata["crawler"] == "crawl4ai"
    assert result.metadata["timeout_seconds"] == 7
    assert result.artifacts == [{"path": "intermediate/crawl4ai/page.html", "type": "html"}]
    assert (tmp_path / "job" / "intermediate" / "crawl4ai" / "page.html").exists()


def test_playwright_worker_returns_markdown_and_render_artifacts(monkeypatch, tmp_path):
    screenshot_path = tmp_path / "job" / "intermediate" / "playwright" / "screenshot.png"
    seen: dict[str, object] = {}

    def fake_render(
        url: str,
        *,
        job_dir: Path | None = None,
        headless: bool = True,
        screenshot: bool = True,
        timeout_seconds: int = 240,
    ) -> RenderedPage:
        seen["url"] = url
        seen["headless"] = headless
        seen["screenshot"] = screenshot
        seen["timeout_seconds"] = timeout_seconds
        html_path = job_dir / "intermediate" / "playwright" / "rendered.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html = "<html><head><title>Rendered</title></head><body><h1>Rendered</h1><p>Dynamic content.</p></body></html>"
        html_path.write_text(html, encoding="utf-8")
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(b"fake png")
        return RenderedPage(
            html=html,
            title="Rendered",
            final_url="https://example.com/rendered",
            status_code=200,
            html_path=html_path,
            screenshot_path=screenshot_path,
        )

    monkeypatch.setattr(playwright_module, "render_with_playwright", fake_render)

    result = PlaywrightWorker().run(
        make_url_input(
            "https://example.com/app",
            tmp_path / "job",
            metadata={"headless": False, "screenshot": True, "timeout_seconds": 11},
        )
    )

    assert result.status == "success"
    assert seen == {
        "url": "https://example.com/app",
        "headless": False,
        "screenshot": True,
        "timeout_seconds": 11,
    }
    assert result.title == "Rendered"
    assert result.metadata["crawler"] == "playwright"
    assert result.metadata["rendered"] is True
    assert result.metadata["final_url"] == "https://example.com/rendered"
    assert result.metadata["headless"] is False
    assert result.metadata["screenshot"] is True
    assert result.metadata["timeout_seconds"] == 11
    assert "# Rendered" in result.markdown
    assert result.artifacts == [
        {"type": "rendered_html", "path": "intermediate/playwright/rendered.html"},
        {"type": "screenshot", "path": "intermediate/playwright/screenshot.png"},
    ]


def test_playwright_worker_can_disable_screenshot(monkeypatch, tmp_path):
    def fake_render(
        url: str,
        *,
        job_dir: Path | None = None,
        headless: bool = True,
        screenshot: bool = True,
        timeout_seconds: int = 240,
    ) -> RenderedPage:
        assert screenshot is False
        html_path = job_dir / "intermediate" / "playwright" / "rendered.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html = "<html><body><h1>No Screenshot</h1><p>Rendered content.</p></body></html>"
        html_path.write_text(html, encoding="utf-8")
        return RenderedPage(html=html, title="No Screenshot", final_url=url, status_code=200, html_path=html_path)

    monkeypatch.setattr(playwright_module, "render_with_playwright", fake_render)

    result = PlaywrightWorker().run(
        make_url_input("https://example.com/app", tmp_path / "job", metadata={"screenshot": False})
    )

    assert result.status == "success"
    assert result.metadata["screenshot"] is False
    assert result.artifacts == [{"type": "rendered_html", "path": "intermediate/playwright/rendered.html"}]


def test_playwright_worker_reports_missing_dependency(monkeypatch, tmp_path):
    def missing_dependency(
        url: str,
        *,
        job_dir: Path | None = None,
        headless: bool = True,
        screenshot: bool = True,
        timeout_seconds: int = 240,
    ):
        raise ImportError("playwright missing")

    monkeypatch.setattr(playwright_module, "render_with_playwright", missing_dependency)

    result = PlaywrightWorker().run(make_url_input("https://example.com/app", tmp_path / "job"))

    assert result.status == "failed"
    assert result.errors == ["missing_optional_dependency: install with `uv sync --extra web`"]


def test_playwright_profile_worker_returns_markdown_and_render_artifacts(monkeypatch, tmp_path):
    screenshot_path = tmp_path / "job" / "intermediate" / "playwright_profile" / "screenshot.png"
    profile_dir = tmp_path / "profile"
    seen: dict[str, object] = {}

    def fake_render(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        headless: bool = False,
        screenshot: bool = True,
        timeout_seconds: int = 240,
    ) -> RenderedPage:
        seen["url"] = url
        seen["profile_dir"] = profile_dir
        seen["headless"] = headless
        seen["screenshot"] = screenshot
        seen["timeout_seconds"] = timeout_seconds
        html_path = job_dir / "intermediate" / "playwright_profile" / "rendered.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html = (
            "<html><head><title>Profile</title></head>"
            "<body><h1>Profile Page</h1><p>Authenticated content.</p></body></html>"
        )
        html_path.write_text(html, encoding="utf-8")
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(b"fake png")
        return RenderedPage(
            html=html,
            title="Profile Page",
            final_url="https://chatgpt.com/c/example",
            status_code=200,
            html_path=html_path,
            screenshot_path=screenshot_path,
        )

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", fake_render)

    result = PlaywrightProfileWorker().run(
        make_url_input(
            "https://chatgpt.com/c/example",
            tmp_path / "job",
            metadata={"profile_dir": str(profile_dir), "headless": True, "screenshot": True, "timeout_seconds": 13},
        )
    )

    assert result.status == "success"
    assert seen == {
        "url": "https://chatgpt.com/c/example",
        "profile_dir": profile_dir,
        "headless": True,
        "screenshot": True,
        "timeout_seconds": 13,
    }
    assert result.title == "Profile Page"
    assert result.metadata["crawler"] == "playwright_profile"
    assert result.metadata["profile_dir"] == str(profile_dir)
    assert result.metadata["headless"] is True
    assert result.metadata["timeout_seconds"] == 13
    assert "# Profile Page" in result.markdown
    assert result.artifacts == [
        {"type": "rendered_html", "path": "intermediate/playwright_profile/rendered.html"},
        {"type": "screenshot", "path": "intermediate/playwright_profile/screenshot.png"},
    ]


def test_playwright_profile_worker_extracts_platform_share_messages(monkeypatch, tmp_path):
    html = (Path(__file__).parent / "fixtures" / "web" / "claude_share_visible_text.html").read_text(encoding="utf-8")
    profile_dir = tmp_path / "profile"
    seen: dict[str, object] = {}

    def fake_render(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        headless: bool = False,
        screenshot: bool = True,
        timeout_seconds: int = 240,
        wait_until: str = "networkidle",
        post_load_wait_ms: int = 0,
    ) -> RenderedPage:
        seen["wait_until"] = wait_until
        seen["post_load_wait_ms"] = post_load_wait_ms
        html_path = job_dir / "intermediate" / "playwright_profile" / "rendered.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        return RenderedPage(
            html=html,
            title="Claude",
            final_url=url,
            status_code=200,
            html_path=html_path,
        )

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", fake_render)

    result = PlaywrightProfileWorker().run(
        make_url_input(
            "https://claude.ai/share/abc",
            tmp_path / "job",
            metadata={"profile_dir": str(profile_dir), "headless": True, "screenshot": False},
        )
    )

    assert result.status == "success"
    assert seen == {"wait_until": "domcontentloaded", "post_load_wait_ms": 5000}
    assert result.metadata["platform"] == "claude"
    assert result.metadata["url_kind"] == "claude_share"
    assert result.metadata["auth_mode"] == "local_profile"
    assert result.metadata["message_count"] == 2
    assert "To evolve, you must first learn to bury the one you were." in result.markdown
    assert {"type": "platform_extraction", "path": "intermediate/claude/platform_extraction.json"} in result.artifacts


def test_playwright_profile_worker_rejects_platform_share_without_messages(monkeypatch, tmp_path):
    html = "<html><head><title>Just a moment...</title></head><body><main>Please enable JavaScript.</main></body></html>"

    def fake_render(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        headless: bool = False,
        screenshot: bool = True,
        timeout_seconds: int = 240,
        wait_until: str = "networkidle",
        post_load_wait_ms: int = 0,
    ) -> RenderedPage:
        return RenderedPage(html=html, title="Just a moment...", final_url=url, status_code=200)

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", fake_render)

    result = PlaywrightProfileWorker().run(make_url_input("https://claude.ai/share/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_BROWSER_RENDER_REQUIRED"
    assert result.metadata["message_count"] == 0
    assert "needs_browser_render" in result.warnings
    assert "# Just a moment..." in result.markdown


def test_playwright_profile_worker_reports_missing_dependency(monkeypatch, tmp_path):
    def missing_dependency(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        headless: bool = False,
        screenshot: bool = True,
        timeout_seconds: int = 240,
    ):
        raise ImportError("playwright missing")

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", missing_dependency)

    result = PlaywrightProfileWorker().run(make_url_input("https://chatgpt.com/c/example", tmp_path / "job"))

    assert result.status == "failed"
    assert result.errors == ["missing_optional_dependency: install with `uv sync --extra web`"]
    assert result.metadata["crawler"] == "playwright_profile"


def test_url_ingest_uses_crawl4ai_fallback_when_firecrawl_quality_is_low(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "too short",
            "html": "<html></html>",
            "metadata": {"title": "Short", "statusCode": 200},
        },
    )
    monkeypatch.setattr(
        crawl4ai_module,
        "crawl4ai_fetch",
        lambda url, **_: SimpleNamespace(
            markdown="# Fallback Article\n\n" + "Fallback content. " * 40,
            html="<html><body>Fallback content</body></html>",
            metadata={"title": "Fallback Article", "status_code": 200},
        ),
    )

    result = IngestRunner(store_root=tmp_path / "store").ingest_url("https://example.com/article")

    assert result.document.source.source_type == "url"
    assert result.document.source.source_url == "https://example.com/article"
    assert result.document.provenance.primary_worker == "crawl4ai_worker"
    assert "firecrawl_worker@0.1.0" in result.document.provenance.worker_chain
    assert "crawl4ai_worker@0.1.0" in result.document.provenance.worker_chain
    assert result.document.quality.score >= 0.75
    assert "Fallback content." in result.document.content.markdown
    assert (tmp_path / "store" / result.document_path).exists()


def test_url_ingest_uses_playwright_fallback_when_crawl4ai_quality_is_low(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "too short",
            "html": "<html></html>",
            "metadata": {"title": "Short", "statusCode": 200},
        },
    )
    monkeypatch.setattr(
        crawl4ai_module,
        "crawl4ai_fetch",
        lambda url, **_: SimpleNamespace(
            markdown="still short",
            html="<html><body>still short</body></html>",
            metadata={"title": "Still Short", "status_code": 200},
        ),
    )

    def fake_render(url: str, *, job_dir: Path | None = None, **_: object) -> RenderedPage:
        return RenderedPage(
            html="<html><body><h1>Rendered Article</h1><p>" + "Rendered content. " * 40 + "</p></body></html>",
            title="Rendered Article",
            final_url=url,
            status_code=200,
        )

    monkeypatch.setattr(playwright_module, "render_with_playwright", fake_render)

    result = IngestRunner(store_root=tmp_path / "store").ingest_url("https://example.com/article")

    assert result.document.provenance.primary_worker == "playwright_worker"
    assert "firecrawl_worker@0.1.0" in result.document.provenance.worker_chain
    assert "crawl4ai_worker@0.1.0" in result.document.provenance.worker_chain
    assert "playwright_worker@0.1.0" in result.document.provenance.worker_chain
    assert result.document.quality.score >= 0.75
    assert "Rendered content." in result.document.content.markdown


def test_login_url_ingest_uses_playwright_profile_worker(monkeypatch, tmp_path):
    def fake_profile_render(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        **_: object,
    ) -> RenderedPage:
        return RenderedPage(
            html="<html><body><h1>Captured Conversation</h1><p>" + "Logged-in content. " * 40 + "</p></body></html>",
            title="Captured Conversation",
            final_url=url,
            status_code=200,
        )

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", fake_profile_render)

    result = IngestRunner(store_root=tmp_path / "store").ingest_url("https://chatgpt.com/c/example")

    assert result.document.provenance.primary_worker == "playwright_profile_worker"
    assert "playwright_profile_worker@0.1.0" in result.document.provenance.worker_chain
    assert result.document.quality.score >= 0.75
    assert "Logged-in content." in result.document.content.markdown


def test_login_url_auth_wall_falls_back_to_playwright(monkeypatch, tmp_path):
    def fake_profile_render(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        **_: object,
    ) -> RenderedPage:
        return RenderedPage(
            html="<html><body><h1>Log in</h1><p>Please log in to continue. Continue with Google.</p></body></html>",
            title="Log in",
            final_url="https://chatgpt.com/auth/login",
            status_code=200,
        )

    def fake_render(url: str, *, job_dir: Path | None = None, **_: object) -> RenderedPage:
        return RenderedPage(
            html="<html><body><h1>Fallback Capture</h1><p>" + "Rendered content. " * 40 + "</p></body></html>",
            title="Fallback Capture",
            final_url=url,
            status_code=200,
        )

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", fake_profile_render)
    monkeypatch.setattr(playwright_module, "render_with_playwright", fake_render)

    result = IngestRunner(store_root=tmp_path / "store").ingest_url("https://chatgpt.com/c/example")

    assert result.document.provenance.primary_worker == "playwright_worker"
    assert "playwright_profile_worker@0.1.0" in result.document.provenance.worker_chain
    assert "playwright_worker@0.1.0" in result.document.provenance.worker_chain
    assert result.document.quality.score >= 0.75
    assert "Rendered content." in result.document.content.markdown


def test_login_url_all_auth_walls_fail_quality(monkeypatch, tmp_path):
    auth_html = (
        "<html><body><h1>Log in</h1>"
        "<p>Please log in to continue. Continue with Google. Forgot password?</p></body></html>"
    )

    def fake_profile_render(
        url: str,
        *,
        profile_dir: Path,
        job_dir: Path | None = None,
        **_: object,
    ) -> RenderedPage:
        return RenderedPage(html=auth_html, title="Log in", final_url="https://chatgpt.com/auth/login", status_code=200)

    def fake_render(url: str, *, job_dir: Path | None = None, **_: object) -> RenderedPage:
        return RenderedPage(html=auth_html, title="Log in", final_url="https://chatgpt.com/auth/login", status_code=200)

    monkeypatch.setattr(playwright_profile_module, "render_with_playwright_profile", fake_profile_render)
    monkeypatch.setattr(playwright_module, "render_with_playwright", fake_render)
    runner = IngestRunner(store_root=tmp_path / "store")

    with pytest.raises(QualityError) as error:
        runner.ingest_url("https://chatgpt.com/c/example")

    assert error.value.code == "QUALITY_BELOW_THRESHOLD"
    job_dir = next((tmp_path / "store" / "jobs").iterdir())
    job_json = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job_json["status"] == "failed"
    assert job_json["error"]["code"] == "QUALITY_BELOW_THRESHOLD"


def test_url_rerun_preserves_source_url(monkeypatch, tmp_path):
    seen: list[str] = []

    def fake_scrape(url: str, **_: object):
        seen.append(url)
        return {
            "markdown": "# Rerun Article\n\n" + "Rerun content. " * 40,
            "html": "<html><body>Rerun content</body></html>",
            "metadata": {"title": "Rerun Article", "statusCode": 200},
        }

    monkeypatch.setattr(firecrawl_module, "firecrawl_scrape", fake_scrape)
    runner = IngestRunner(store_root=tmp_path / "store")

    first = runner.ingest_url("https://example.com/article")
    rerun = runner.rerun_job(first.job.id)

    assert seen == ["https://example.com/article", "https://example.com/article"]
    assert rerun.document.source.source_type == "url"
    assert rerun.document.source.source_url == "https://example.com/article"
    assert rerun.document.raw_id == first.document.raw_id
    assert rerun.job.id != first.job.id


def test_url_rerun_rejects_incompatible_forced_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "# Rerun Article\n\n" + "Rerun content. " * 40,
            "html": "<html><body>Rerun content</body></html>",
            "metadata": {"title": "Rerun Article", "statusCode": 200},
        },
    )
    runner = IngestRunner(store_root=tmp_path / "store")
    first = runner.ingest_url("https://example.com/article")

    try:
        runner.rerun_job(first.job.id, worker_name="plain_text_worker")
    except Exception as error:
        assert str(error) == "Worker cannot handle source: plain_text_worker for url https://example.com/article"
    else:
        raise AssertionError("Expected rerun to reject incompatible forced worker")

    assert len(list((tmp_path / "store" / "jobs").iterdir())) == 1


def test_cli_url_ingest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        firecrawl_module,
        "firecrawl_scrape",
        lambda url, **_: {
            "markdown": "# CLI Article\n\n" + "CLI content. " * 40,
            "html": "<html><body>CLI content</body></html>",
            "metadata": {"title": "CLI Article", "statusCode": 200},
        },
    )

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "url", "https://example.com/article"])

    assert result.exit_code == 0, result.output
    assert "Status: success" in result.output
    assert "Document:" in result.output
    jobs = list((tmp_path / "jobs").iterdir())
    assert len(jobs) == 1
    assert (jobs[0] / "document.md").exists()
    assert (jobs[0] / "ingest_document.json").exists()
    assert (jobs[0] / "trace.jsonl").exists()
