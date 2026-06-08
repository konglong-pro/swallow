from __future__ import annotations

from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.url_classifier import UrlKind, classify_url
from swallow.workers.base import BaseWorker
from swallow.workers.platform_common import (
    build_platform_extraction,
    classify_platform_page_state,
    find_conversation_messages,
    metadata_from_extraction,
    PlatformPageState,
    render_platform_markdown,
    render_platform_page,
    render_platform_page_dom_ready,
    write_platform_artifacts,
)
from swallow.workers.web_common import get_url, short_error


class ConversationShareWorker(BaseWorker):
    platform: str
    url_kind: UrlKind
    worker_strength: str
    dom_ready_render = False
    post_load_wait_ms = 5_000

    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["conversation_markdown", "share_link", "platform_extraction"],
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
        post_load_wait_ms = to_positive_int(input.metadata.get("post_load_wait_ms"), default=self.post_load_wait_ms)
        try:
            if self.dom_ready_render:
                page = render_platform_page_dom_ready(
                    str(url),
                    headless=headless,
                    timeout_seconds=timeout_seconds,
                    post_load_wait_ms=post_load_wait_ms,
                )
            else:
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
                errors=[f"{self.platform}_share_render_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={"platform": self.platform, "url_kind": self.url_kind.value, "source_url": url},
            )

        title, messages = find_conversation_messages(page.html, platform=self.platform)
        title = title or page.title or f"{self.platform.title()} Shared Conversation"
        page_state = classify_platform_page_state(page.html, status_code=page.status_code) if not messages else PlatformPageState("ok")
        quality_flags: list[str] = []
        if not messages:
            quality_flags.append("missing_messages")
        if page_state.warning:
            quality_flags.append(page_state.warning)

        extraction = build_platform_extraction(
            platform=self.platform,
            url_kind=self.url_kind.value,
            source_url=str(url),
            final_url=page.final_url,
            title=title,
            content={
                "type": "conversation",
                "text_char_count": sum(len(message["content"]) for message in messages),
                "message_count": len(messages),
                "messages": messages,
                "share_scope": "unknown",
            },
            method="playwright_dom",
            auth_mode=page_state.auth_mode,
            page_state=page_state.state,
            worker=self.name,
            quality_flags=quality_flags,
        )
        artifacts = write_platform_artifacts(input, platform=self.platform, extraction=extraction, rendered_html=page.html)
        metadata = metadata_from_extraction(extraction, status_code=page.status_code, html_length=len(page.html))
        markdown = render_platform_markdown(extraction)

        if not messages:
            error_code = page_state.error_code or "PLATFORM_NO_MESSAGES"
            warning = page_state.warning or "platform_missing_messages"
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                markdown=markdown,
                title=title,
                artifacts=artifacts,
                metadata={**metadata, "error_code": error_code},
                warnings=[warning],
                errors=[f"{self.platform}_share_{page_state.state}"],
            )

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=title,
            artifacts=artifacts,
            metadata=metadata,
        )


class ChatGPTShareWorker(ConversationShareWorker):
    name = "chatgpt_share_worker"
    platform = "chatgpt"
    url_kind = UrlKind.CHATGPT_SHARE
    worker_strength = "chatgpt_share"


class GeminiShareWorker(ConversationShareWorker):
    name = "gemini_share_worker"
    platform = "gemini"
    url_kind = UrlKind.GEMINI_SHARE
    worker_strength = "gemini_share"


class ClaudeShareWorker(ConversationShareWorker):
    name = "claude_share_worker"
    platform = "claude"
    url_kind = UrlKind.CLAUDE_SHARE
    worker_strength = "claude_share"
    dom_ready_render = True


class DeepSeekShareWorker(ConversationShareWorker):
    name = "deepseek_share_worker"
    platform = "deepseek"
    url_kind = UrlKind.DEEPSEEK_SHARE
    worker_strength = "deepseek_share"


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
