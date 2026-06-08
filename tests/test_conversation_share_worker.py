from __future__ import annotations

import json
from pathlib import Path

from swallow.core.config import IngestConfig
from swallow.core.models import WorkerInput
from swallow.core.runner import IngestRunner
from swallow.workers import conversation_share_worker as module
from swallow.workers.conversation_share_worker import ChatGPTShareWorker, ClaudeShareWorker, DeepSeekShareWorker, GeminiShareWorker
from swallow.workers.platform_common import PlatformPage

FIXTURES = Path(__file__).parent / "fixtures" / "web"


def make_input(url: str, job_dir: Path) -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(job_dir / "url.json"),
        mime_type="application/json",
        source_type="url",
        source_url=url,
        metadata={"job_dir": str(job_dir), "original_filename": "share.url"},
    )


def stub_render(monkeypatch, html: str, *, title: str, status_code: int = 200) -> None:
    def fake_render(url, **_):
        return PlatformPage(html=html, title=title, final_url=url, status_code=status_code)

    monkeypatch.setattr(module, "render_platform_page", fake_render)
    monkeypatch.setattr(module, "render_platform_page_dom_ready", fake_render)


def test_chatgpt_share_worker_extracts_messages_and_artifact(monkeypatch, tmp_path):
    html = (FIXTURES / "chatgpt_share_success.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="ChatGPT", final_url=url, status_code=200),
    )

    result = ChatGPTShareWorker().run(make_input("https://chatgpt.com/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["platform"] == "chatgpt"
    assert result.metadata["url_kind"] == "chatgpt_share"
    assert result.metadata["message_count"] == 2
    assert "How should platform URL ingest work?" in result.markdown
    extraction = json.loads((tmp_path / "job" / "intermediate" / "chatgpt" / "platform_extraction.json").read_text(encoding="utf-8"))
    assert extraction["schema_version"] == "platform_extraction.v1"
    assert extraction["content"]["message_count"] == 2


def test_chatgpt_share_worker_extracts_mapping_hydration(monkeypatch, tmp_path):
    html = (FIXTURES / "chatgpt_share_mapping.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="ChatGPT", final_url=url, status_code=200),
    )

    result = ChatGPTShareWorker().run(make_input("https://chatgpt.com/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["message_count"] == 2
    assert "Extract this mapping message." in result.markdown
    assert "Mapping extraction keeps order and roles." in result.markdown


def test_gemini_share_worker_extracts_messages(monkeypatch, tmp_path):
    html = (FIXTURES / "gemini_share_success.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Gemini", final_url=url, status_code=200),
    )

    result = GeminiShareWorker().run(make_input("https://gemini.google.com/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["platform"] == "gemini"
    assert result.metadata["message_count"] == 2
    assert "Architecture Decision Record" in result.markdown


def test_claude_share_worker_extracts_dom_messages(monkeypatch, tmp_path):
    html = (FIXTURES / "claude_share_success.html").read_text(encoding="utf-8")
    stub_render(monkeypatch, html, title="Claude")

    result = ClaudeShareWorker().run(make_input("https://claude.ai/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["platform"] == "claude"
    assert result.metadata["message_count"] == 2
    assert "Use deterministic fixture HTML" in result.markdown


def test_claude_share_worker_extracts_visible_snapshot_text(monkeypatch, tmp_path):
    html = (FIXTURES / "claude_share_visible_text.html").read_text(encoding="utf-8")
    stub_render(monkeypatch, html, title="Claude")

    result = ClaudeShareWorker().run(make_input("https://claude.ai/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["message_count"] == 2
    assert "To evolve, you must first learn to bury the one you were." in result.markdown
    assert "Ask Claude your own question" not in result.markdown


def test_deepseek_share_worker_extracts_structured_messages(monkeypatch, tmp_path):
    html = (FIXTURES / "deepseek_share_success.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="DeepSeek", final_url=url, status_code=200),
    )

    result = DeepSeekShareWorker().run(make_input("https://chat.deepseek.com/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["platform"] == "deepseek"
    assert result.metadata["message_count"] == 2
    assert "traceable Markdown candidate" in result.markdown


def test_deepseek_share_worker_extracts_visible_snapshot_text(monkeypatch, tmp_path):
    html = (FIXTURES / "deepseek_share_visible_text.html").read_text(encoding="utf-8")
    stub_render(monkeypatch, html, title="DeepSeek")

    result = DeepSeekShareWorker().run(make_input("https://chat.deepseek.com/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["message_count"] == 2
    assert "二手 Mac mini 跑 agent" in result.markdown
    assert "内存优先" in result.markdown
    assert "和 DeepSeek 继续聊" not in result.markdown


def test_gemini_share_worker_extracts_visible_snapshot_text(monkeypatch, tmp_path):
    html = (FIXTURES / "gemini_share_visible_text.html").read_text(encoding="utf-8")
    stub_render(monkeypatch, html, title="Gemini")

    result = GeminiShareWorker().run(make_input("https://gemini.google.com/share/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.title == "Programing wiki"
    assert result.metadata["message_count"] == 2
    assert "Architecture Decision Record" in result.markdown
    assert "AliyunOSSAdapter" in result.markdown
    assert "Copy public link" not in result.markdown


def test_share_worker_fails_empty_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(
            html="<html><title>Empty</title><body><main></main></body></html>",
            title="Empty",
            final_url=url,
            status_code=200,
        ),
    )

    result = ChatGPTShareWorker().run(make_input("https://chatgpt.com/share/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_EMPTY_SHELL"
    assert "platform_empty_shell" in result.warnings


def test_share_worker_reports_auth_wall(monkeypatch, tmp_path):
    html = (FIXTURES / "chatgpt_share_login.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Sign in", final_url=url, status_code=200),
    )

    result = ChatGPTShareWorker().run(make_input("https://chatgpt.com/share/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_AUTH_REQUIRED"
    assert result.metadata["auth_mode"] == "required"
    assert result.metadata["page_state"] == "auth_required"
    assert "auth_wall_detected" in result.warnings


def test_share_worker_reports_deleted_share(monkeypatch, tmp_path):
    html = (FIXTURES / "gemini_share_deleted.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Deleted", final_url=url, status_code=200),
    )

    result = GeminiShareWorker().run(make_input("https://gemini.google.com/share/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_CONTENT_UNAVAILABLE"
    assert result.metadata["page_state"] == "deleted_or_unavailable"
    assert "platform_content_unavailable" in result.warnings


def test_share_worker_reports_empty_shell(monkeypatch, tmp_path):
    html = (FIXTURES / "claude_share_empty.html").read_text(encoding="utf-8")
    stub_render(monkeypatch, html, title="Claude")

    result = ClaudeShareWorker().run(make_input("https://claude.ai/share/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_EMPTY_SHELL"
    assert result.metadata["page_state"] == "empty_shell"
    assert "platform_empty_shell" in result.warnings


def test_share_worker_reports_rate_limit(monkeypatch, tmp_path):
    html = (FIXTURES / "deepseek_share_rate_limited.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Too many requests", final_url=url, status_code=429),
    )

    result = DeepSeekShareWorker().run(make_input("https://chat.deepseek.com/share/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_RATE_LIMITED"
    assert result.metadata["page_state"] == "rate_limited"
    assert "platform_rate_limited" in result.warnings


def test_gemini_share_ingest_end_to_end_writes_document_and_trace(monkeypatch, tmp_path):
    html = (FIXTURES / "gemini_share_success.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Gemini", final_url=url, status_code=200),
    )
    config = IngestConfig.from_mapping({"workers": {"gemini_share": {"timeout_seconds": None}}})

    result = IngestRunner(store_root=tmp_path / "store", config=config).ingest_url("https://gemini.google.com/share/abc")

    assert result.document.provenance.primary_worker == "gemini_share_worker"
    assert "gemini_share_worker@0.1.0" in result.document.provenance.worker_chain
    assert "What is an ADR?" in result.document.content.markdown
    assert (tmp_path / "store" / result.document_path).exists()
    trace = (tmp_path / "store" / result.trace_path).read_text(encoding="utf-8")
    assert '"event": "url_classified"' in trace
    assert '"kind": "gemini_share"' in trace
