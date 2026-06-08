from __future__ import annotations

from swallow.core.config import IngestConfig
from swallow.core.models import WorkerCapability
from swallow.workers.base import BaseWorker
from swallow.workers.browser_capture_worker import BrowserCaptureWorker
from swallow.workers.conversation_share_worker import (
    ChatGPTShareWorker,
    ClaudeShareWorker,
    DeepSeekShareWorker,
    GeminiShareWorker,
)
from swallow.workers.crawl4ai_worker import Crawl4AIWorker
from swallow.workers.export_archive_worker import ExportArchiveWorker
from swallow.workers.faster_whisper_worker import FasterWhisperWorker
from swallow.workers.firecrawl_worker import FirecrawlWorker
from swallow.workers.markitdown_worker import MarkItDownWorker
from swallow.workers.paddleocr_worker import PaddleOCRWorker
from swallow.workers.platform_article_worker import WeChatArticleWorker
from swallow.workers.playwright_profile_worker import PlaywrightProfileWorker
from swallow.workers.playwright_worker import PlaywrightWorker
from swallow.workers.plain_text_worker import PlainTextWorker
from swallow.workers.youtube_asr_worker import YouTubeASRWorker
from swallow.workers.youtube_transcript_worker import YouTubeTranscriptWorker


class WorkerRegistry:
    def __init__(self) -> None:
        self.workers: dict[str, BaseWorker] = {}

    def register(self, worker: BaseWorker) -> None:
        self.workers[worker.name] = worker

    def get(self, name: str) -> BaseWorker:
        return self.workers[name]

    def has(self, name: str) -> bool:
        return name in self.workers

    def list(self) -> list[BaseWorker]:
        return list(self.workers.values())

    def describe(self) -> list[dict]:
        return [describe_worker(worker) for worker in self.list()]


def default_registry(config: IngestConfig | None = None) -> WorkerRegistry:
    config = config or IngestConfig()
    registry = WorkerRegistry()
    register_if_enabled(registry, PlainTextWorker(), config)
    register_if_enabled(registry, MarkItDownWorker(), config)
    register_if_enabled(registry, PaddleOCRWorker(), config)
    register_if_enabled(registry, FasterWhisperWorker(), config)
    register_if_enabled(registry, FirecrawlWorker(), config)
    register_if_enabled(registry, Crawl4AIWorker(), config)
    register_if_enabled(registry, PlaywrightWorker(), config)
    register_if_enabled(registry, PlaywrightProfileWorker(), config)
    register_if_enabled(registry, ChatGPTShareWorker(), config)
    register_if_enabled(registry, GeminiShareWorker(), config)
    register_if_enabled(registry, ClaudeShareWorker(), config)
    register_if_enabled(registry, DeepSeekShareWorker(), config)
    register_if_enabled(registry, WeChatArticleWorker(), config)
    register_if_enabled(registry, YouTubeTranscriptWorker(), config)
    register_if_enabled(registry, YouTubeASRWorker(), config)
    register_if_enabled(registry, BrowserCaptureWorker(), config)
    register_if_enabled(registry, ExportArchiveWorker(), config)
    return registry


def register_if_enabled(registry: WorkerRegistry, worker: BaseWorker, config: IngestConfig) -> None:
    if config.worker_enabled(worker.name):
        registry.register(worker)


def describe_worker(worker: BaseWorker) -> dict:
    capability = getattr(worker, "capability", WorkerCapability())
    return {
        "name": worker.name,
        "version": worker.version,
        "capability": capability.model_dump(mode="json"),
    }
