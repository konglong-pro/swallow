from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from swallow.core.batch import BatchRunner
from swallow.core.runner import IngestRunner


pytestmark = pytest.mark.concurrency

FIXTURES = Path(__file__).parent / "fixtures"


def test_concurrent_ingest_20_small_files_have_isolated_jobs(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    paths: list[Path] = []
    for index in range(20):
        path = inputs / f"small-{index:02d}.txt"
        path.write_text(f"# Small {index}\n\n" + "concurrent ingest content " * 20, encoding="utf-8")
        paths.append(path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(IngestRunner(store_root=tmp_path / "store").ingest_file, path) for path in paths]
        results = [future.result() for future in as_completed(futures)]

    job_ids = {result.job.id for result in results}
    assert len(job_ids) == 20
    assert len(list((tmp_path / "store" / "jobs").iterdir())) == 20
    for result in results:
        job_dir = tmp_path / "store" / "jobs" / result.job.id
        assert (job_dir / "document.md").exists()
        assert (job_dir / "manifest.json").exists()
        assert (job_dir / "trace.jsonl").exists()


def test_concurrent_ingest_5_pdfs_have_isolated_jobs_and_raw_dedupe(tmp_path):
    pytest.importorskip("markitdown")
    source = FIXTURES / "files" / "electronic.pdf"
    inputs = tmp_path / "pdfs"
    inputs.mkdir()
    paths: list[Path] = []
    for index in range(5):
        path = inputs / f"electronic-{index}.pdf"
        shutil.copy2(source, path)
        paths.append(path)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(IngestRunner(store_root=tmp_path / "store").ingest_file, path) for path in paths]
        results = [future.result() for future in as_completed(futures)]

    assert len({result.job.id for result in results}) == 5
    assert len({result.raw.sha256 for result in results}) == 1
    assert len(list((tmp_path / "store" / "jobs").iterdir())) == 5
    assert len(list((tmp_path / "store" / "raw_store").iterdir())) == 1


def test_repeated_ingest_same_file_10_times_reuses_raw_store(tmp_path):
    source = tmp_path / "repeat.txt"
    source.write_text("# Repeat\n\n" + "same raw content " * 30, encoding="utf-8")
    runner = IngestRunner(store_root=tmp_path / "store")

    results = [runner.ingest_file(source) for _ in range(10)]

    assert len({result.job.id for result in results}) == 10
    assert len({result.raw.raw_id for result in results}) == 1
    assert len(list((tmp_path / "store" / "jobs").iterdir())) == 10
    assert len(list((tmp_path / "store" / "raw_store").iterdir())) == 1


def test_repeated_batch_same_inputs_5_times_remains_isolated(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for index in range(4):
        (inputs / f"batch-{index}.txt").write_text("# Batch\n\n" + f"item {index} " * 70, encoding="utf-8")
    batch_runner = BatchRunner(store_root=tmp_path / "store")

    summaries = [batch_runner.run([str(inputs / "*")], max_workers=2) for _ in range(5)]

    assert all(summary["status"] == "success" for summary in summaries)
    assert len({summary["batch_id"] for summary in summaries}) == 5
    assert len(list((tmp_path / "store" / "batch_runs").iterdir())) == 5
    assert len(list((tmp_path / "store" / "jobs").iterdir())) == 20


def test_concurrent_batch_failure_does_not_stop_successful_items(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for index in range(6):
        (inputs / f"good-{index}.txt").write_text("# Good\n\n" + f"content {index} " * 30, encoding="utf-8")
    for index in range(2):
        (inputs / f"bad-{index}.bin").write_bytes(b"\x00\x01unsupported")

    summary = BatchRunner(store_root=tmp_path / "store").run([str(inputs / "*")], max_workers=4)

    assert summary["status"] == "partial"
    assert summary["total"] == 8
    assert summary["success"] == 6
    assert summary["failed"] == 2
    for item in summary["jobs"]:
        assert item["job_id"].startswith("ing_")
        assert item["manifest"].startswith("jobs/")
        assert item["trace"].startswith("jobs/")
