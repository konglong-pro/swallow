from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from swallow.core.models import WorkerInput
from swallow.workers.playwright_worker import RenderedPage, auto_scroll, click_expand_controls, render_with_playwright
from swallow.workers.web_common import get_job_dir, save_text_artifact

PLATFORM_EXTRACTION_SCHEMA_VERSION = "platform_extraction.v1"


@dataclass(frozen=True)
class PlatformPage:
    html: str
    title: str | None
    final_url: str
    status_code: int | None = None


@dataclass(frozen=True)
class PlatformPageState:
    state: str
    error_code: str | None = None
    warning: str | None = None
    auth_mode: str = "none"

    @property
    def failed(self) -> bool:
        return self.error_code is not None


def render_platform_page(
    url: str,
    *,
    headless: bool = True,
    timeout_seconds: int = 120,
) -> PlatformPage:
    rendered = render_with_playwright(
        url,
        job_dir=None,
        headless=headless,
        screenshot=False,
        timeout_seconds=timeout_seconds,
    )
    return page_from_rendered(rendered, fallback_url=url)


def render_platform_page_dom_ready(
    url: str,
    *,
    headless: bool = True,
    timeout_seconds: int = 120,
    post_load_wait_ms: int = 5_000,
) -> PlatformPage:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            if post_load_wait_ms > 0:
                page.wait_for_timeout(post_load_wait_ms)
            auto_scroll(page, max_scrolls=4)
            click_expand_controls(page)
            html_text = page.content()
            return PlatformPage(
                html=html_text,
                title=page.title(),
                final_url=page.url or url,
                status_code=response.status if response is not None else None,
            )
        finally:
            browser.close()


def page_from_rendered(rendered: RenderedPage, *, fallback_url: str) -> PlatformPage:
    return PlatformPage(
        html=rendered.html,
        title=rendered.title,
        final_url=rendered.final_url or fallback_url,
        status_code=rendered.status_code,
    )


def write_platform_artifacts(
    input: WorkerInput,
    *,
    platform: str,
    extraction: dict[str, Any],
    rendered_html: str | None = None,
) -> list[dict[str, Any]]:
    job_dir = get_job_dir(input)
    if job_dir is None:
        return []

    artifacts: list[dict[str, Any]] = []
    extraction_artifact = save_text_artifact(
        job_dir,
        f"intermediate/{platform}/platform_extraction.json",
        json.dumps(extraction, ensure_ascii=False, indent=2) + "\n",
    )
    extraction_artifact["type"] = "platform_extraction"
    artifacts.append(extraction_artifact)

    if rendered_html is not None:
        html_artifact = save_text_artifact(job_dir, f"intermediate/{platform}/rendered.html", rendered_html)
        html_artifact["type"] = "rendered_html"
        artifacts.append(html_artifact)

    return artifacts


def build_platform_extraction(
    *,
    platform: str,
    url_kind: str,
    source_url: str,
    final_url: str | None = None,
    canonical_url: str | None = None,
    title: str | None = None,
    authors: list[str] | None = None,
    published_at: str | None = None,
    language: str | None = None,
    content: dict[str, Any] | None = None,
    assets: list[dict[str, Any]] | None = None,
    method: str = "html_parser",
    auth_mode: str = "none",
    page_state: str = "ok",
    worker: str | None = None,
    quality_flags: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extraction = {
        "schema_version": PLATFORM_EXTRACTION_SCHEMA_VERSION,
        "platform": platform,
        "url_kind": url_kind,
        "source_url": source_url,
        "final_url": final_url or source_url,
        "canonical_url": canonical_url or final_url or source_url,
        "title": title,
        "authors": authors or [],
        "published_at": published_at,
        "language": language,
        "content": content or {"type": "article", "text_char_count": 0},
        "assets": assets or [],
        "extraction": {
            "worker": worker,
            "method": method,
            "auth_mode": auth_mode,
            "page_state": page_state,
            "quality_flags": quality_flags or [],
        },
    }
    if extra:
        extraction.update(extra)
    return extraction


def metadata_from_extraction(extraction: dict[str, Any], *, status_code: int | None = None, html_length: int | None = None) -> dict[str, Any]:
    content = extraction.get("content") if isinstance(extraction.get("content"), dict) else {}
    extraction_meta = extraction.get("extraction") if isinstance(extraction.get("extraction"), dict) else {}
    metadata = {
        "platform": extraction.get("platform"),
        "url_kind": extraction.get("url_kind"),
        "source_url": extraction.get("source_url"),
        "final_url": extraction.get("final_url"),
        "canonical_url": extraction.get("canonical_url"),
        "title": extraction.get("title"),
        "authors": extraction.get("authors") or [],
        "content_type": content.get("type"),
        "text_char_count": content.get("text_char_count"),
        "message_count": content.get("message_count"),
        "subtitle_segment_count": content.get("subtitle_segment_count"),
        "asr_segment_count": content.get("asr_segment_count"),
        "asset_count": len(extraction.get("assets") or []),
        "platform_extraction_schema": extraction.get("schema_version"),
        "auth_mode": extraction_meta.get("auth_mode"),
        "page_state": extraction_meta.get("page_state"),
        "quality_flags": extraction_meta.get("quality_flags", []),
    }
    if status_code is not None:
        metadata["status_code"] = status_code
    if html_length is not None:
        metadata["html_length"] = html_length
    return metadata


def render_platform_markdown(extraction: dict[str, Any]) -> str:
    content = extraction.get("content") if isinstance(extraction.get("content"), dict) else {}
    content_type = content.get("type")
    if content_type == "conversation":
        return render_conversation_markdown(extraction)
    if content_type == "transcript":
        return render_transcript_markdown(extraction)
    return render_article_markdown(extraction)


def render_article_markdown(extraction: dict[str, Any]) -> str:
    title = clean_text(extraction.get("title")) or "Article"
    content = extraction.get("content") if isinstance(extraction.get("content"), dict) else {}
    body = clean_markdown(str(content.get("markdown") or content.get("text") or ""))
    chunks = [f"# {title}", metadata_comment(extraction)]
    authors = extraction.get("authors") or []
    if authors:
        chunks.append("Author: " + ", ".join(str(author) for author in authors))
    if extraction.get("published_at"):
        chunks.append(f"Published: {extraction['published_at']}")
    if body:
        chunks.append(body)
    return join_chunks(chunks)


def render_conversation_markdown(extraction: dict[str, Any]) -> str:
    title = clean_text(extraction.get("title")) or "Shared Conversation"
    content = extraction.get("content") if isinstance(extraction.get("content"), dict) else {}
    messages = content.get("messages") if isinstance(content.get("messages"), list) else []
    chunks = [f"# {title}", metadata_comment(extraction)]
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        role = clean_text(message.get("role")) or "unknown"
        text = clean_markdown(str(message.get("content") or ""))
        if text:
            chunks.append(f"## {index}. {role}")
            chunks.append(text)
    return join_chunks(chunks)


def render_transcript_markdown(extraction: dict[str, Any]) -> str:
    title = clean_text(extraction.get("title")) or "Transcript"
    content = extraction.get("content") if isinstance(extraction.get("content"), dict) else {}
    segments = content.get("segments") if isinstance(content.get("segments"), list) else []
    chunks = [f"# {title}", metadata_comment(extraction), "## Transcript"]
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = format_seconds(float_or_zero(segment.get("start")))
        text = clean_text(segment.get("text"))
        if text:
            chunks.append(f"[{start}] {text}")
    return join_chunks(chunks)


def metadata_comment(extraction: dict[str, Any]) -> str:
    lines = [
        f"platform: {extraction.get('platform')}",
        f"url_kind: {extraction.get('url_kind')}",
        f"source_url: {extraction.get('source_url')}",
    ]
    final_url = extraction.get("final_url")
    if final_url and final_url != extraction.get("source_url"):
        lines.append(f"final_url: {final_url}")
    return "<!-- platform_extraction\n" + "\n".join(lines) + "\n-->"


def classify_platform_page_state(html_text: str, *, text: str | None = None, status_code: int | None = None) -> PlatformPageState:
    visible_text = clean_text(text) or markdown_to_text(html_to_markdown(body_html(html_text)))
    lowered = (visible_text or "").lower()

    if status_code in {429, 503} or contains_any(lowered, RATE_LIMIT_MARKERS):
        return PlatformPageState(
            state="rate_limited",
            error_code="PLATFORM_RATE_LIMITED",
            warning="platform_rate_limited",
        )
    if status_code == 403 or contains_any(lowered, REGION_BLOCK_MARKERS):
        return PlatformPageState(
            state="region_blocked",
            error_code="PLATFORM_REGION_BLOCKED",
            warning="platform_region_blocked",
        )
    if contains_any(lowered, AUTH_REQUIRED_MARKERS):
        return PlatformPageState(
            state="auth_required",
            error_code="PLATFORM_AUTH_REQUIRED",
            warning="auth_wall_detected",
            auth_mode="required",
        )
    if contains_any(lowered, BROWSER_REQUIRED_MARKERS):
        return PlatformPageState(
            state="browser_required",
            error_code="PLATFORM_BROWSER_RENDER_REQUIRED",
            warning="needs_browser_render",
        )
    if contains_any(lowered, DELETED_OR_UNAVAILABLE_MARKERS):
        return PlatformPageState(
            state="deleted_or_unavailable",
            error_code="PLATFORM_CONTENT_UNAVAILABLE",
            warning="platform_content_unavailable",
        )
    if len(visible_text or "") < 20:
        return PlatformPageState(
            state="empty_shell",
            error_code="PLATFORM_EMPTY_SHELL",
            warning="platform_empty_shell",
        )
    return PlatformPageState(state="ok")


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


AUTH_REQUIRED_MARKERS = (
    "log in to continue",
    "login to continue",
    "sign in to continue",
    "sign in to view",
    "please log in",
    "please sign in",
    "continue with google",
    "continue with apple",
    "登录以继续",
    "请登录",
    "扫码登录",
    "登录后",
    "注册/登录",
)
BROWSER_REQUIRED_MARKERS = (
    "javascript is not available",
    "enable javascript",
    "please enable javascript",
)
DELETED_OR_UNAVAILABLE_MARKERS = (
    "conversation not found",
    "conversation unavailable",
    "this chat doesn't exist",
    "unable to load conversation",
    "this content is no longer available",
    "post unavailable",
    "content unavailable",
    "video unavailable",
    "please open in wechat",
    "该内容已被发布者删除",
    "请在微信客户端打开",
    "内容不存在",
    "该内容已删除",
    "回答不存在",
    "当前内容无法展示",
)
RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "try again later",
    "temporarily unavailable",
    "访问过于频繁",
    "操作频繁",
    "稍后再试",
)
REGION_BLOCK_MARKERS = (
    "not available in your region",
    "unavailable in your region",
    "not available in your country",
    "blocked in your country",
    "地区不可用",
    "该地区",
)


def extract_json_payloads(html_text: str) -> list[Any]:
    payloads: list[Any] = []
    for match in re.finditer(
        r"<script[^>]*type=[\"'](?:application/json|application/ld\+json)[\"'][^>]*>(?P<body>.*?)</script>",
        html_text,
        re.I | re.S,
    ):
        body = html.unescape(match.group("body")).strip()
        if not body:
            continue
        try:
            payloads.append(json.loads(body))
        except json.JSONDecodeError:
            continue
    return payloads


def find_conversation_messages(html_text: str, *, platform: str | None = None) -> tuple[str | None, list[dict[str, str]]]:
    for payload in extract_json_payloads(html_text):
        found = conversation_from_json(payload)
        if found[1]:
            return found

    dom_messages = conversation_from_dom(html_text)
    if dom_messages:
        return extract_title(html_text), dom_messages

    messages: list[dict[str, str]] = []
    for match in re.finditer(
        r"<(?P<tag>[a-z0-9]+)[^>]*(?:data-message-author-role|data-role)=[\"'](?P<role>[^\"']+)[\"'][^>]*>(?P<body>.*?)</(?P=tag)>",
        html_text,
        re.I | re.S,
    ):
        text = html_to_markdown(match.group("body")).strip()
        if text:
            messages.append({"role": clean_text(match.group("role")) or "unknown", "content": text})
    if messages:
        return (extract_title(html_text), messages)

    platform_title, platform_messages = conversation_from_platform_visible_text(html_text, platform=platform)
    return (platform_title or extract_title(html_text), platform_messages)


def conversation_from_json(payload: Any) -> tuple[str | None, list[dict[str, str]]]:
    if not isinstance(payload, dict):
        return None, []
    candidate = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else payload
    title = clean_text(candidate.get("title")) if isinstance(candidate, dict) else None
    messages = candidate.get("messages") if isinstance(candidate, dict) else None
    if not isinstance(messages, list):
        mapping = candidate.get("mapping") if isinstance(candidate, dict) else None
        mapping_messages = messages_from_mapping(mapping)
        if mapping_messages:
            return title, mapping_messages

        discovered: list[list[dict[str, str]]] = []
        collect_conversation_message_lists(payload, discovered)
        if discovered:
            return title, max(discovered, key=len)
        return title, []
    normalized = normalize_message_list(messages)
    return title, normalized


def conversation_from_dom(html_text: str) -> list[dict[str, str]]:
    soup = soup_from_html(html_text)
    if soup is None:
        return []

    messages: list[dict[str, str]] = []
    for element in soup.select("[data-message-author-role], [data-role], [data-author]"):
        if has_role_ancestor(element):
            continue
        role = element.get("data-message-author-role") or element.get("data-role") or element.get("data-author")
        text = html_to_markdown(inner_html(element)).strip()
        if text:
            messages.append({"role": clean_text(role) or "unknown", "content": text})
    return messages


def conversation_from_platform_visible_text(html_text: str, *, platform: str | None) -> tuple[str | None, list[dict[str, str]]]:
    if platform == "claude":
        return conversation_from_claude_visible_text(html_text)
    if platform == "deepseek":
        return conversation_from_deepseek_visible_text(html_text)
    if platform == "gemini":
        return conversation_from_gemini_visible_text(html_text)
    return None, []


def conversation_from_claude_visible_text(html_text: str) -> tuple[str | None, list[dict[str, str]]]:
    lines = visible_text_lines(html_text)
    messages: list[dict[str, str]] = []
    role: str | None = None
    parts: list[str] = []

    def flush() -> None:
        nonlocal parts, role
        if role is None:
            return
        content = visible_message_content(parts, platform="claude")
        if content:
            messages.append({"role": role, "content": content})
        parts = []

    for line in lines:
        if line.startswith("You said:"):
            flush()
            role = "user"
            remainder = clean_text(line.removeprefix("You said:"))
            parts = [remainder] if remainder else []
            continue
        if line.startswith("Claude responded:"):
            flush()
            role = "assistant"
            remainder = clean_text(line.removeprefix("Claude responded:"))
            parts = [remainder] if remainder else []
            continue
        if role is not None:
            parts.append(line)
    flush()
    return visible_title(lines, default=extract_title(html_text), platform="claude"), messages


def conversation_from_deepseek_visible_text(html_text: str) -> tuple[str | None, list[dict[str, str]]]:
    lines = visible_text_lines(html_text)
    if not any(line == "来自分享的对话" or "由 AI 生成" in line or "由AI生成" in line for line in lines):
        return None, []

    start_index = 0
    for index, line in enumerate(lines):
        if "由 AI 生成" in line or "由AI生成" in line:
            start_index = index + 1
            break

    stop_index = len(lines)
    for index in range(start_index, len(lines)):
        if lines[index] == "和 DeepSeek 继续聊":
            stop_index = index
            break

    search_marker = "专家模式暂不支持搜索，请使用快速模式"
    marker_index = next((index for index in range(start_index, stop_index) if lines[index] == search_marker), None)
    if marker_index is not None:
        user_lines = lines[start_index:marker_index]
        assistant_lines = lines[marker_index + 1 : stop_index]
    else:
        content_lines = [line for line in lines[start_index:stop_index] if not skip_visible_line(line, platform="deepseek")]
        user_lines = content_lines[:1]
        assistant_lines = content_lines[1:]

    messages: list[dict[str, str]] = []
    user_content = visible_message_content(user_lines, platform="deepseek")
    assistant_content = visible_message_content(assistant_lines, platform="deepseek")
    if user_content:
        messages.append({"role": "user", "content": user_content})
    if assistant_content:
        messages.append({"role": "assistant", "content": assistant_content})
    return visible_title(lines, default=extract_title(html_text), platform="deepseek"), messages


def conversation_from_gemini_visible_text(html_text: str) -> tuple[str | None, list[dict[str, str]]]:
    lines = visible_text_lines(html_text)
    user_markers = [index for index, line in enumerate(lines) if line == "You said"]
    messages: list[dict[str, str]] = []

    for marker_position, marker_index in enumerate(user_markers):
        next_marker = user_markers[marker_position + 1] if marker_position + 1 < len(user_markers) else len(lines)
        segment = trim_gemini_segment(lines[marker_index + 1 : next_marker])
        content_lines = [line for line in segment if not skip_visible_line(line, platform="gemini")]
        if not content_lines:
            continue
        user_content = visible_message_content(content_lines[:1], platform="gemini")
        assistant_content = visible_message_content(content_lines[1:], platform="gemini")
        if user_content:
            messages.append({"role": "user", "content": user_content})
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

    return visible_title(lines, default=extract_title(html_text), platform="gemini"), messages


def visible_text_lines(html_text: str) -> list[str]:
    soup = soup_from_html(html_text)
    if soup is not None:
        for element in soup(["script", "style", "svg", "noscript", "template"]):
            element.decompose()
        raw_lines = soup.get_text("\n").splitlines()
    else:
        raw_lines = strip_tags(body_html(html_text)).splitlines()

    lines: list[str] = []
    for line in raw_lines:
        text = clean_text(line)
        text = strip_invisible_format_chars(text) if text else None
        if text:
            lines.append(text)
    return dedupe_adjacent(lines)


def visible_message_content(lines: list[str], *, platform: str) -> str:
    filtered = [line for line in dedupe_adjacent(lines) if not skip_visible_line(line, platform=platform)]
    return clean_markdown("\n".join(filtered))


def skip_visible_line(line: str, *, platform: str) -> bool:
    if line in COMMON_VISIBLE_SKIP_LINES:
        return True
    if is_icon_only_line(line):
        return True
    if platform == "claude":
        return line in CLAUDE_VISIBLE_SKIP_LINES or is_short_date_line(line)
    if platform == "deepseek":
        return line in DEEPSEEK_VISIBLE_SKIP_LINES or line.startswith("DeepSeek -")
    if platform == "gemini":
        return line in GEMINI_VISIBLE_SKIP_LINES or line.startswith("Published ") or line.startswith("Created with")
    return False


def trim_gemini_segment(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        if line in GEMINI_VISIBLE_STOP_LINES:
            return lines[:index]
    return lines


def visible_title(lines: list[str], *, default: str | None, platform: str) -> str | None:
    for line in lines[:20]:
        if skip_visible_line(line, platform=platform):
            continue
        if platform == "claude" and line == "Claude":
            continue
        if platform == "deepseek" and line in {"来自分享的对话", "专家模式"}:
            continue
        if platform == "gemini" and line in {"Gemini", "About Gemini"}:
            continue
        if platform == "gemini" and "direct access to Google AI" in line:
            continue
        if line.startswith("http://") or line.startswith("https://"):
            continue
        return line
    return default


def dedupe_adjacent(lines: list[str]) -> list[str]:
    deduped: list[str] = []
    previous: str | None = None
    for line in lines:
        if line == previous:
            continue
        deduped.append(line)
        previous = line
    return deduped


def is_short_date_line(line: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|[A-Z][a-z]+ \d{1,2}, \d{4}", line))


def is_icon_only_line(line: str) -> bool:
    return all(char.isspace() or 0xE000 <= ord(char) <= 0xF8FF for char in line)


def strip_invisible_format_chars(value: str) -> str:
    return re.sub(r"[\u200e\u200f\u202a-\u202e]", "", value).strip()


COMMON_VISIBLE_SKIP_LINES = {
    "Report",
    "Sign in",
    "Copy public link",
    "Opens in a new window",
}
CLAUDE_VISIBLE_SKIP_LINES = {
    "Ask Claude your own question",
}
DEEPSEEK_VISIBLE_SKIP_LINES = {
    "来自分享的对话",
    "专家模式",
    "该对话来自分享，由 AI 生成，请仔细甄别。",
    "专家模式暂不支持搜索，请使用快速模式",
    "和 DeepSeek 继续聊",
}
GEMINI_VISIBLE_SKIP_LINES = {
    "Gemini",
    "About Gemini",
    "Gemini App",
    "Get Gemini App",
    "Subscriptions",
    "For Business",
    "Export to Sheets",
}
GEMINI_VISIBLE_STOP_LINES = {
    "Google Privacy Policy",
    "Google Terms of Service",
    "Your privacy & Gemini Apps",
    "Gemini may display inaccurate info, including about people, so double-check its responses.",
    "Copy public link",
    "Report",
}


def has_role_ancestor(element: Any) -> bool:
    parent = getattr(element, "parent", None)
    while parent is not None:
        attrs = getattr(parent, "attrs", {})
        if any(key in attrs for key in ("data-message-author-role", "data-role", "data-author")):
            return True
        parent = getattr(parent, "parent", None)
    return False


def collect_conversation_message_lists(value: Any, discovered: list[list[dict[str, str]]]) -> None:
    if isinstance(value, list):
        normalized = normalize_message_list(value)
        if normalized:
            discovered.append(normalized)
        for item in value:
            collect_conversation_message_lists(item, discovered)
        return
    if not isinstance(value, dict):
        return
    messages = value.get("messages")
    if isinstance(messages, list):
        normalized = normalize_message_list(messages)
        if normalized:
            discovered.append(normalized)
    mapping_messages = messages_from_mapping(value.get("mapping"))
    if mapping_messages:
        discovered.append(mapping_messages)
    for item in value.values():
        collect_conversation_message_lists(item, discovered)


def normalize_message_list(messages: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        parsed = message_from_json(message)
        if parsed is not None:
            normalized.append(parsed)
    return normalized


def messages_from_mapping(mapping: Any) -> list[dict[str, str]]:
    if not isinstance(mapping, dict):
        return []
    ordered: list[tuple[float, int, dict[str, str]]] = []
    for index, node in enumerate(mapping.values()):
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        parsed = message_from_json(message)
        if parsed is None:
            continue
        order_value = message.get("create_time") if isinstance(message, dict) else None
        ordered.append((float_or_zero(order_value), index, parsed))
    if any(item[0] for item in ordered):
        ordered.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ordered]


def message_from_json(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    role = role_from_json(value.get("role") or value.get("author"))
    content = value.get("content") or value.get("text")
    text = clean_markdown(stringify_content(content))
    if not text:
        return None
    return {"role": role or "unknown", "content": text}


def role_from_json(value: Any) -> str | None:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get("role") or value.get("name"))
    return None


def extract_wechat_article(html_text: str) -> dict[str, Any]:
    title = element_text_by_id(html_text, "activity-name") or meta_content(html_text, "og:title") or extract_title(html_text)
    account = element_text_by_id(html_text, "js_name")
    content_html = element_html_by_id(html_text, "js_content")
    content_html = promote_lazy_image_sources(content_html)
    markdown = html_to_markdown(content_html)
    text = markdown_to_text(markdown)
    assets = image_assets(content_html)
    published_at = element_text_by_id(html_text, "publish_time") or script_var(html_text, "ct")
    return {
        "title": clean_text(title),
        "authors": [account] if account else [],
        "published_at": clean_text(published_at),
        "markdown": clean_markdown(markdown),
        "text": text,
        "assets": assets,
    }


def extract_article_by_selectors(
    html_text: str,
    *,
    title_ids: tuple[str, ...] = (),
    content_ids: tuple[str, ...] = (),
    title_selectors: tuple[str, ...] = (),
    content_selectors: tuple[str, ...] = (),
    author_selectors: tuple[str, ...] = (),
    published_selectors: tuple[str, ...] = (),
) -> dict[str, Any]:
    json_candidate = article_from_json_payloads(html_text)
    title = first_text_by_id(html_text, title_ids)
    title = title or first_text_by_selector(html_text, title_selectors)
    title = title or meta_content(html_text, "og:title") or json_candidate.get("title") or extract_title(html_text)
    content_html = first_html_by_id(html_text, content_ids)
    content_html = content_html or first_html_by_selector(html_text, content_selectors)
    if not content_html:
        json_text = str(json_candidate.get("text") or "")
        content_html = html.escape(json_text) if json_text else body_html(html_text)
    markdown = html_to_markdown(content_html)
    author = first_text_by_selector(html_text, author_selectors) or meta_content(html_text, "article:author") or json_candidate.get("author")
    published_at = first_text_by_selector(html_text, published_selectors) or meta_content(html_text, "article:published_time") or json_candidate.get("published_at")
    return {
        "title": clean_text(title),
        "authors": [author] if isinstance(author, str) and author else [],
        "published_at": clean_text(published_at),
        "markdown": clean_markdown(markdown),
        "text": markdown_to_text(markdown),
        "assets": image_assets(content_html),
    }


def extract_title(html_text: str) -> str | None:
    return element_text(html_text, "title") or meta_content(html_text, "og:title")


def first_text_by_id(html_text: str, ids: tuple[str, ...]) -> str | None:
    for item in ids:
        text = element_text_by_id(html_text, item)
        if text:
            return text
    return None


def first_html_by_id(html_text: str, ids: tuple[str, ...]) -> str:
    for item in ids:
        fragment = element_html_by_id(html_text, item)
        if fragment:
            return fragment
    return ""


def first_text_by_selector(html_text: str, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        text = element_text_by_selector(html_text, selector)
        if text:
            return text
    return None


def first_html_by_selector(html_text: str, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        fragment = element_html_by_selector(html_text, selector)
        if fragment:
            return fragment
    return ""


def element_text(html_text: str, tag: str) -> str | None:
    soup = soup_from_html(html_text)
    if soup is not None:
        element = soup.find(tag)
        return clean_text(element.get_text(" ", strip=True)) if element else None

    match = re.search(rf"<{tag}[^>]*>(?P<body>.*?)</{tag}>", html_text, re.I | re.S)
    return clean_text(strip_tags(match.group("body"))) if match else None


def element_text_by_id(html_text: str, element_id: str) -> str | None:
    soup = soup_from_html(html_text)
    if soup is not None:
        element = soup.find(id=element_id)
        return clean_text(element.get_text(" ", strip=True)) if element else None

    fragment = element_html_by_id(html_text, element_id)
    return clean_text(strip_tags(fragment)) if fragment else None


def element_html_by_id(html_text: str, element_id: str) -> str:
    soup = soup_from_html(html_text)
    if soup is not None:
        element = soup.find(id=element_id)
        return inner_html(element) if element else ""

    pattern = (
        r"<(?P<tag>[a-z0-9]+)[^>]*\bid=[\"']"
        + re.escape(element_id)
        + r"[\"'][^>]*>(?P<body>.*?)</(?P=tag)>"
    )
    match = re.search(pattern, html_text, re.I | re.S)
    return match.group("body") if match else ""


def element_text_by_selector(html_text: str, selector: str) -> str | None:
    soup = soup_from_html(html_text)
    if soup is None:
        return None
    try:
        element = soup.select_one(selector)
    except Exception:
        return None
    return clean_text(element.get_text(" ", strip=True)) if element else None


def element_html_by_selector(html_text: str, selector: str) -> str:
    soup = soup_from_html(html_text)
    if soup is None:
        return ""
    try:
        element = soup.select_one(selector)
    except Exception:
        return ""
    return inner_html(element) if element else ""


def body_html(html_text: str) -> str:
    soup = soup_from_html(html_text)
    if soup is not None and soup.body is not None:
        return inner_html(soup.body)

    match = re.search(r"<body[^>]*>(?P<body>.*?)</body>", html_text, re.I | re.S)
    return match.group("body") if match else html_text


def meta_content(html_text: str, property_name: str) -> str | None:
    soup = soup_from_html(html_text)
    if soup is not None:
        tag = soup.find("meta", attrs={"property": property_name}) or soup.find("meta", attrs={"name": property_name})
        value = tag.get("content") if tag is not None else None
        return clean_text(value)

    pattern = (
        r"<meta[^>]*(?:property|name)=[\"']"
        + re.escape(property_name)
        + r"[\"'][^>]*content=[\"'](?P<content>[^\"']+)[\"'][^>]*>"
    )
    match = re.search(pattern, html_text, re.I | re.S)
    if not match:
        pattern = (
            r"<meta[^>]*content=[\"'](?P<content>[^\"']+)[\"'][^>]*(?:property|name)=[\"']"
            + re.escape(property_name)
            + r"[\"'][^>]*>"
        )
        match = re.search(pattern, html_text, re.I | re.S)
    return clean_text(match.group("content")) if match else None


def script_var(html_text: str, name: str) -> str | None:
    match = re.search(rf"\b{name}\s*=\s*['\"](?P<value>[^'\"]+)['\"]", html_text)
    return clean_text(match.group("value")) if match else None


def promote_lazy_image_sources(fragment: str) -> str:
    soup = soup_from_html(fragment)
    if soup is not None:
        for image in soup.find_all("img"):
            if image.get("src"):
                continue
            lazy = image.get("data-src") or image.get("data-original")
            if lazy:
                image["src"] = lazy
        return str(soup)

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if re.search(r"\ssrc\s*=", tag, re.I):
            return tag
        lazy = re.search(r"\s(?:data-src|data-original)=[\"'](?P<src>[^\"']+)[\"']", tag, re.I)
        if not lazy:
            return tag
        return tag[:-1] + f' src="{html.escape(lazy.group("src"))}">'

    return re.sub(r"<img\b[^>]*>", replace, fragment, flags=re.I)


def image_assets(fragment: str) -> list[dict[str, str]]:
    soup = soup_from_html(fragment)
    if soup is not None:
        assets: list[dict[str, str]] = []
        for image in soup.find_all("img"):
            src = image.get("src") or image.get("data-src") or image.get("data-original")
            if isinstance(src, str) and src.strip():
                assets.append({"type": "image", "url": html.unescape(src.strip()), "role": "inline"})
        return assets

    assets: list[dict[str, str]] = []
    for match in re.finditer(r"<img\b[^>]*(?:src|data-src|data-original)=[\"'](?P<src>[^\"']+)[\"'][^>]*>", fragment, re.I):
        src = html.unescape(match.group("src")).strip()
        if src:
            assets.append({"type": "image", "url": src, "role": "inline"})
    return assets


def article_from_json_payloads(html_text: str) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    for payload in extract_json_payloads(html_text):
        collect_article_candidates(payload, candidates)
    if not candidates:
        return {}
    return max(candidates, key=lambda item: len(item.get("text") or ""))


def collect_article_candidates(value: Any, candidates: list[dict[str, str]]) -> None:
    if isinstance(value, list):
        for item in value:
            collect_article_candidates(item, candidates)
        return
    if not isinstance(value, dict):
        return

    text = first_string_value(value, ("articleBody", "body", "content", "description", "text"))
    if text and len(markdown_to_text(text)) >= 20:
        author = author_from_json(value.get("author"))
        candidates.append(
            {
                "title": first_string_value(value, ("headline", "name", "title")) or "",
                "text": text,
                "author": author,
                "published_at": first_string_value(value, ("datePublished", "dateCreated", "createdAt", "updatedAt")) or "",
            }
        )

    for item in value.values():
        collect_article_candidates(item, candidates)


def first_string_value(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return clean_markdown(item)
    return None


def author_from_json(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value) or ""
    if isinstance(value, dict):
        return clean_text(value.get("name")) or ""
    if isinstance(value, list):
        names = [author_from_json(item) for item in value]
        return ", ".join(name for name in names if name)
    return ""


def soup_from_html(html_text: str) -> Any | None:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    return BeautifulSoup(html_text, "html.parser")


def inner_html(element: Any) -> str:
    return "".join(str(child) for child in element.contents)


def html_to_markdown(fragment: str) -> str:
    try:
        from markdownify import markdownify
    except ImportError:
        return clean_text(strip_tags(fragment)) or ""
    return clean_markdown(markdownify(fragment, heading_style="ATX"))


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def markdown_to_text(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"[#*_`>|]", " ", text)
    return clean_text(text) or ""


def clean_markdown(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def stringify_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(part for part in (stringify_content(item).strip() for item in value) if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("parts"), list):
            return stringify_content(value["parts"])
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def join_chunks(chunks: list[str | None]) -> str:
    return "\n\n".join(chunk.strip() for chunk in chunks if isinstance(chunk, str) and chunk.strip()).strip() + "\n"


def float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_seconds(value: float) -> str:
    total = int(value)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def fetch_text(url: str, *, timeout_seconds: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "swallow/0.1 URL ingest"})
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/") or None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            values = parse_qs(parsed.query).get("v")
            return values[0] if values else None
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/", 2)[2] or None
    return None


def find_youtube_caption_tracks(html_text: str) -> list[dict[str, Any]]:
    marker = '"captionTracks"'
    marker_index = html_text.find(marker)
    if marker_index < 0:
        return []
    array_start = html_text.find("[", marker_index)
    if array_start < 0:
        return []
    array_text = read_json_array(html_text, array_start)
    if not array_text:
        return []
    try:
        tracks = json.loads(array_text)
    except json.JSONDecodeError:
        return []
    return tracks if isinstance(tracks, list) else []


def read_json_array(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_timedtext_xml(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    segments: list[dict[str, Any]] = []
    for element in root.iter():
        if element.tag != "text":
            continue
        text = clean_text("".join(element.itertext()))
        if not text:
            continue
        segments.append(
            {
                "start": float_or_zero(element.attrib.get("start")),
                "duration": float_or_zero(element.attrib.get("dur")),
                "text": text,
            }
        )
    return segments


def parse_timedtext_json3(json_text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return []
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return []

    segments: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        text = clean_text("".join(str(seg.get("utf8") or "") for seg in segs if isinstance(seg, dict)))
        if not text:
            continue
        segments.append(
            {
                "start": float_or_zero(event.get("tStartMs")) / 1000,
                "duration": float_or_zero(event.get("dDurationMs")) / 1000,
                "text": text,
            }
        )
    return segments


def parse_timedtext_payload(text: str) -> list[dict[str, Any]]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        segments = parse_timedtext_json3(text)
        if segments:
            return segments
    return parse_timedtext_xml(text)
