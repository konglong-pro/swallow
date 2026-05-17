from __future__ import annotations

from types import SimpleNamespace

from swallow.core import doctor
from swallow.core.config import IngestConfig


def test_check_module_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda module_name: None)

    check = doctor.check_module("markitdown", "markitdown", "uv sync --extra markitdown")

    assert check.status == "missing"
    assert check.name == "markitdown"
    assert check.install_hint == "uv sync --extra markitdown"


def test_check_ffmpeg_accepts_configured_path():
    config = IngestConfig.from_mapping(
        {"workers": {"faster_whisper": {"ffmpeg_path": "C:/tools/ffmpeg.exe"}}}
    )

    check = doctor.check_ffmpeg(config)

    assert check.status == "ok"
    assert "C:/tools/ffmpeg.exe" in check.detail


def test_firecrawl_api_key_uses_configured_env_name(monkeypatch):
    monkeypatch.delenv("CUSTOM_FIRECRAWL_KEY", raising=False)
    config = IngestConfig.from_mapping({"workers": {"firecrawl": {"api_key_env": "CUSTOM_FIRECRAWL_KEY"}}})

    missing = doctor.check_firecrawl_api_key(config)
    assert missing.status == "warning"
    assert "CUSTOM_FIRECRAWL_KEY is not set" in missing.detail

    monkeypatch.setenv("CUSTOM_FIRECRAWL_KEY", "secret")
    present = doctor.check_firecrawl_api_key(config)
    assert present.status == "ok"
    assert present.detail == "CUSTOM_FIRECRAWL_KEY is set"


def test_playwright_profile_dir_reports_missing_and_existing(tmp_path):
    missing_config = IngestConfig.from_mapping({"workers": {"playwright_profile": {"profile_dir": str(tmp_path / "new")}}})

    missing = doctor.check_playwright_profile_dir(missing_config)
    assert missing.status == "warning"
    assert "does not exist yet" in missing.detail

    existing_dir = tmp_path / "existing"
    existing_dir.mkdir()
    existing_config = IngestConfig.from_mapping(
        {"workers": {"playwright_profile": {"profile_dir": str(existing_dir)}}}
    )

    existing = doctor.check_playwright_profile_dir(existing_config)
    assert existing.status == "ok"
    assert str(existing_dir) in existing.detail


def test_firecrawl_runtime_validates_client_shape(monkeypatch):
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: SimpleNamespace(Firecrawl=object))

    check = doctor.check_firecrawl_runtime()

    assert check.status == "ok"
    assert check.name == "firecrawl_runtime"


def test_firecrawl_runtime_reports_missing_client_class(monkeypatch):
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: SimpleNamespace())

    check = doctor.check_firecrawl_runtime()

    assert check.status == "missing"
    assert "client class" in check.detail


def test_crawl4ai_runtime_validates_async_web_crawler(monkeypatch):
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: SimpleNamespace(AsyncWebCrawler=object))

    check = doctor.check_crawl4ai_runtime()

    assert check.status == "ok"
    assert check.name == "crawl4ai_runtime"


def test_crawl4ai_runtime_reports_missing_async_web_crawler(monkeypatch):
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: SimpleNamespace())

    check = doctor.check_crawl4ai_runtime()

    assert check.status == "missing"
    assert "AsyncWebCrawler" in check.detail


def test_run_doctor_deep_adds_web_runtime_checks(monkeypatch):
    def fake_check_module(name: str, module_name: str, install_hint: str, *, package_name: str | None = None):
        return doctor.DoctorCheck(name=name, status="ok", detail="ok")

    monkeypatch.setattr(doctor, "check_module", fake_check_module)
    monkeypatch.setattr(
        doctor,
        "check_firecrawl_api_key",
        lambda config: doctor.DoctorCheck(name="firecrawl_api_key", status="ok", detail="ok"),
    )
    monkeypatch.setattr(
        doctor,
        "check_firecrawl_runtime",
        lambda: doctor.DoctorCheck(name="firecrawl_runtime", status="ok", detail="ok"),
    )
    monkeypatch.setattr(
        doctor,
        "check_crawl4ai_runtime",
        lambda: doctor.DoctorCheck(name="crawl4ai_runtime", status="ok", detail="ok"),
    )
    monkeypatch.setattr(
        doctor,
        "check_playwright_chromium",
        lambda: doctor.DoctorCheck(name="playwright_chromium", status="ok", detail="ok"),
    )
    monkeypatch.setattr(
        doctor,
        "check_playwright_profile_dir",
        lambda config: doctor.DoctorCheck(name="playwright_profile_dir", status="warning", detail="missing"),
    )
    monkeypatch.setattr(
        doctor,
        "check_playwright_profile_context",
        lambda: doctor.DoctorCheck(name="playwright_profile_context", status="ok", detail="ok"),
    )
    config = IngestConfig.from_mapping(
        {
            "workers": {
                "markitdown": {"enabled": False},
                "paddleocr": {"enabled": False},
                "faster_whisper": {"enabled": False},
            }
        }
    )

    shallow_names = {check.name for check in doctor.run_doctor(config, deep=False).checks}
    deep_names = {check.name for check in doctor.run_doctor(config, deep=True).checks}

    assert "firecrawl_runtime" not in shallow_names
    assert "crawl4ai_runtime" not in shallow_names
    assert "playwright_chromium" not in shallow_names
    assert "playwright_profile_context" not in shallow_names
    assert {"firecrawl_runtime", "crawl4ai_runtime", "playwright_chromium", "playwright_profile_context"} <= deep_names


def test_run_doctor_skips_disabled_workers(monkeypatch):
    checked_modules: list[str] = []

    def fake_check_module(name: str, module_name: str, install_hint: str, *, package_name: str | None = None):
        checked_modules.append(name)
        return doctor.DoctorCheck(name=name, status="ok", detail="ok")

    monkeypatch.setattr(doctor, "check_module", fake_check_module)
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda config: doctor.DoctorCheck(name="ffmpeg", status="ok", detail="ok"))
    monkeypatch.setattr(
        doctor,
        "check_firecrawl_api_key",
        lambda config: doctor.DoctorCheck(name="firecrawl_api_key", status="warning", detail="missing"),
    )
    config = IngestConfig.from_mapping({"workers": {"markitdown": {"enabled": False}, "crawl4ai": {"enabled": False}}})

    report = doctor.run_doctor(config)

    assert "markitdown" not in checked_modules
    assert "crawl4ai" not in checked_modules
    assert report.checks[0].name == "python"
