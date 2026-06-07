from __future__ import annotations

from pathlib import Path
from typing import Sequence

from swallow.sdk import QueueIngestClient


def create_queue_client(
    *,
    store_root: Path,
    cwd: Path,
    allowed_roots: Sequence[Path],
) -> QueueIngestClient:
    return QueueIngestClient(store_root=store_root, cwd=cwd, allowed_roots=allowed_roots)
