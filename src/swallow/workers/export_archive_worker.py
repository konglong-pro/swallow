from __future__ import annotations

import json
import posixpath
import zipfile
from pathlib import Path
from typing import Any

from swallow.core.errors import SecurityError, error_to_dict
from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.normalizers.conversation_to_markdown import archive_conversations_to_markdown, stringify_content
from swallow.workers.base import BaseWorker

ZIP_SLIP_BLOCKED = "ZIP_SLIP_BLOCKED"


class ExportArchiveWorker(BaseWorker):
    name = "export_archive_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=["application/zip", "application/x-zip-compressed"],
        source_types=["export_archive"],
        strengths=["batch_history_import", "chatgpt_export", "conversation_markdown"],
        cost_level="low",
        requires_gpu=False,
        requires_network=False,
        supports_batch=True,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "export_archive"

    def run(self, input: WorkerInput) -> WorkerResult:
        try:
            archive_type = detect_archive_type(input.input_path)
            if archive_type != "chatgpt_export":
                return WorkerResult(
                    status="failed",
                    worker_name=self.name,
                    worker_version=self.version,
                    errors=[f"unsupported_archive_type: {archive_type}"],
                )
            conversations_payload = read_chatgpt_conversations_payload(input.input_path)
            conversations = parse_chatgpt_export(conversations_payload)
        except SecurityError as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"zip_slip_blocked: {error}"],
                metadata={
                    "error_code": error.code,
                    "security_error": error_to_dict(error),
                },
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"export_archive_parse_failed: {type(error).__name__}: {error}"],
            )

        markdown = archive_conversations_to_markdown(conversations, title="ChatGPT Export")
        artifacts: list[dict[str, Any]] = []
        job_dir = get_job_dir(input)
        if job_dir is not None:
            conversations_path = job_dir / "intermediate" / "export_archive" / "conversations.json"
            conversations_path.parent.mkdir(parents=True, exist_ok=True)
            conversations_path.write_text(json.dumps(conversations_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            artifacts.append({"type": "chatgpt_conversations_json", "path": conversations_path.relative_to(job_dir).as_posix()})

        record_count = len(conversations)
        message_count = sum(len(conversation.get("messages", [])) for conversation in conversations)
        warnings: list[str] = []
        if record_count == 0:
            warnings.append("archive_no_conversations")
        if message_count == 0:
            warnings.append("archive_no_messages")

        return WorkerResult(
            status="success" if message_count else "partial",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title="ChatGPT Export",
            artifacts=artifacts,
            metadata={
                "archive_type": "chatgpt_export",
                "record_count": record_count,
                "message_count": message_count,
            },
            warnings=warnings,
        )


def detect_archive_type(path: str | Path) -> str:
    archive_path = Path(path)
    if not zipfile.is_zipfile(archive_path):
        return "unsupported"
    with zipfile.ZipFile(archive_path) as archive:
        validate_archive_members_safe(archive)
        names = set(archive.namelist())
    if "conversations.json" in names or any(name.endswith("/conversations.json") for name in names):
        return "chatgpt_export"
    return "unsupported"


def read_chatgpt_conversations_payload(path: str | Path) -> Any:
    with zipfile.ZipFile(path) as archive:
        validate_archive_members_safe(archive)
        name = find_archive_member(archive, "conversations.json")
        with archive.open(name) as file:
            return json.loads(file.read().decode("utf-8"))


def find_archive_member(archive: zipfile.ZipFile, filename: str) -> str:
    for name in archive.namelist():
        if name == filename or name.endswith(f"/{filename}"):
            return name
    raise FileNotFoundError(filename)


def validate_archive_members_safe(archive: zipfile.ZipFile) -> None:
    for info in archive.infolist():
        name = info.filename
        if is_unsafe_archive_member(name):
            raise SecurityError(
                f"Blocked unsafe archive member path: {name}",
                code=ZIP_SLIP_BLOCKED,
                retryable=False,
                fallback_allowed=False,
            )


def is_unsafe_archive_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return True
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        return True
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return True
    collapsed = posixpath.normpath(normalized)
    return collapsed == ".." or collapsed.startswith("../")


def parse_chatgpt_export(payload: Any) -> list[dict[str, Any]]:
    conversations_payload = payload.get("conversations") if isinstance(payload, dict) else payload
    if not isinstance(conversations_payload, list):
        raise ValueError("ChatGPT conversations payload must be a list")

    conversations: list[dict[str, Any]] = []
    for index, raw_conversation in enumerate(conversations_payload, start=1):
        if not isinstance(raw_conversation, dict):
            continue
        messages = extract_chatgpt_messages(raw_conversation)
        conversations.append(
            {
                "title": raw_conversation.get("title") or f"Conversation {index}",
                "created_at": raw_conversation.get("create_time"),
                "updated_at": raw_conversation.get("update_time"),
                "messages": messages,
            }
        )
    return conversations


def extract_chatgpt_messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    direct_messages = conversation.get("messages")
    if isinstance(direct_messages, list):
        return [normalize_message(message) for message in direct_messages if isinstance(message, dict)]

    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []

    indexed_messages: list[tuple[float, int, dict[str, Any]]] = []
    for index, node in enumerate(mapping.values()):
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        normalized = normalize_message(message)
        if not normalized["content"]:
            continue
        sort_time = message.get("create_time")
        indexed_messages.append((float(sort_time) if isinstance(sort_time, (int, float)) else float(index), index, normalized))

    indexed_messages.sort(key=lambda item: (item[0], item[1]))
    return [message for _, _, message in indexed_messages]


def normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    author = message.get("author")
    role = author.get("role") if isinstance(author, dict) else message.get("role")
    content = message.get("content")
    return {
        "role": role or "unknown",
        "content": extract_message_content(content if content is not None else message.get("text")),
    }


def extract_message_content(content: Any) -> str:
    if isinstance(content, dict):
        if content.get("content_type") == "text" and isinstance(content.get("parts"), list):
            return stringify_content(content["parts"])
        if isinstance(content.get("parts"), list):
            return stringify_content(content["parts"])
        if isinstance(content.get("text"), str):
            return content["text"]
    return stringify_content(content)


def get_job_dir(input: WorkerInput) -> Path | None:
    value = input.metadata.get("job_dir")
    if not value:
        return None
    return Path(value)
