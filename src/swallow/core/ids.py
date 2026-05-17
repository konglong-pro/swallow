from __future__ import annotations

from datetime import datetime

import ulid


def new_job_id() -> str:
    return f"ing_{ulid.new()}"


def new_doc_id() -> str:
    return f"doc_{ulid.new()}"


def raw_id_from_sha256(sha256: str) -> str:
    return f"raw_{sha256[:12]}"


def new_batch_id() -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    suffix = str(ulid.new())[-8:].lower()
    return f"batch_{stamp}_{suffix}"
