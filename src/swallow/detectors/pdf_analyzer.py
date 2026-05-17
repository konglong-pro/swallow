from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field


class PdfAnalysis(BaseModel):
    page_count: int
    extracted_text_chars: int
    chars_per_page: float
    text_density: float
    is_scanned: bool
    has_many_images: bool
    image_count: int
    warnings: list[str] = Field(default_factory=list)


def analyze_pdf(path: str | Path) -> PdfAnalysis:
    pdf_path = Path(path)
    warnings: list[str] = []
    page_count = get_pdf_page_count(pdf_path, warnings)
    text = extract_pdf_text(pdf_path, warnings)
    text_chars = len(text.strip())
    image_count = count_pdf_images(pdf_path, warnings)
    chars_per_page = round(text_chars / max(page_count, 1), 2)
    text_density = round(min(chars_per_page / 2000, 1.0), 4)
    is_scanned = page_count > 0 and text_chars == 0
    has_many_images = image_count >= max(page_count * 2, 3)

    return PdfAnalysis(
        page_count=page_count,
        extracted_text_chars=text_chars,
        chars_per_page=chars_per_page,
        text_density=text_density,
        is_scanned=is_scanned,
        has_many_images=has_many_images,
        image_count=image_count,
        warnings=warnings,
    )


def get_pdf_page_count(path: Path, warnings: list[str]) -> int:
    try:
        import pypdfium2

        document = pypdfium2.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()
    except ImportError:
        warnings.append("missing_optional_dependency:pypdfium2")
    except Exception as error:
        warnings.append(f"pdf_page_count_failed:{type(error).__name__}")

    try:
        data = path.read_bytes()
    except Exception as error:
        warnings.append(f"pdf_read_failed:{type(error).__name__}")
        return 0

    return len(re.findall(rb"/Type\s*/Page\b", data))


def extract_pdf_text(path: Path, warnings: list[str]) -> str:
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        warnings.append("missing_optional_dependency:pdfminer")
        return ""

    try:
        return extract_text(str(path)) or ""
    except Exception as error:
        warnings.append(f"pdf_text_extract_failed:{type(error).__name__}")
        return ""


def count_pdf_images(path: Path, warnings: list[str]) -> int:
    try:
        data = path.read_bytes()
    except Exception as error:
        warnings.append(f"pdf_image_count_failed:{type(error).__name__}")
        return 0

    return len(re.findall(rb"/Subtype\s*/Image\b", data))
