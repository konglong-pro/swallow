from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from swallow.core.config import IngestConfig
from swallow.workers.faster_whisper_worker import resolve_ffmpeg_executable, short_error

CheckStatus = Literal["ok", "warning", "missing"]


class DoctorCheck(BaseModel):
    name: str
    status: CheckStatus
    detail: str
    install_hint: str | None = None


class DoctorReport(BaseModel):
    python_version: str
    executable: str
    platform: str
    checks: list[DoctorCheck] = Field(default_factory=list)

    @property
    def has_missing(self) -> bool:
        return any(check.status == "missing" for check in self.checks)


def run_doctor(config: IngestConfig | None = None, *, deep: bool = False) -> DoctorReport:
    config = config or IngestConfig()
    checks: list[DoctorCheck] = [check_python_version()]

    if config.worker_enabled("markitdown_worker"):
        checks.append(check_module("markitdown", "markitdown", "uv sync --extra markitdown"))

    if config.worker_enabled("paddleocr_worker"):
        checks.extend(
            [
                check_module("paddleocr", "paddleocr", "uv sync --extra ocr"),
                check_module("pypdfium2", "pypdfium2", "uv sync --extra ocr"),
                check_module("pillow", "PIL", "uv sync --extra ocr", package_name="pillow"),
            ]
        )

    if config.worker_enabled("faster_whisper_worker"):
        checks.extend(
            [
                check_module("faster-whisper", "faster_whisper", "uv sync --extra asr", package_name="faster-whisper"),
                check_module("imageio-ffmpeg", "imageio_ffmpeg", "uv sync --extra asr", package_name="imageio-ffmpeg"),
                check_ffmpeg(config),
            ]
        )

    if config.worker_enabled("firecrawl_worker"):
        checks.append(check_module("firecrawl-py", "firecrawl", "uv sync --extra web", package_name="firecrawl-py"))
        checks.append(check_firecrawl_api_key(config))
        if deep:
            checks.append(check_firecrawl_runtime())

    if config.worker_enabled("crawl4ai_worker"):
        checks.append(check_module("crawl4ai", "crawl4ai", "uv sync --extra web"))
        if deep:
            checks.append(check_crawl4ai_runtime())

    playwright_needed = config.worker_enabled("playwright_worker") or config.worker_enabled("playwright_profile_worker")
    if playwright_needed:
        checks.append(check_module("playwright", "playwright", "uv sync --extra web"))
        if deep:
            checks.append(check_playwright_chromium())

    if config.worker_enabled("playwright_profile_worker"):
        checks.append(check_playwright_profile_dir(config))
        if deep:
            checks.append(check_playwright_profile_context())

    return DoctorReport(
        python_version=platform.python_version(),
        executable=sys.executable,
        platform=platform.platform(),
        checks=checks,
    )


def check_python_version() -> DoctorCheck:
    version_info = sys.version_info
    if version_info >= (3, 11):
        return DoctorCheck(
            name="python",
            status="ok",
            detail=f"{platform.python_version()} satisfies Python 3.11+",
        )
    return DoctorCheck(
        name="python",
        status="missing",
        detail=f"{platform.python_version()} is too old; Swallow requires Python 3.11+",
        install_hint="Install Python 3.11 and recreate the uv environment",
    )


def check_module(name: str, module_name: str, install_hint: str, *, package_name: str | None = None) -> DoctorCheck:
    if importlib.util.find_spec(module_name) is None:
        return DoctorCheck(
            name=name,
            status="missing",
            detail=f"Python module `{module_name}` is not importable",
            install_hint=install_hint,
        )
    package = package_name or name
    return DoctorCheck(
        name=name,
        status="ok",
        detail=f"installed{format_package_version(package)}",
    )


def check_ffmpeg(config: IngestConfig) -> DoctorCheck:
    params = config.worker_params("faster_whisper_worker")
    preferred = params.get("ffmpeg_path") or params.get("ffmpeg") or os.getenv("SWALLOW_FFMPEG_PATH")
    try:
        resolved = resolve_ffmpeg_executable(str(preferred) if preferred else None)
    except Exception as error:
        return DoctorCheck(
            name="ffmpeg",
            status="missing",
            detail=short_error(error),
            install_hint="Install ffmpeg on PATH or use `uv sync --extra asr` for imageio-ffmpeg fallback",
        )
    return DoctorCheck(name="ffmpeg", status="ok", detail=f"resolved to {resolved}")


def check_firecrawl_api_key(config: IngestConfig) -> DoctorCheck:
    params = config.worker_params("firecrawl_worker")
    api_key_env = str(params.get("api_key_env") or "FIRECRAWL_API_KEY")
    if os.getenv(api_key_env):
        return DoctorCheck(name="firecrawl_api_key", status="ok", detail=f"{api_key_env} is set")
    return DoctorCheck(
        name="firecrawl_api_key",
        status="warning",
        detail=f"{api_key_env} is not set; Firecrawl may fail and fall back to Crawl4AI/Playwright",
        install_hint=f"Set {api_key_env} or disable firecrawl_worker",
    )


def check_firecrawl_runtime() -> DoctorCheck:
    try:
        firecrawl = importlib.import_module("firecrawl")
    except Exception as error:
        return DoctorCheck(
            name="firecrawl_runtime",
            status="missing",
            detail=f"firecrawl import failed: {short_error(error)}",
            install_hint="uv sync --extra web",
        )

    if getattr(firecrawl, "Firecrawl", None) is None and getattr(firecrawl, "FirecrawlApp", None) is None:
        return DoctorCheck(
            name="firecrawl_runtime",
            status="missing",
            detail="firecrawl client class was not found",
            install_hint="Upgrade or reinstall firecrawl-py with `uv sync --extra web`",
        )

    return DoctorCheck(name="firecrawl_runtime", status="ok", detail="firecrawl client import succeeded")


def check_crawl4ai_runtime() -> DoctorCheck:
    try:
        crawl4ai = importlib.import_module("crawl4ai")
    except Exception as error:
        return DoctorCheck(
            name="crawl4ai_runtime",
            status="missing",
            detail=f"crawl4ai import failed: {short_error(error)}",
            install_hint="uv sync --extra web",
        )

    if getattr(crawl4ai, "AsyncWebCrawler", None) is None:
        return DoctorCheck(
            name="crawl4ai_runtime",
            status="missing",
            detail="crawl4ai.AsyncWebCrawler was not found",
            install_hint="Upgrade or reinstall crawl4ai with `uv sync --extra web`",
        )

    return DoctorCheck(name="crawl4ai_runtime", status="ok", detail="crawl4ai AsyncWebCrawler import succeeded")


def check_playwright_chromium() -> DoctorCheck:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as error:
        return DoctorCheck(
            name="playwright_chromium",
            status="missing",
            detail=short_error(error),
            install_hint="uv run playwright install chromium",
        )
    return DoctorCheck(name="playwright_chromium", status="ok", detail="Chromium launched successfully")


def check_playwright_profile_context() -> DoctorCheck:
    try:
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory(prefix="swallow-profile-doctor-") as profile_dir:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(user_data_dir=profile_dir, headless=True)
                try:
                    page = context.new_page()
                    page.goto("about:blank")
                finally:
                    context.close()
    except Exception as error:
        return DoctorCheck(
            name="playwright_profile_context",
            status="missing",
            detail=short_error(error),
            install_hint="uv run playwright install chromium",
        )
    return DoctorCheck(
        name="playwright_profile_context",
        status="ok",
        detail="Chromium persistent context launched successfully",
    )


def check_playwright_profile_dir(config: IngestConfig) -> DoctorCheck:
    params = config.worker_params("playwright_profile_worker")
    profile_dir = Path(str(params.get("profile_dir") or "~/.swallow/browser-profiles/chrome-default")).expanduser()
    if profile_dir.exists():
        return DoctorCheck(name="playwright_profile_dir", status="ok", detail=f"exists at {profile_dir}")
    return DoctorCheck(
        name="playwright_profile_dir",
        status="warning",
        detail=f"{profile_dir} does not exist yet; it will be created on first run",
        install_hint="Run a profile capture locally, then log in in the opened browser before relying on authenticated pages",
    )


def format_package_version(package_name: str) -> str:
    try:
        return f" ({version(package_name)})"
    except PackageNotFoundError:
        return ""
