from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    script = SCRIPTS_DIR / name
    spec = importlib.util.spec_from_file_location(script.stem, script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_asr_smoke_token_check_reports_missing_tokens():
    asr_runtime_smoke = load_script("asr_runtime_smoke.py")

    with pytest.raises(AssertionError, match="missing tokens"):
        asr_runtime_smoke.assert_asr_result("unrelated transcript", ("swallow",))


def test_asr_smoke_token_check_accepts_tokens_case_insensitively():
    asr_runtime_smoke = load_script("asr_runtime_smoke.py")

    asr_runtime_smoke.assert_asr_result("Swallow AUDIO smoke THREE", ("swallow", "audio", "three"))


def test_web_smoke_token_check_reports_missing_tokens():
    web_runtime_smoke = load_script("web_runtime_smoke.py")

    with pytest.raises(AssertionError, match="missing tokens"):
        web_runtime_smoke.assert_web_result("unrelated page", ("Example",))


def test_web_smoke_resolves_default_and_explicit_targets():
    web_runtime_smoke = load_script("web_runtime_smoke.py")

    assert web_runtime_smoke.resolve_targets(None) == ["router", "firecrawl", "crawl4ai", "playwright", "profile"]
    assert web_runtime_smoke.resolve_targets(["playwright", "all", "playwright"]) == [
        "playwright",
        "router",
        "firecrawl",
        "crawl4ai",
        "profile",
    ]


def test_web_smoke_worker_result_requires_success():
    web_runtime_smoke = load_script("web_runtime_smoke.py")
    from swallow.core.models import WorkerResult

    failed = WorkerResult(
        status="failed",
        worker_name="crawl4ai_worker",
        worker_version="0.1.0",
        errors=["crawl4ai failed"],
    )

    with pytest.raises(AssertionError, match="crawl4ai smoke failed"):
        web_runtime_smoke.assert_worker_result("crawl4ai", failed, ("Example",))


def test_service_smoke_payload_check_requires_success():
    service_runtime_smoke = load_script("service_runtime_smoke.py")

    with pytest.raises(AssertionError, match="Expected success payload"):
        service_runtime_smoke.assert_success_payload(200, {"status": "failed"})


def test_runtime_smoke_all_builds_default_commands(tmp_path):
    runtime_smoke_all = load_script("runtime_smoke_all.py")

    args = runtime_smoke_all.parse_args([])
    commands = runtime_smoke_all.build_smoke_commands(args, tmp_path / "store")

    assert [command.name for command in commands] == ["service", "ocr", "asr", "web"]
    assert commands[0].command[-2:] == ["--store", str(tmp_path / "store" / "service")]
    assert "--model" in commands[2].command
    assert "tiny" in commands[2].command
    assert commands[3].command[-2:] == ["--url", "https://example.com/"]


def test_runtime_smoke_all_honors_skips_and_passthrough_args(tmp_path):
    runtime_smoke_all = load_script("runtime_smoke_all.py")

    args = runtime_smoke_all.parse_args(
        [
            "--skip-service",
            "--skip-ocr",
            "--asr-audio",
            "sample.wav",
            "--asr-model",
            "base",
            "--web-url",
            "https://example.org/",
            "--web-token",
            "Example Domain",
            "--web-target",
            "playwright",
            "--web-require-firecrawl",
            "--web-profile-dir",
            "profile-dir",
        ]
    )
    commands = runtime_smoke_all.build_smoke_commands(args, tmp_path / "store")

    assert [command.name for command in commands] == ["asr", "web"]
    assert "--audio" in commands[0].command
    assert "sample.wav" in commands[0].command
    assert "base" in commands[0].command
    assert "https://example.org/" in commands[1].command
    assert "--target" in commands[1].command
    assert "playwright" in commands[1].command
    assert "--require-firecrawl" in commands[1].command
    assert commands[1].command[-2:] == ["--profile-dir", "profile-dir"]
