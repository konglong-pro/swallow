from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from swallow.core.ids import raw_id_from_sha256
from swallow.core.models import RawRecord
from swallow.core.time import now_iso


class RawStore:
    def __init__(self, store_root: Path | str = ".") -> None:
        self.store_root = Path(store_root)
        self.raw_root = self.store_root / "raw_store"

    def save_immutable(self, input_path: Path | str) -> RawRecord:
        source = Path(input_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Input file does not exist: {source}")

        sha256 = compute_sha256(source)
        raw_dir = self.raw_root / sha256
        meta_path = raw_dir / "original.meta.json"

        if meta_path.exists():
            return RawRecord.model_validate_json(meta_path.read_text(encoding="utf-8"))

        raw_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower() or ".bin"
        original_path = raw_dir / f"original{suffix}"
        shutil.copy2(source, original_path)

        mime_type, _ = mimetypes.guess_type(source.name)
        record = RawRecord(
            raw_id=raw_id_from_sha256(sha256),
            sha256=sha256,
            path=as_posix_relative(original_path, self.store_root),
            original_filename=source.name,
            mime_type=mime_type,
            size_bytes=source.stat().st_size,
            created_at=now_iso(),
        )

        meta_path.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    def save_url_reference(self, url: str) -> RawRecord:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"URL must be an absolute http(s) URL: {url}")

        payload = (json.dumps({"source_type": "url", "source_url": url}, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        sha256 = compute_sha256_bytes(payload)
        raw_dir = self.raw_root / sha256
        meta_path = raw_dir / "original.meta.json"

        if meta_path.exists():
            return RawRecord.model_validate_json(meta_path.read_text(encoding="utf-8"))

        raw_dir.mkdir(parents=True, exist_ok=True)
        original_path = raw_dir / "original.url.json"
        original_path.write_bytes(payload)

        record = RawRecord(
            raw_id=raw_id_from_sha256(sha256),
            sha256=sha256,
            path=as_posix_relative(original_path, self.store_root),
            original_filename=url_original_filename(url),
            mime_type="application/json",
            size_bytes=len(payload),
            created_at=now_iso(),
        )

        meta_path.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    def resolve_path(self, raw: RawRecord) -> Path:
        return self.store_root / raw.path


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def as_posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def url_original_filename(url: str) -> str:
    parsed = urlparse(url)
    candidate = f"{parsed.netloc}{parsed.path}".strip("/") or "url"
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-._")
    if not candidate:
        candidate = "url"
    return f"{candidate[:80]}.url"
