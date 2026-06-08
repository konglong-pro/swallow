from __future__ import annotations

from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.url_classifier import UrlKind, classify_url
from swallow.workers.base import BaseWorker
from swallow.workers.platform_common import (
    build_platform_extraction,
    classify_platform_page_state,
    extract_article_by_selectors,
    extract_wechat_article,
    metadata_from_extraction,
    PlatformPageState,
    render_platform_markdown,
    render_platform_page,
    write_platform_artifacts,
)
from swallow.workers.web_common import get_url, short_error


class PlatformArticleWorker(BaseWorker):
    platform: str
    url_kind: UrlKind
    content_type = "article"
    title_ids: tuple[str, ...] = ()
    content_ids: tuple[str, ...] = ()
    title_selectors: tuple[str, ...] = ()
    content_selectors: tuple[str, ...] = ()
    author_selectors: tuple[str, ...] = ()
    published_selectors: tuple[str, ...] = ()
    min_content_chars = 80

    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["article_markdown", "platform_extraction", "browser_render"],
        cost_level="medium",
        requires_gpu=False,
        requires_network=True,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        url = get_url(input)
        return input.source_type == "url" and bool(url) and classify_url(str(url)).kind == self.url_kind

    def run(self, input: WorkerInput) -> WorkerResult:
        url = get_url(input)
        if not url:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_worker_input: source_url"],
            )

        timeout_seconds = to_positive_int(input.metadata.get("timeout_seconds"), default=120)
        headless = to_bool(input.metadata.get("headless"), default=True)
        try:
            page = render_platform_page(str(url), headless=headless, timeout_seconds=timeout_seconds)
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra web`"],
                metadata={"platform": self.platform, "url_kind": self.url_kind.value, "source_url": url},
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"{self.platform}_article_render_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={"platform": self.platform, "url_kind": self.url_kind.value, "source_url": url},
            )

        extracted = self.extract(page.html)
        title = extracted.get("title") or page.title or f"{self.platform.title()} Article"
        text = str(extracted.get("text") or "")
        page_state = classify_platform_page_state(page.html, text=text, status_code=page.status_code) if len(text) < self.min_content_chars else PlatformPageState("ok")
        quality_flags: list[str] = []
        if len(text) < self.min_content_chars:
            quality_flags.append("content_too_short")
        if page_state.warning:
            quality_flags.append(page_state.warning)

        extraction = build_platform_extraction(
            platform=self.platform,
            url_kind=self.url_kind.value,
            source_url=str(url),
            final_url=page.final_url,
            title=str(title),
            authors=extracted.get("authors") if isinstance(extracted.get("authors"), list) else [],
            published_at=extracted.get("published_at") if isinstance(extracted.get("published_at"), str) else None,
            content={
                "type": self.content_type,
                "text_char_count": len(text),
                "markdown": extracted.get("markdown") or "",
                "text": text,
            },
            assets=extracted.get("assets") if isinstance(extracted.get("assets"), list) else [],
            method="playwright_dom",
            auth_mode=page_state.auth_mode,
            page_state=page_state.state,
            worker=self.name,
            quality_flags=quality_flags,
        )
        artifacts = write_platform_artifacts(input, platform=self.platform, extraction=extraction, rendered_html=page.html)
        metadata = metadata_from_extraction(extraction, status_code=page.status_code, html_length=len(page.html))
        markdown = render_platform_markdown(extraction)

        if len(text) < self.min_content_chars:
            error_code = page_state.error_code or "PLATFORM_CONTENT_TOO_SHORT"
            warning = page_state.warning or "platform_content_too_short"
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                markdown=markdown,
                title=str(title),
                artifacts=artifacts,
                metadata={**metadata, "error_code": error_code},
                warnings=[warning],
                errors=[f"{self.platform}_{self.content_type}_{page_state.state}"],
            )

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=str(title),
            artifacts=artifacts,
            metadata=metadata,
        )

    def extract(self, html_text: str) -> dict[str, Any]:
        return extract_article_by_selectors(
            html_text,
            title_ids=self.title_ids,
            content_ids=self.content_ids,
            title_selectors=self.title_selectors,
            content_selectors=self.content_selectors,
            author_selectors=self.author_selectors,
            published_selectors=self.published_selectors,
        )


class WeChatArticleWorker(PlatformArticleWorker):
    name = "wechat_article_worker"
    platform = "wechat"
    url_kind = UrlKind.WECHAT_ARTICLE
    title_ids = ("activity-name",)
    content_ids = ("js_content",)

    def extract(self, html_text: str) -> dict[str, Any]:
        return extract_wechat_article(html_text)


def to_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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
