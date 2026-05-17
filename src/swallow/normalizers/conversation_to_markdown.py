from __future__ import annotations

import json
from typing import Any


def browser_capture_to_markdown(payload: dict[str, Any]) -> str:
    title = str(payload.get("title") or "Browser Capture").strip() or "Browser Capture"
    chunks = [f"# {title}"]

    meta_lines = []
    for key in ("platform", "url", "captured_at"):
        value = payload.get(key)
        if value:
            meta_lines.append(f"{key}: {value}")
    if meta_lines:
        chunks.append("<!-- browser_capture\n" + "\n".join(meta_lines) + "\n-->")

    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = []

    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        role = normalize_role(message.get("role"))
        content = stringify_content(message.get("content")).strip()
        if not content:
            continue
        chunks.append(f"## {index}. {role}")
        chunks.append(content)

    return "\n\n".join(chunks).strip() + "\n"


def archive_conversations_to_markdown(conversations: list[dict[str, Any]], *, title: str = "ChatGPT Export") -> str:
    chunks = [f"# {title}"]

    for index, conversation in enumerate(conversations, start=1):
        conversation_title = str(conversation.get("title") or f"Conversation {index}").strip()
        chunks.append(f"## Conversation {index}: {conversation_title}")

        created_at = conversation.get("created_at")
        updated_at = conversation.get("updated_at")
        meta_lines = []
        if created_at is not None:
            meta_lines.append(f"created_at: {created_at}")
        if updated_at is not None:
            meta_lines.append(f"updated_at: {updated_at}")
        if meta_lines:
            chunks.append("<!-- archive:conversation\n" + "\n".join(meta_lines) + "\n-->")

        messages = conversation.get("messages")
        if not isinstance(messages, list):
            continue
        for message_index, message in enumerate(messages, start=1):
            if not isinstance(message, dict):
                continue
            role = normalize_role(message.get("role"))
            content = stringify_content(message.get("content")).strip()
            if not content:
                continue
            chunks.append(f"### {message_index}. {role}")
            chunks.append(content)

    return "\n\n".join(chunks).strip() + "\n"


def normalize_role(role: Any) -> str:
    value = str(role or "unknown").strip().lower()
    return value or "unknown"


def stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [stringify_content(item).strip() for item in content]
        return "\n\n".join(part for part in parts if part)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("parts"), list):
            return stringify_content(content["parts"])
        return json.dumps(content, ensure_ascii=False, indent=2)
    return str(content)
