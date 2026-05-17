from __future__ import annotations

MARKDOWN_NORMALIZER_NAME = "markdown_normalizer"
MARKDOWN_NORMALIZER_VERSION = "0.1.0"


def normalize_markdown_body(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    normalized = limit_consecutive_blank_lines(normalized, max_blank_lines=2)
    return normalized.rstrip() + "\n"


def limit_consecutive_blank_lines(text: str, *, max_blank_lines: int) -> str:
    lines = text.split("\n")
    output: list[str] = []
    blank_count = 0

    for line in lines:
        if line.strip():
            blank_count = 0
            output.append(line)
            continue

        blank_count += 1
        if blank_count <= max_blank_lines:
            output.append(line)

    return "\n".join(output)
