from __future__ import annotations

from pathlib import Path

PLAIN_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
PDF_MIME_TYPE = "application/pdf"
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
IMAGE_MIME_TYPES = {
    "image/bmp",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}
AUDIO_VIDEO_EXTENSIONS = {".flac", ".m4a", ".mov", ".mp3", ".mp4", ".wav", ".webm"}
AUDIO_VIDEO_MIME_TYPES = {
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/webm",
    "video/mp4",
    "video/quicktime",
    "video/webm",
}

MARKITDOWN_EXTENSIONS = {
    ".csv",
    ".docx",
    ".epub",
    ".htm",
    ".html",
    ".json",
    ".pdf",
    ".pptx",
    ".tsv",
    ".xlsx",
    ".xml",
}

OFFICE_MIME_TYPES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

MARKITDOWN_MIME_TYPES = OFFICE_MIME_TYPES | {
    "application/epub+zip",
    "application/json",
    "application/pdf",
    "application/xml",
    "text/csv",
    "text/html",
    "text/tab-separated-values",
    "text/xml",
}


def is_plain_text_file(path: str | Path, mime_type: str | None) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in PLAIN_TEXT_EXTENSIONS:
        return True
    return bool(mime_type and mime_type.startswith("text/") and mime_type not in MARKITDOWN_MIME_TYPES)


def is_markitdown_candidate(path: str | Path, mime_type: str | None) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in MARKITDOWN_EXTENSIONS or bool(mime_type and mime_type in MARKITDOWN_MIME_TYPES)


def is_pdf_file(path: str | Path, mime_type: str | None) -> bool:
    return Path(path).suffix.lower() == ".pdf" or mime_type == PDF_MIME_TYPE


def is_image_file(path: str | Path, mime_type: str | None) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS or bool(mime_type and mime_type in IMAGE_MIME_TYPES)


def is_audio_video_file(path: str | Path, mime_type: str | None) -> bool:
    return Path(path).suffix.lower() in AUDIO_VIDEO_EXTENSIONS or bool(
        mime_type and mime_type in AUDIO_VIDEO_MIME_TYPES
    )
