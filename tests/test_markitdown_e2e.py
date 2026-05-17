from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from swallow.core.runner import IngestRunner


def test_markitdown_ingests_real_docx(tmp_path):
    pytest.importorskip("markitdown")
    pytest.importorskip("mammoth")
    source = tmp_path / "sample.docx"
    create_docx(source, "Swallow DOCX fixture", "DOCX content routed through MarkItDown worker.")

    result = IngestRunner(store_root=tmp_path / "store").ingest_file(source)

    assert result.document.provenance.primary_worker == "markitdown_worker"
    assert "markitdown_worker@0.1.0" in result.document.provenance.worker_chain
    assert "Swallow DOCX fixture" in result.document.content.markdown
    assert "DOCX content routed through MarkItDown worker." in result.document.content.markdown


def test_markitdown_ingests_real_xlsx(tmp_path):
    pytest.importorskip("markitdown")
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "sample.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Ingest"
    sheet.append(["kind", "value"])
    sheet.append(["xlsx", "Swallow XLSX fixture"])
    workbook.save(source)

    result = IngestRunner(store_root=tmp_path / "store").ingest_file(source)

    assert result.document.provenance.primary_worker == "markitdown_worker"
    assert "Swallow XLSX fixture" in result.document.content.markdown


def test_markitdown_ingests_real_pdf(tmp_path):
    pytest.importorskip("markitdown")
    pytest.importorskip("pdfminer")
    source = tmp_path / "sample.pdf"
    text = "Swallow PDF fixture. " * 30
    create_simple_pdf(source, text)

    result = IngestRunner(store_root=tmp_path / "store").ingest_file(source)

    assert result.document.provenance.primary_worker == "markitdown_worker"
    assert "fallback:paddleocr_worker" not in result.document.provenance.worker_chain
    assert "Swallow PDF fixture" in result.document.content.markdown


def create_docx(path: Path, heading: str, paragraph: str) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{heading}</w:t></w:r></w:p>
    <w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        docx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        docx.writestr("word/document.xml", document_xml)


def create_simple_pdf(path: Path, text: str) -> None:
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
