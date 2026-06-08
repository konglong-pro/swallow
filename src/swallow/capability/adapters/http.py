from __future__ import annotations

from pathlib import Path
from typing import Sequence

from swallow.capability.errors import CapabilityConfigError
from swallow.sdk import HttpIngestClient


def create_http_client(
    *,
    base_url: str | None,
    cwd: Path,
    allowed_roots: Sequence[Path],
) -> HttpIngestClient:
    if not base_url:
        raise CapabilityConfigError("The service profile requires a service_url/base_url")
    return HttpIngestClient(base_url=base_url, cwd=cwd, allowed_roots=allowed_roots)
