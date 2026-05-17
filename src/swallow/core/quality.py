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
    score = 1.0
    warnings = list(result.warnings)

    if metrics["text_length"] < 300:
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
        "needs_browser_render": "enable javascript" in lowered or "please enable javascript" in lowered,
        "auth_wall_score": auth_score,
        "auth_wall_detected": auth_wall_detected(markdown, metadata, auth_score=auth_score),
    }


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
    "使用 google 继续",
    "使用 apple 继续",
)

AUTH_PATH_MARKERS = ("/login", "/signin", "/sign-in", "/auth", "/session", "/account/login")


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
            source_kind = UrlKind.STATIC_PUBLIC
        if source_kind in {UrlKind.LOGIN_REQUIRED, UrlKind.PARTIAL_LOGIN, UrlKind.RESTRICTED}:
            return True

    return score >= 3 and len(markdown.strip()) < 2000


def read_url(value: Any) -> str:
    return value if isinstance(value, str) else ""


def has_auth_path(url: str) -> bool:
    if not url:
        return False
    path = urlparse(url).path.lower()
    return any(marker in path for marker in AUTH_PATH_MARKERS)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
