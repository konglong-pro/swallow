from __future__ import annotations

from swallow.capability.adapters.cli import create_cli_client
from swallow.capability.adapters.http import create_http_client
from swallow.capability.adapters.local import create_local_client
from swallow.capability.adapters.queue import create_queue_client

__all__ = [
    "create_cli_client",
    "create_http_client",
    "create_local_client",
    "create_queue_client",
]
