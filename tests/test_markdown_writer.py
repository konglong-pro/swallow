from __future__ import annotations

import yaml

from swallow.core.runner import IngestRunner


def test_document_markdown_has_front_matter_and_source_comment(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("line one\r\n\r\n\r\n\r\nline two", encoding="utf-8")

    result = IngestRunner(store_root=tmp_path).ingest_file(sample)
    document = (tmp_path / result.document_path).read_text(encoding="utf-8")

    assert document.startswith("---\n")
    front_matter_text = document.split("---", 2)[1]
    front_matter = yaml.safe_load(front_matter_text)

    assert front_matter["ingest_document_id"].startswith("doc_")
    assert front_matter["job_id"].startswith("ing_")
    assert front_matter["raw_id"] == result.raw.raw_id
    assert front_matter["source_type"] == "file"
    assert front_matter["source_subtype"] == "txt"
    assert front_matter["primary_worker"] == "plain_text_worker"
    assert "plain_text_worker@0.1.0" in front_matter["worker_chain"]
    assert "markdown_normalizer@0.1.0" in front_matter["worker_chain"]
    assert "<!-- ingest:source" in document
    assert "raw_id: " + result.raw.raw_id in document
    assert "sha256: " + result.raw.sha256 in document
    assert "line one\n\n\nline two\n" in document
