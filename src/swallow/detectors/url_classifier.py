from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class UrlKind(str, Enum):
    STATIC_PUBLIC = "static_public"
    DYNAMIC = "dynamic"
    LOGIN_REQUIRED = "login_required"
    PARTIAL_LOGIN = "partial_login"
    RESTRICTED = "restricted"


class UrlClassification:
    def __init__(self, kind: UrlKind, host: str) -> None:
        self.kind = kind
        self.host = host


LOGIN_REQUIRED_HOSTS = {
    "chatgpt.com",
    "claude.ai",
    "gemini.google.com",
}

PARTIAL_LOGIN_HOSTS = {
    "zhihu.com",
    "www.zhihu.com",
}

RESTRICTED_HOSTS = {
    "x.com",
    "twitter.com",
}

DYNAMIC_HOSTS = {
    "app.",
}


def classify_url(url: str) -> UrlClassification:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in LOGIN_REQUIRED_HOSTS or any(host.endswith(f".{domain}") for domain in LOGIN_REQUIRED_HOSTS):
        return UrlClassification(UrlKind.LOGIN_REQUIRED, host)

    if host in RESTRICTED_HOSTS or any(host.endswith(f".{domain}") for domain in RESTRICTED_HOSTS):
        return UrlClassification(UrlKind.RESTRICTED, host)

    if host in PARTIAL_LOGIN_HOSTS:
        return UrlClassification(UrlKind.PARTIAL_LOGIN, host)

    if any(host.startswith(prefix) for prefix in DYNAMIC_HOSTS):
        return UrlClassification(UrlKind.DYNAMIC, host)

    return UrlClassification(UrlKind.STATIC_PUBLIC, host)
