from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from swallow.sdk.errors import IngestSandboxError


_HTTP_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
}
_SENSITIVE_FILENAMES = {
    ".env",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}


def default_allowed_roots(cwd: Path, store_root: Path) -> list[Path]:
    return [cwd.resolve(strict=False), store_root.resolve(strict=False)]


def resolve_from_cwd(path: Path | str, cwd: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve(strict=False)


def resolve_allowed_file(path: Path | str, *, cwd: Path, allowed_roots: list[Path]) -> Path:
    candidate = resolve_from_cwd(path, cwd)
    if not candidate.is_file():
        raise FileNotFoundError(f"Input file does not exist: {candidate}")
    if not any(is_relative_to(candidate, root) for root in allowed_roots):
        roots = ", ".join(str(root) for root in allowed_roots)
        raise IngestSandboxError(f"Input path is outside allowed roots: {candidate}; allowed roots: {roots}")
    reject_sensitive_path(candidate)
    return candidate


def reject_sensitive_path(path: Path) -> None:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if name in _SENSITIVE_FILENAMES:
        raise IngestSandboxError(f"Sensitive input path is not allowed: {path}")
    if ".git" in parts and name == "config":
        raise IngestSandboxError(f"Sensitive input path is not allowed: {path}")
    if ".ssh" in parts and name in _SENSITIVE_FILENAMES:
        raise IngestSandboxError(f"Sensitive input path is not allowed: {path}")


def validate_ingest_url(url: str, *, allow_localhost: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _HTTP_SCHEMES or not parsed.netloc:
        raise IngestSandboxError(f"URL ingest only supports absolute http and https URLs: {url}")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise IngestSandboxError(f"URL must include a host: {url}")
    if not allow_localhost and is_blocked_host(host):
        raise IngestSandboxError(f"URL host is not allowed by the SDK sandbox: {host}")
    return url


def is_blocked_host(host: str) -> bool:
    if host in _BLOCKED_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_multicast
        or address.is_unspecified
    )


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
