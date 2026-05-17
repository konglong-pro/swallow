from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.workers.base import BaseWorker
from swallow.workers.web_common import get_job_dir, get_url, save_text_artifact, short_error


class PlaywrightWorker(BaseWorker):
    name = "playwright_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["browser_render", "dynamic_page", "screenshot", "lazy_loaded_content"],
        cost_level="medium",
        requires_gpu=False,
        requires_network=True,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "url" and bool(input.source_url)

    def run(self, input: WorkerInput) -> WorkerResult:
        url = get_url(input)
        if not url:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_worker_input: source_url"],
            )

        job_dir = get_job_dir(input)
        headless = to_bool(input.metadata.get("headless"), default=True)
        screenshot = to_bool(input.metadata.get("screenshot"), default=True)
        timeout_seconds = to_positive_int(input.metadata.get("timeout_seconds"), default=240)
        try:
            page = render_with_playwright(
                url,
                job_dir=job_dir,
                headless=headless,
                screenshot=screenshot,
                timeout_seconds=timeout_seconds,
            )
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra web`"],
                metadata={
                    "crawler": "playwright",
                    "source_url": url,
                    "headless": headless,
                    "screenshot": screenshot,
                    "timeout_seconds": timeout_seconds,
                },
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"playwright_render_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={
                    "crawler": "playwright",
                    "source_url": url,
                    "headless": headless,
                    "screenshot": screenshot,
                    "timeout_seconds": timeout_seconds,
                },
            )

        markdown = html_to_markdown(page.html)
        if not markdown.strip():
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["playwright_empty_markdown"],
                metadata={
                    "crawler": "playwright",
                    "source_url": url,
                    "final_url": page.final_url,
                    "headless": headless,
                    "screenshot": screenshot,
                    "timeout_seconds": timeout_seconds,
                },
            )

        artifacts: list[dict[str, Any]] = []
        if job_dir is not None:
            html_path = page.html_path
            if html_path is None:
                artifact = save_text_artifact(job_dir, "intermediate/playwright/rendered.html", page.html)
                artifact["type"] = "rendered_html"
                artifacts.append(artifact)
            else:
                artifacts.append({"type": "rendered_html", "path": relative_artifact_path(html_path, job_dir)})

            if page.screenshot_path is not None:
                artifacts.append({"type": "screenshot", "path": relative_artifact_path(page.screenshot_path, job_dir)})

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=page.title,
            artifacts=artifacts,
            metadata={
                "crawler": "playwright",
                "source_url": url,
                "rendered": True,
                "final_url": page.final_url,
                "status_code": page.status_code,
                "html_length": len(page.html),
                "headless": headless,
                "screenshot": screenshot,
                "timeout_seconds": timeout_seconds,
            },
            warnings=page.warnings,
        )


@dataclass
class RenderedPage:
    html: str
    title: str | None = None
    final_url: str | None = None
    status_code: int | None = None
    html_path: Path | None = None
    screenshot_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def render_with_playwright(
    url: str,
    *,
    job_dir: Path | None = None,
    headless: bool = True,
    screenshot: bool = True,
    timeout_seconds: int = 240,
) -> RenderedPage:
    from playwright.sync_api import sync_playwright

    html_path = job_dir / "intermediate" / "playwright" / "rendered.html" if job_dir else None
    screenshot_path = job_dir / "intermediate" / "playwright" / "screenshot.png" if job_dir and screenshot else None
    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=timeout_seconds * 1000)
            auto_scroll(page)
            click_expand_controls(page)
            html = page.content()
            title = page.title()
            final_url = page.url
            status_code = response.status if response is not None else None
            if html_path is not None:
                html_path.write_text(html, encoding="utf-8")
            if screenshot_path is not None:
                page.screenshot(path=str(screenshot_path), full_page=True)
        finally:
            browser.close()

    if not html.strip():
        warnings.append("rendered_html_empty")

    return RenderedPage(
        html=html,
        title=title,
        final_url=final_url,
        status_code=status_code,
        html_path=html_path,
        screenshot_path=screenshot_path if screenshot_path and screenshot_path.exists() else None,
        warnings=warnings,
    )


def auto_scroll(page: Any, *, max_scrolls: int = 8) -> None:
    for _ in range(max_scrolls):
        page.evaluate("window.scrollBy(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))")
        page.wait_for_timeout(250)


def click_expand_controls(page: Any) -> None:
    selectors = [
        "button:has-text('Read more')",
        "button:has-text('Show more')",
        "button:has-text('\\u5c55\\u5f00')",
        "button:has-text('\\u663e\\u793a\\u66f4\\u591a')",
        "text=\\u9605\\u8bfb\\u5168\\u6587",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                locator.nth(index).click(timeout=1_000)
        except Exception:
            continue


def html_to_markdown(html: str) -> str:
    try:
        from markdownify import markdownify
    except ImportError:
        return fallback_html_to_text_markdown(html)

    markdown = markdownify(html, heading_style="ATX")
    return normalize_markdown(markdown)


def fallback_html_to_text_markdown(html: str) -> str:
    parser = TextExtractingHtmlParser()
    parser.feed(html)
    return normalize_markdown("\n".join(parser.parts))


class TextExtractingHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)


def normalize_markdown(markdown: str) -> str:
    normalized = re.sub(r"\n{3,}", "\n\n", markdown.replace("\r\n", "\n").replace("\r", "\n"))
    return normalized.strip() + "\n"


def relative_artifact_path(path: Path, job_dir: Path) -> str:
    return path.relative_to(job_dir).as_posix()


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
