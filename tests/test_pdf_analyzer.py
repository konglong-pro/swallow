from __future__ import annotations

import pytest

from swallow.detectors.pdf_analyzer import analyze_pdf


def test_pdf_analyzer_detects_extractable_text_pdf(tmp_path):
    pytest.importorskip("pdfminer")
    source = tmp_path / "text.pdf"
    create_simple_pdf(source, "Extractable PDF text. " * 30)

    analysis = analyze_pdf(source)

    assert analysis.page_count == 1
    assert analysis.extracted_text_chars > 300
    assert analysis.chars_per_page > 300
    assert analysis.text_density >= 0.15
    assert analysis.is_scanned is False


def test_pdf_analyzer_detects_scanned_like_pdf_without_text(tmp_path):
    source = tmp_path / "empty.pdf"
    create_simple_pdf(source, "")

    analysis = analyze_pdf(source)

    assert analysis.page_count == 1
    assert analysis.extracted_text_chars == 0
    assert analysis.is_scanned is True


def create_simple_pdf(path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")

    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    path.write_bytes(content)
