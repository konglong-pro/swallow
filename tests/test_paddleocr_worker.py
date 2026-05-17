from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from swallow.core.models import WorkerInput
from swallow.core.runner import IngestRunner
from swallow.workers import paddleocr_worker as paddleocr_module
from swallow.workers.paddleocr_worker import PaddleOCRWorker, render_pdf_to_images


def make_input(
    path: str,
    job_dir: Path,
    mime_type: str | None = "image/png",
    *,
    metadata: dict[str, object] | None = None,
) -> WorkerInput:
    values: dict[str, object] = {"original_filename": Path(path).name, "job_dir": str(job_dir)}
    if metadata:
        values.update(metadata)
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=path,
        mime_type=mime_type,
        source_type="file",
        metadata=values,
    )


def test_paddleocr_worker_reports_missing_dependency(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    source = tmp_path / "sample.png"
    source.write_bytes(b"not a real png")

    result = PaddleOCRWorker().run(make_input(str(source), tmp_path / "job"))

    assert result.status == "failed"
    assert result.errors == ["missing_optional_dependency: install with `uv sync --extra ocr`"]


def test_paddleocr_worker_returns_markdown_and_artifacts_for_image(monkeypatch, tmp_path):
    install_fake_paddleocr(monkeypatch, text="Image OCR text", confidence=0.91)
    source = tmp_path / "sample.png"
    source.write_bytes(b"not a real png")
    job_dir = tmp_path / "job"

    result = PaddleOCRWorker().run(make_input(str(source), job_dir))

    assert result.status == "success"
    assert result.worker_name == "paddleocr_worker"
    assert result.confidence == 0.91
    assert "Image OCR text" in result.markdown
    assert result.artifacts == [{"type": "ocr_json", "path": "intermediate/paddleocr/result.json"}]
    assert (job_dir / "intermediate" / "paddleocr" / "result.json").exists()


def test_paddleocr_worker_uses_configured_pdf_dpi(monkeypatch, tmp_path):
    install_fake_paddleocr(monkeypatch, text="PDF OCR text", confidence=0.9)
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.4 fake")
    job_dir = tmp_path / "job"
    seen: dict[str, object] = {}

    def fake_render(pdf_path: str | Path, out_dir: str | Path, *, dpi: int = 200) -> list[Path]:
        seen["pdf_path"] = str(pdf_path)
        seen["dpi"] = dpi
        page = Path(out_dir) / "page_0001.png"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(b"fake image")
        return [page]

    monkeypatch.setattr(paddleocr_module, "render_pdf_to_images", fake_render)

    result = PaddleOCRWorker().run(
        make_input(str(source), job_dir, mime_type="application/pdf", metadata={"dpi": 144})
    )

    assert result.status == "success"
    assert seen == {"pdf_path": str(source), "dpi": 144}
    assert result.metadata["dpi"] == 144
    assert result.artifacts == [
        {"type": "ocr_json", "path": "intermediate/paddleocr/result.json"},
        {"type": "page_image_dir", "path": "intermediate/pages"},
    ]


def test_render_pdf_to_images_creates_page_png(tmp_path):
    pytest.importorskip("pypdfium2")
    source = tmp_path / "sample.pdf"
    create_simple_pdf(source, "Render me")

    image_paths = render_pdf_to_images(source, tmp_path / "pages", dpi=72)

    assert len(image_paths) == 1
    assert image_paths[0].name == "page_0001.png"
    assert image_paths[0].exists()


def test_low_text_pdf_ingests_with_mocked_paddleocr(monkeypatch, tmp_path):
    pytest.importorskip("pypdfium2")
    install_fake_paddleocr(monkeypatch, text="Scanned PDF OCR text from fake engine", confidence=0.88)
    source = tmp_path / "scanned.pdf"
    create_simple_pdf(source, "")

    result = IngestRunner(store_root=tmp_path / "store").ingest_file(source)

    assert result.document.provenance.primary_worker == "paddleocr_worker"
    assert "paddleocr_worker@0.1.0" in result.document.provenance.worker_chain
    assert "Scanned PDF OCR text from fake engine" in result.document.content.markdown
    assert "<!-- page: 1 -->" in result.document.content.markdown
    assert any(artifact["type"] == "page_image_dir" for artifact in result.document.provenance.artifacts)


def install_fake_paddleocr(monkeypatch, *, text: str, confidence: float) -> None:
    class FakePaddleOCR:
        def ocr(self, image_path: str, cls: bool = True):
            return [[[[0, 0], [1, 0], [1, 1], [0, 1]], (text, confidence)]]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddleOCR))


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
