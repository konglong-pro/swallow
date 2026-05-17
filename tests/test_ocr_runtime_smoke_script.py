from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SMOKE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ocr_runtime_smoke.py"
spec = importlib.util.spec_from_file_location("ocr_runtime_smoke", SMOKE_SCRIPT)
assert spec is not None
ocr_runtime_smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ocr_runtime_smoke)


def test_smoke_token_check_allows_missing_spaces():
    ocr_runtime_smoke.assert_ocr_result("image", "Swallow OCRSmokeTest 123")


def test_smoke_token_check_reports_missing_tokens():
    with pytest.raises(AssertionError, match="missing tokens"):
        ocr_runtime_smoke.assert_ocr_result("image", "unrelated text")
