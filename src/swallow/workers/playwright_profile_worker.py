from __future__ import annotations

from pathlib import Path
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.url_classifier import UrlKind, classify_url
from swallow.workers.base import BaseWorker
from swallow.workers.platform_common import (
    PlatformPageState,
    build_platform_extraction,
    classify_platform_page_state,
    find_conversation_messages,
    metadata_from_extraction,
    render_platform_markdown,
    write_platform_artifacts,
)
from swallow.workers.playwright_worker import (
    RenderedPage,
    auto_scroll,
    click_expand_controls,
    html_to_markdown,
    relative_artifact_path,
    to_bool,
    to_positive_int,
)
from swallow.workers.web_common import get_job_dir, get_url, save_text_artifact, short_error


DEFAULT_PROFILE_DIR = "~/.swallow/browser-profiles/chrome-default"
PROFILE_PLATFORM_CONVERSATION_KINDS = {
    UrlKind.CHATGPT_SHARE,
    UrlKind.GEMINI_SHARE,
    UrlKind.CLAUDE_SHARE,
    UrlKind.DEEPSEEK_SHARE,
}


class PlaywrightProfileWorker(BaseWorker):
    name = "playwright_profile_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["local_browser_profile", "login_state_capture", "browser_render", "screenshot"],
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
        profile_dir = resolve_profile_dir(input.metadata.get("profile_dir"))
        headless = to_bool(input.metadata.get("headless"), default=False)
        screenshot = to_bool(input.metadata.get("screenshot"), default=True)
        timeout_seconds = to_positive_int(input.metadata.get("timeout_seconds"), default=240)
        classification = classify_url(url)
        platform_share = classification.kind in PROFILE_PLATFORM_CONVERSATION_KINDS
        base_metadata = {
            "crawler": "playwright_profile",
            "source_url": url,
            "profile_dir": str(profile_dir),
            "headless": headless,
            "screenshot": screenshot,
            "timeout_seconds": timeout_seconds,
        }

        try:
            render_kwargs = {
                "profile_dir": profile_dir,
                "job_dir": job_dir,
                "headless": headless,
                "screenshot": screenshot,
                "timeout_seconds": timeout_seconds,
            }
            if platform_share:
                render_kwargs["wait_until"] = str(input.metadata.get("wait_until") or "domcontentloaded")
                render_kwargs["post_load_wait_ms"] = to_positive_int(
                    input.metadata.get("post_load_wait_ms"),
                    default=5_000,
                )
            page = render_with_playwright_profile(url, **render_kwargs)
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra web`"],
                metadata=base_metadata,
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"playwright_profile_render_failed: {type(error).__name__}: {short_error(error)}"],
                metadata=base_metadata,
            )

        if platform_share:
            platform_result = platform_share_result(input, page, classification.kind, classification.platform, base_metadata)
            if platform_result is not None:
                return platform_result

        markdown = html_to_markdown(page.html)
        if not markdown.strip():
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["playwright_profile_empty_markdown"],
                metadata={
                    **base_metadata,
                    "final_url": page.final_url,
                    "status_code": page.status_code,
                    "html_length": len(page.html),
                },
            )

        artifacts: list[dict[str, Any]] = []
        if job_dir is not None:
            if page.html_path is None:
                artifact = save_text_artifact(job_dir, "intermediate/playwright_profile/rendered.html", page.html)
                artifact["type"] = "rendered_html"
                artifacts.append(artifact)
            else:
                artifacts.append({"type": "rendered_html", "path": relative_artifact_path(page.html_path, job_dir)})

            if page.screenshot_path is not None:
                artifacts.append(
                    {"type": "screenshot", "path": relative_artifact_path(page.screenshot_path, job_dir)}
                )

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=page.title,
            artifacts=artifacts,
            metadata={
                **base_metadata,
                "rendered": True,
                "final_url": page.final_url,
                "status_code": page.status_code,
                "html_length": len(page.html),
            },
            warnings=page.warnings,
        )


def resolve_profile_dir(value: Any) -> Path:
    raw = str(value or DEFAULT_PROFILE_DIR).strip() or DEFAULT_PROFILE_DIR
    return Path(raw).expanduser()


def render_with_playwright_profile(
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
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    html_path = job_dir / "intermediate" / "playwright_profile" / "rendered.html" if job_dir else None
    screenshot_path = (
        job_dir / "intermediate" / "playwright_profile" / "screenshot.png" if job_dir and screenshot else None
    )
    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(user_data_dir=str(profile_dir), headless=headless)
        try:
            page = context.new_page()
            response = page.goto(url, wait_until=wait_until, timeout=timeout_seconds * 1000)
            if post_load_wait_ms > 0:
                page.wait_for_timeout(post_load_wait_ms)
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
            context.close()

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


def platform_share_result(
    input: WorkerInput,
    page: RenderedPage,
    kind: UrlKind,
    platform: str | None,
    base_metadata: dict[str, Any],
) -> WorkerResult | None:
    if platform is None:
        return None

    title, messages = find_conversation_messages(page.html, platform=platform)
    title = title or page.title or f"{platform.title()} Shared Conversation"
    page_state = classify_platform_page_state(page.html, status_code=page.status_code) if not messages else PlatformPageState("ok")
    quality_flags: list[str] = []
    if not messages:
        quality_flags.append("missing_messages")
    if page_state.warning:
        quality_flags.append(page_state.warning)

    extraction = build_platform_extraction(
        platform=platform,
        url_kind=kind.value,
        source_url=str(input.source_url),
        final_url=page.final_url,
        title=title,
        content={
            "type": "conversation",
            "text_char_count": sum(len(message["content"]) for message in messages),
            "message_count": len(messages),
            "messages": messages,
            "share_scope": "unknown",
        },
        method="playwright_profile_dom",
        auth_mode="local_profile" if messages else page_state.auth_mode,
        page_state=page_state.state,
        worker=PlaywrightProfileWorker.name,
        quality_flags=quality_flags,
    )
    artifacts: list[dict[str, Any]] = []
    job_dir = get_job_dir(input)
    if job_dir is not None:
        if page.html_path is None:
            artifact = save_text_artifact(job_dir, "intermediate/playwright_profile/rendered.html", page.html)
            artifact["type"] = "rendered_html"
            artifacts.append(artifact)
        else:
            artifacts.append({"type": "rendered_html", "path": relative_artifact_path(page.html_path, job_dir)})

        if page.screenshot_path is not None:
            artifacts.append({"type": "screenshot", "path": relative_artifact_path(page.screenshot_path, job_dir)})
    artifacts.extend(write_platform_artifacts(input, platform=platform, extraction=extraction))

    metadata = {
        **base_metadata,
        **metadata_from_extraction(extraction, status_code=page.status_code, html_length=len(page.html)),
    }
    markdown = render_platform_markdown(extraction)
    if not messages:
        error_code = page_state.error_code or "PLATFORM_NO_MESSAGES"
        warning = page_state.warning or "platform_missing_messages"
        return WorkerResult(
            status="failed",
            worker_name=PlaywrightProfileWorker.name,
            worker_version=PlaywrightProfileWorker.version,
            markdown=markdown,
            title=title,
            artifacts=artifacts,
            metadata={**metadata, "error_code": error_code},
            warnings=[warning],
            errors=[f"playwright_profile_{platform}_share_{page_state.state}"],
        )

    return WorkerResult(
        status="success",
        worker_name=PlaywrightProfileWorker.name,
        worker_version=PlaywrightProfileWorker.version,
        markdown=markdown,
        title=title,
        artifacts=artifacts,
        metadata=metadata,
    )
