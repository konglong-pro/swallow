from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from swallow.core.models import QualityReport, WorkerInput, WorkerResult
from swallow.detectors.url_classifier import UrlKind, classify_url

QUALITY_CHECKER_NAME = "quality_checker"
QUALITY_CHECKER_VERSION = "0.1.0"


def check_quality(result: WorkerResult, input: WorkerInput) -> QualityReport:
    markdown = result.markdown or ""
    metrics = collect_common_metrics(markdown)
    if input.source_type == "url":
        metrics.update(collect_web_metrics(markdown, result.metadata))
        metrics.update(collect_platform_metrics(markdown, result.metadata, input))
    complete_platform_extraction = has_complete_platform_extraction(metrics)
    score = 1.0
    warnings = list(result.warnings)

    if metrics["text_length"] < 300 and not complete_platform_extraction:
        score -= 0.3
        warnings.append("text_length_below_300")

    if metrics["garbage_ratio"] > 0.1:
        score -= 0.3
        warnings.append("possible_garbage_text")

    if input.mime_type == "application/pdf" and metrics.get("chars_per_page", 999) < 80:
        score -= 0.4
        warnings.append("pdf_chars_per_page_below_80")

    if input.source_type == "url":
        if metrics["nav_noise_ratio"] > 0.35:
            score -= 0.2
            warnings.append("possible_nav_noise")
        if metrics["blocked_detected"]:
            score -= 0.6
            warnings.append("blocked_or_rate_limited")
        if metrics["needs_browser_render"]:
            score -= 0.4
            warnings.append("needs_browser_render")
        if metrics["auth_wall_detected"]:
            score -= 0.6
            warnings.append("auth_wall_detected")
        if metrics.get("platform_required_content_missing"):
            score -= 0.7
            warnings.append(str(metrics["platform_required_content_missing"]))
        if metrics.get("platform_reject_marker_detected"):
            score -= 0.6
            warnings.append("platform_reject_marker_detected")

    score = max(round(score, 2), 0.0)
    return QualityReport(
        score=score,
        warnings=dedupe_preserve_order(warnings),
        metrics=metrics,
        confidence=result.confidence,
    )


def collect_common_metrics(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    text_length = len(markdown.strip())
    empty_lines = sum(1 for line in lines if not line.strip())
    line_count = len(lines)
    return {
        "text_length": text_length,
        "line_count": line_count,
        "table_count": sum(1 for line in lines if line.strip().startswith("|")),
        "image_count": markdown.count("!["),
        "heading_count": sum(1 for line in lines if line.startswith("#")),
        "garbage_ratio": garbage_ratio(markdown),
        "empty_ratio": round(empty_lines / max(line_count, 1), 4),
        "language": None,
    }


def garbage_ratio(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 1.0
    garbage = len(re.findall(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]", stripped))
    return round(garbage / max(len(stripped), 1), 4)


def collect_web_metrics(markdown: str, metadata: dict[str, Any]) -> dict[str, Any]:
    lowered = markdown.lower()
    status_code = metadata.get("status_code")
    auth_score = auth_wall_score(markdown)
    return {
        "status_code": status_code,
        "final_url": metadata.get("final_url") or metadata.get("source_url"),
        "html_length": metadata.get("html_length"),
        "markdown_length": len(markdown),
        "nav_noise_ratio": nav_noise_ratio(markdown),
        "blocked_detected": status_code in {403, 429, 503} or "access denied" in lowered,
        "needs_browser_render": (
            "enable javascript" in lowered
            or "please enable javascript" in lowered
            or "javascript is not available" in lowered
        ),
        "auth_wall_score": auth_score,
        "auth_wall_detected": auth_wall_detected(markdown, metadata, auth_score=auth_score),
    }


def collect_platform_metrics(markdown: str, metadata: dict[str, Any], input: WorkerInput) -> dict[str, Any]:
    source_url = input.source_url or read_url(metadata.get("source_url") or metadata.get("final_url"))
    classification = classify_url(source_url) if source_url else None
    url_kind = read_url_kind(metadata.get("url_kind")) or (classification.kind if classification else UrlKind.GENERIC_WEB)
    platform = metadata.get("platform") or (classification.platform if classification else None)
    return {
        "url_kind": url_kind.value,
        "platform": platform,
        "message_count": read_int(metadata.get("message_count")),
        "subtitle_segment_count": read_int(metadata.get("subtitle_segment_count")),
        "asr_segment_count": read_int(metadata.get("asr_segment_count")),
        "platform_text_char_count": read_int(metadata.get("text_char_count")),
        "platform_extraction_schema": metadata.get("platform_extraction_schema"),
        "platform_required_content_missing": platform_required_content_missing(url_kind, metadata),
        "platform_reject_marker_detected": platform_reject_marker_detected(markdown, url_kind),
    }


def platform_required_content_missing(kind: UrlKind, metadata: dict[str, Any]) -> str | None:
    if kind in CONVERSATION_URL_KINDS and read_int(metadata.get("message_count")) < 1:
        return "platform_missing_messages"
    if kind in ARTICLE_URL_KINDS and read_int(metadata.get("text_char_count")) < 80:
        return "platform_content_too_short"
    if kind == UrlKind.YOUTUBE_VIDEO and max(
        read_int(metadata.get("subtitle_segment_count")),
        read_int(metadata.get("asr_segment_count")),
    ) < 1:
        return "platform_no_transcript"
    return None


def has_complete_platform_extraction(metrics: dict[str, Any]) -> bool:
    if metrics.get("platform_extraction_schema") != "platform_extraction.v1":
        return False
    kind = read_url_kind(metrics.get("url_kind"))
    if kind not in PLATFORM_URL_KINDS_WITH_REQUIRED_CONTENT:
        return False
    return metrics.get("platform_required_content_missing") is None


def platform_reject_marker_detected(markdown: str, kind: UrlKind) -> bool:
    lowered = markdown.lower()
    return any(marker.lower() in lowered for marker in PLATFORM_REJECT_MARKERS.get(kind, ()))


def nav_noise_ratio(markdown: str) -> float:
    lines = [line.strip().lower() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return 0.0
    nav_terms = ("home", "menu", "login", "sign in", "subscribe", "privacy", "terms", "cookie")
    noisy = sum(1 for line in lines if len(line) <= 40 and any(term in line for term in nav_terms))
    return round(noisy / len(lines), 4)


AUTH_WALL_PHRASES = (
    "log in to continue",
    "login to continue",
    "sign in to continue",
    "please log in",
    "please sign in",
    "continue with google",
    "continue with apple",
    "continue with microsoft",
    "continue with email",
    "create an account",
    "forgot password",
    "join x today",
    "登录以继续",
    "请登录",
    "扫码登录",
    "登录后",
    "注册/登录",
)

AUTH_PATH_MARKERS = ("/login", "/signin", "/sign-in", "/auth", "/session", "/account/login")
CONVERSATION_URL_KINDS = {
    UrlKind.CHATGPT_SHARE,
    UrlKind.GEMINI_SHARE,
    UrlKind.CLAUDE_SHARE,
    UrlKind.DEEPSEEK_SHARE,
}
ARTICLE_URL_KINDS = {
    UrlKind.WECHAT_ARTICLE,
}
AUTH_WALL_URL_KINDS = {
    UrlKind.LOGIN_REQUIRED_WEB,
    UrlKind.RESTRICTED_WEB,
    UrlKind.CHATGPT_SHARE,
    UrlKind.GEMINI_SHARE,
    UrlKind.CLAUDE_SHARE,
    UrlKind.DEEPSEEK_SHARE,
}
PLATFORM_URL_KINDS_WITH_REQUIRED_CONTENT = (
    CONVERSATION_URL_KINDS
    | ARTICLE_URL_KINDS
    | {
        UrlKind.YOUTUBE_VIDEO,
    }
)
PLATFORM_REJECT_MARKERS = {
    UrlKind.CHATGPT_SHARE: ("unable to load conversation", "conversation not found"),
    UrlKind.GEMINI_SHARE: ("this chat doesn't exist", "sign in to view"),
    UrlKind.CLAUDE_SHARE: ("sign in to view", "conversation unavailable"),
    UrlKind.DEEPSEEK_SHARE: ("sign in", "conversation unavailable"),
    UrlKind.WECHAT_ARTICLE: ("该内容已被发布者删除", "请在微信客户端打开", "内容不存在"),
    UrlKind.YOUTUBE_VIDEO: ("transcript unavailable", "no transcript"),
}


def auth_wall_score(markdown: str) -> int:
    lowered = re.sub(r"\s+", " ", markdown.lower())
    return sum(1 for phrase in AUTH_WALL_PHRASES if phrase in lowered)


def auth_wall_detected(markdown: str, metadata: dict[str, Any], *, auth_score: int | None = None) -> bool:
    score = auth_wall_score(markdown) if auth_score is None else auth_score
    if score <= 0:
        return False

    final_url = read_url(metadata.get("final_url") or metadata.get("source_url"))
    source_url = read_url(metadata.get("source_url") or metadata.get("final_url"))
    if has_auth_path(final_url):
        return True

    if source_url:
        try:
            source_kind = classify_url(source_url).kind
        except Exception:
            source_kind = UrlKind.GENERIC_WEB
        if source_kind in AUTH_WALL_URL_KINDS:
            return True

    return score >= 3 and len(markdown.strip()) < 2000


def read_url(value: Any) -> str:
    return value if isinstance(value, str) else ""


def has_auth_path(url: str) -> bool:
    if not url:
        return False
    path = urlparse(url).path.lower()
    return any(marker in path for marker in AUTH_PATH_MARKERS)


def read_url_kind(value: Any) -> UrlKind | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return UrlKind(value)
    except ValueError:
        return None


def read_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
