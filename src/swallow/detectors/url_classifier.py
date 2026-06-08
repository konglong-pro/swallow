from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, urlparse, urlunparse


class UrlKind(str, Enum):
    CHATGPT_SHARE = "chatgpt_share"
    GEMINI_SHARE = "gemini_share"
    CLAUDE_SHARE = "claude_share"
    DEEPSEEK_SHARE = "deepseek_share"
    WECHAT_ARTICLE = "wechat_article"
    YOUTUBE_VIDEO = "youtube_video"
    SHORT_URL = "short_url"
    DYNAMIC_WEB = "dynamic_web"
    LOGIN_REQUIRED_WEB = "login_required_web"
    RESTRICTED_WEB = "restricted_web"
    GENERIC_WEB = "generic_web"

    # Backward-compatible aliases for existing tests and call sites.
    STATIC_PUBLIC = "generic_web"
    DYNAMIC = "dynamic_web"
    LOGIN_REQUIRED = "login_required_web"
    PARTIAL_LOGIN = "login_required_web"
    RESTRICTED = "restricted_web"


@dataclass(frozen=True)
class UrlClassification:
    kind: UrlKind
    platform: str | None
    source_url: str
    normalized_url: str
    confidence: float
    needs_redirect_resolution: bool = False
    needs_browser_profile: bool = False

    @property
    def host(self) -> str:
        return urlparse(self.normalized_url).hostname or ""


LOGIN_REQUIRED_HOSTS = {
    "chatgpt.com",
    "chat.openai.com",
    "claude.ai",
    "chat.deepseek.com",
    "gemini.google.com",
}

PARTIAL_LOGIN_HOSTS: set[str] = set()

RESTRICTED_HOSTS: set[str] = set()

DYNAMIC_HOST_PREFIXES = {
    "app.",
}

SHORT_URL_HOSTS: set[str] = set()


def classify_url(url: str) -> UrlClassification:
    source_url = url.strip()
    normalized_url = normalize_url(source_url)
    parsed = urlparse(normalized_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")

    if host in {"chatgpt.com", "chat.openai.com"} and path.startswith("/share/"):
        return classification(UrlKind.CHATGPT_SHARE, "chatgpt", source_url, normalized_url)

    if host == "gemini.google.com" and path.startswith("/share/"):
        return classification(UrlKind.GEMINI_SHARE, "gemini", source_url, normalized_url)

    if host == "g.co" and path.startswith("/gemini/share/"):
        return classification(
            UrlKind.GEMINI_SHARE,
            "gemini",
            source_url,
            normalized_url,
            needs_redirect_resolution=True,
        )

    if host == "claude.ai" and path.startswith("/share/"):
        return classification(UrlKind.CLAUDE_SHARE, "claude", source_url, normalized_url)

    if host in {"chat.deepseek.com", "deepseek.com", "www.deepseek.com"} and path.startswith("/share/"):
        return classification(UrlKind.DEEPSEEK_SHARE, "deepseek", source_url, normalized_url)

    if host == "mp.weixin.qq.com" and (path.startswith("/s") or "__biz" in parse_qs(parsed.query)):
        return classification(UrlKind.WECHAT_ARTICLE, "wechat", source_url, normalized_url)

    if is_youtube_video(host, path, parsed.query):
        return classification(UrlKind.YOUTUBE_VIDEO, "youtube", source_url, normalized_url)

    if host in SHORT_URL_HOSTS:
        return classification(
            UrlKind.SHORT_URL,
            None,
            source_url,
            normalized_url,
            needs_redirect_resolution=True,
        )

    if host_matches(host, RESTRICTED_HOSTS):
        return classification(
            UrlKind.RESTRICTED_WEB,
            restricted_platform(host),
            source_url,
            normalized_url,
            needs_browser_profile=True,
        )

    if host_matches(host, LOGIN_REQUIRED_HOSTS) or host in PARTIAL_LOGIN_HOSTS:
        return classification(
            UrlKind.LOGIN_REQUIRED_WEB,
            login_platform(host),
            source_url,
            normalized_url,
            needs_browser_profile=True,
        )

    if any(host.startswith(prefix) for prefix in DYNAMIC_HOST_PREFIXES):
        return classification(UrlKind.DYNAMIC_WEB, None, source_url, normalized_url, confidence=0.75)

    return classification(UrlKind.GENERIC_WEB, None, source_url, normalized_url, confidence=0.6)


def classification(
    kind: UrlKind,
    platform: str | None,
    source_url: str,
    normalized_url: str,
    *,
    confidence: float = 1.0,
    needs_redirect_resolution: bool = False,
    needs_browser_profile: bool = False,
) -> UrlClassification:
    return UrlClassification(
        kind=kind,
        platform=platform,
        source_url=source_url,
        normalized_url=normalized_url,
        confidence=confidence,
        needs_redirect_resolution=needs_redirect_resolution,
        needs_browser_profile=needs_browser_profile,
    )


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    return urlunparse((scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def host_matches(host: str, domains: set[str]) -> bool:
    return host in domains or any(host.endswith(f".{domain}") for domain in domains)


def is_youtube_video(host: str, path: str, query: str) -> bool:
    if host in {"youtu.be", "www.youtu.be"} and path.strip("/"):
        return True
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path == "/watch" and "v" in parse_qs(query):
            return True
        if path.startswith("/shorts/"):
            return True
    return False


def restricted_platform(host: str) -> str | None:
    return None


def login_platform(host: str) -> str | None:
    if host.endswith("chatgpt.com") or host.endswith("chat.openai.com"):
        return "chatgpt"
    if host.endswith("gemini.google.com"):
        return "gemini"
    if host.endswith("claude.ai"):
        return "claude"
    if host.endswith("deepseek.com"):
        return "deepseek"
    return None
