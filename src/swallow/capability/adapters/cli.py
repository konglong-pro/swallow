from __future__ import annotations

from pathlib import Path
from typing import Sequence

from swallow.sdk import CliIngestClient


def create_cli_client(
    *,
    store_root: Path,
    cwd: Path,
    allowed_roots: Sequence[Path],
    config_path: Path | None,
    command: str | Sequence[str],
    max_processes: int,
) -> CliIngestClient:
    return CliIngestClient(
        store_root=store_root,
        cwd=cwd,
        allowed_roots=allowed_roots,
        config_path=config_path,
        command=command,
        max_processes=max_processes,
    )
