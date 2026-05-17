from __future__ import annotations

from swallow.core.raw_store import RawStore


def test_raw_store_deduplicates_by_sha256(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("same content\n", encoding="utf-8")

    store = RawStore(tmp_path)
    first = store.save_immutable(source)
    second = store.save_immutable(source)

    assert first.raw_id == second.raw_id
    assert first.sha256 == second.sha256
    assert first.path == second.path

    raw_dirs = [path for path in (tmp_path / "raw_store").iterdir() if path.is_dir()]
    assert len(raw_dirs) == 1
    assert (raw_dirs[0] / "original.txt").exists()
    assert (raw_dirs[0] / "original.meta.json").exists()


def test_raw_store_deduplicates_url_references_by_canonical_payload(tmp_path):
    store = RawStore(tmp_path)
    first = store.save_url_reference("https://example.com/article")
    second = store.save_url_reference("https://example.com/article")

    assert first.raw_id == second.raw_id
    assert first.sha256 == second.sha256
    assert first.path == second.path
    assert first.original_filename == "example.com-article.url"
    assert first.mime_type == "application/json"

    raw_dirs = [path for path in (tmp_path / "raw_store").iterdir() if path.is_dir()]
    assert len(raw_dirs) == 1
    assert (raw_dirs[0] / "original.url.json").exists()
    assert (raw_dirs[0] / "original.meta.json").exists()
