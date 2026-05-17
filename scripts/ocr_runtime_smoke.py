from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from swallow.core.runner import IngestRunner

SMOKE_TEXT = "Swallow OCR Smoke Test 123"
REQUIRED_TOKENS = ("Swallow", "OCR", "123")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real PaddleOCR runtime smoke test.")
    parser.add_argument("--store", type=Path, help="Store root. Defaults to a temporary directory.")
    args = parser.parse_args()

    store = args.store or Path(tempfile.mkdtemp(prefix="swallow-ocr-smoke-"))
    store.mkdir(parents=True, exist_ok=True)

    image_path = store / "ocr-smoke.png"
    pdf_path = store / "ocr-smoke-scanned.pdf"
    create_text_image(image_path, SMOKE_TEXT)
    create_scanned_pdf(image_path, pdf_path)

    runner = IngestRunner(store_root=store)
    image_result = runner.ingest_file(image_path)
    pdf_result = runner.ingest_file(pdf_path)

    assert_ocr_result("image", image_result.document.content.markdown)
    assert_ocr_result("scanned_pdf", pdf_result.document.content.markdown)

    print(f"Store: {store}")
    print(f"Image job: {image_result.job.id}")
    print(f"Image document: {store / image_result.document_path}")
    print(f"Scanned PDF job: {pdf_result.job.id}")
    print(f"Scanned PDF document: {store / pdf_result.document_path}")
    print("OCR runtime smoke: success")
    return 0


def create_text_image(path: Path, text: str) -> None:
    image = Image.new("RGB", (1200, 280), "white")
    draw = ImageDraw.Draw(image)
    font = load_font(58)
    draw.text((48, 92), text, fill="black", font=font)
    image.save(path)


def create_scanned_pdf(image_path: Path, pdf_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    image.save(pdf_path, "PDF", resolution=200.0)


def load_font(size: int):
    for candidate in (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def assert_ocr_result(label: str, markdown: str) -> None:
    compact = markdown.replace(" ", "")
    missing = [token for token in REQUIRED_TOKENS if token.replace(" ", "") not in compact]
    if missing:
        raise AssertionError(f"{label} OCR missing tokens {missing}. Markdown was:\n{markdown}")


if __name__ == "__main__":
    raise SystemExit(main())
