from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

MB = 1024 * 1024


class WorkerSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    version: str | None = None
    timeout_seconds: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    def effective_params(self) -> dict[str, Any]:
        values = dict(self.params)
        if self.model_extra:
            values.update(self.model_extra)
        return values


class ResourceLimits(BaseModel):
    max_file_size_bytes: int = 100 * MB
    max_pdf_size_bytes: int = 300 * MB
    max_audio_video_size_bytes: int = 2 * 1024 * MB
    max_html_size_bytes: int = 50 * MB


class IngestConfig(BaseModel):
    workers: dict[str, WorkerSettings] = Field(default_factory=lambda: default_worker_settings())
    limits: ResourceLimits = Field(default_factory=ResourceLimits)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> IngestConfig:
        merged: dict[str, Any] = {"workers": default_worker_settings(), "limits": ResourceLimits()}
        if data:
            for key, value in data.items():
                if key != "workers":
                    merged[key] = value
            user_workers = data.get("workers")
            if isinstance(user_workers, dict):
                for worker_name, worker_settings in user_workers.items():
                    base = merged["workers"].get(worker_name, WorkerSettings())
                    base_data = base.model_dump(mode="python")
                    if isinstance(worker_settings, dict):
                        base_data.update(worker_settings)
                    merged["workers"][worker_name] = base_data
        return cls.model_validate(merged)

    def worker_settings(self, worker_name: str) -> WorkerSettings:
        key = worker_config_key(worker_name)
        return self.workers.get(key, WorkerSettings())

    def worker_enabled(self, worker_name: str) -> bool:
        return self.worker_settings(worker_name).enabled

    def worker_params(self, worker_name: str) -> dict[str, Any]:
        settings = self.worker_settings(worker_name)
        params = settings.effective_params()
        if settings.timeout_seconds is not None:
            params.setdefault("timeout_seconds", settings.timeout_seconds)
        return params


def load_config(path: str | Path | None = None) -> IngestConfig:
    resolved_path = resolve_config_path(path)
    if resolved_path is None:
        return IngestConfig()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Config file does not exist: {resolved_path}")
    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Config file must contain a YAML mapping")
    return IngestConfig.from_mapping(payload)


def resolve_config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        return Path(path)
    env_path = os.getenv("SWALLOW_CONFIG")
    if env_path:
        return Path(env_path)
    default_path = Path("swallow.config.yaml")
    if default_path.exists():
        return default_path
    return None


def worker_config_key(worker_name: str) -> str:
    return WORKER_NAME_TO_CONFIG_KEY.get(worker_name, worker_name.removesuffix("_worker"))


def default_worker_settings() -> dict[str, WorkerSettings]:
    return {
        name: WorkerSettings(**settings)
        for name, settings in {
            "plain_text": {"enabled": True, "timeout_seconds": 30},
            "markitdown": {"enabled": True, "timeout_seconds": 120},
            "paddleocr": {"enabled": True, "timeout_seconds": 600, "dpi": 200},
            "faster_whisper": {
                "enabled": True,
                "timeout_seconds": 1800,
                "model": "large-v3",
                "device": "auto",
                "compute_type": "default",
            },
            "firecrawl": {"enabled": True, "timeout_seconds": 120},
            "crawl4ai": {"enabled": True, "timeout_seconds": 180},
            "playwright": {"enabled": True, "timeout_seconds": 240, "headless": True, "screenshot": True},
            "playwright_profile": {
                "enabled": True,
                "timeout_seconds": 240,
                "headless": False,
                "screenshot": True,
                "profile_dir": "~/.swallow/browser-profiles/chrome-default",
            },
            "browser_capture": {"enabled": True, "timeout_seconds": 30},
            "export_archive": {"enabled": True, "timeout_seconds": 120},
        }.items()
    }


WORKER_NAME_TO_CONFIG_KEY = {
    "plain_text_worker": "plain_text",
    "markitdown_worker": "markitdown",
    "paddleocr_worker": "paddleocr",
    "faster_whisper_worker": "faster_whisper",
    "firecrawl_worker": "firecrawl",
    "crawl4ai_worker": "crawl4ai",
    "playwright_worker": "playwright",
    "playwright_profile_worker": "playwright_profile",
    "browser_capture_worker": "browser_capture",
    "export_archive_worker": "export_archive",
}
