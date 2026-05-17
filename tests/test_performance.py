from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from swallow.core.batch import BatchRunner
from swallow.core.config import IngestConfig
from swallow.core.runner import IngestRunner


pytestmark = pytest.mark.performance

if os.getenv("RUN_PERFORMANCE_TESTS") != "1":
    pytest.skip("Set RUN_PERFORMANCE_TESTS=1 to run performance thresholds.", allow_module_level=True)


FIXTURES = Path(__file__).parent / "fixtures"


def test_1mb_plain_text_ingest_under_10_seconds(tmp_path):
    source = tmp_path / "one-mb.txt"
    source.write_text("# 1MB\n\n" + ("swallow performance content " * 40_000), encoding="utf-8")

    elapsed, result = timed(lambda: IngestRunner(store_root=tmp_path / "store").ingest_file(source))

    assert elapsed < read_threshold("SWALLOW_PERF_TEXT_SECONDS", default=10.0)
    assert (tmp_path / "store" / result.document_path).exists()


def test_10_page_electronic_pdf_ingest_under_30_seconds(tmp_path):
    pytest.importorskip("markitdown")
    source = FIXTURES / "files" / "electronic.pdf"

    elapsed, result = timed(lambda: IngestRunner(store_root=tmp_path / "store").ingest_file(source))

    assert elapsed < read_threshold("SWALLOW_PERF_PDF_SECONDS", default=30.0)
    assert (tmp_path / "store" / result.document_path).exists()


def test_100_small_text_batch_completes_under_60_seconds(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for index in range(100):
        (inputs / f"small-{index:03d}.txt").write_text("# Small\n\n" + f"item {index} " * 70, encoding="utf-8")

    elapsed, summary = timed(lambda: BatchRunner(store_root=tmp_path / "store").run([str(inputs / "*")], max_workers=8))

    assert elapsed < read_threshold("SWALLOW_PERF_BATCH_SECONDS", default=60.0)
    assert summary["status"] == "success"
    assert summary["total"] == 100
    assert summary["success"] == 100


def test_ocr_fixture_performance_when_enabled(tmp_path):
    if os.getenv("RUN_OCR_TESTS") != "1":
        pytest.skip("Set RUN_OCR_TESTS=1 with RUN_PERFORMANCE_TESTS=1 to run OCR performance.")

    elapsed, result = timed(lambda: IngestRunner(store_root=tmp_path / "store").ingest_file(FIXTURES / "ocr" / "scanned.pdf"))

    assert elapsed < read_threshold("SWALLOW_PERF_OCR_SECONDS", default=180.0)
    assert (tmp_path / "store" / result.document_path).exists()


def test_asr_fixture_performance_when_enabled(tmp_path):
    if os.getenv("RUN_ASR_TESTS") != "1":
        pytest.skip("Set RUN_ASR_TESTS=1 with RUN_PERFORMANCE_TESTS=1 to run ASR performance.")

    config = IngestConfig.from_mapping(
        {"workers": {"faster_whisper": {"model": "tiny", "device": "cpu", "compute_type": "int8"}}}
    )
    elapsed, result = timed(
        lambda: IngestRunner(store_root=tmp_path / "store", config=config).ingest_file(FIXTURES / "audio" / "short-zh.wav")
    )

    assert elapsed < read_threshold("SWALLOW_PERF_ASR_SECONDS", default=180.0)
    assert (tmp_path / "store" / result.document_path).exists()


def timed(fn):
    started = time.perf_counter()
    result = fn()
    return time.perf_counter() - started, result


def read_threshold(name: str, *, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default
