from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.file_type import IMAGE_MIME_TYPES, PDF_MIME_TYPE, is_image_file, is_pdf_file
from swallow.workers.base import BaseWorker


class PaddleOCRWorker(BaseWorker):
    name = "paddleocr_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=sorted({PDF_MIME_TYPE, *IMAGE_MIME_TYPES}),
        source_types=["file"],
        strengths=["ocr", "scanned_pdf", "image_text", "chinese_documents"],
        cost_level="high",
        requires_gpu=False,
        requires_network=False,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "file" and (
            is_pdf_file(input.input_path, input.mime_type) or is_image_file(input.input_path, input.mime_type)
        )

    def run(self, input: WorkerInput) -> WorkerResult:
        job_dir = get_job_dir(input)
        if job_dir is None:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_worker_metadata: job_dir"],
            )

        try:
            from paddleocr import PaddleOCR
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra ocr`"],
                metadata={"engine": "paddleocr", "input_mime_type": input.mime_type},
            )

        engine_versions = get_engine_versions()
        intermediate_dir = job_dir / "intermediate"
        ocr_dir = intermediate_dir / "paddleocr"
        ocr_dir.mkdir(parents=True, exist_ok=True)

        artifacts: list[dict[str, Any]] = []
        warnings: list[str] = []
        dpi = to_positive_int(input.metadata.get("dpi"), default=200)

        try:
            if is_pdf_file(input.input_path, input.mime_type):
                pages_dir = intermediate_dir / "pages"
                image_paths = render_pdf_to_images(input.input_path, pages_dir, dpi=dpi)
                artifacts.append({"type": "page_image_dir", "path": relative_artifact_path(pages_dir, job_dir)})
            else:
                image_paths = [Path(input.input_path)]
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"pdf_render_failed: {type(error).__name__}: {error}"],
                metadata={"engine": "paddleocr", "input_mime_type": input.mime_type, "dpi": dpi, **engine_versions},
            )

        try:
            ocr_engine = create_paddleocr_engine(PaddleOCR)
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"paddleocr_init_failed: {type(error).__name__}: {error}"],
                metadata={"engine": "paddleocr", "input_mime_type": input.mime_type, "dpi": dpi, **engine_versions},
            )

        pages: list[OcrPage] = []
        for index, image_path in enumerate(image_paths, start=1):
            try:
                raw_result = run_ocr(ocr_engine, image_path)
            except Exception as error:
                warnings.append(f"page_{index}_ocr_failed:{type(error).__name__}:{short_error(error)}")
                raw_result = []
            entries = extract_ocr_entries(raw_result)
            pages.append(OcrPage(page_number=index, image_path=image_path, entries=entries, raw_result=to_jsonable(raw_result)))

        result_json_path = ocr_dir / "result.json"
        result_json_path.write_text(
            json.dumps([page.to_json() for page in pages], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts.insert(0, {"type": "ocr_json", "path": relative_artifact_path(result_json_path, job_dir)})

        markdown = render_ocr_markdown(input, pages)
        confidence = average_confidence(pages)
        if confidence is not None and confidence < 0.75:
            warnings.append("low_ocr_confidence")
        if len(markdown.strip()) < 100:
            warnings.append("ocr_output_too_short")

        text_found = any(entry.text.strip() for page in pages for entry in page.entries)
        return WorkerResult(
            status="success" if text_found else "failed",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown if text_found else None,
            title=Path(input.metadata.get("original_filename", input.input_path)).stem,
            artifacts=artifacts,
            metadata={
                "engine": "paddleocr",
                "pipeline": "paddleocr",
                "page_count": len(image_paths),
                "dpi": dpi if is_pdf_file(input.input_path, input.mime_type) else None,
                **engine_versions,
            },
            confidence=confidence,
            warnings=warnings,
            errors=[] if text_found else ["paddleocr produced empty markdown"],
        )


class OcrEntry:
    def __init__(self, text: str, confidence: float | None = None) -> None:
        self.text = text
        self.confidence = confidence

    def to_json(self) -> dict[str, Any]:
        return {"text": self.text, "confidence": self.confidence}


class OcrPage:
    def __init__(self, page_number: int, image_path: Path, entries: list[OcrEntry], raw_result: Any) -> None:
        self.page_number = page_number
        self.image_path = image_path
        self.entries = entries
        self.raw_result = raw_result

    def to_json(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "image_path": self.image_path.as_posix(),
            "entries": [entry.to_json() for entry in self.entries],
            "raw_result": self.raw_result,
        }


def get_job_dir(input: WorkerInput) -> Path | None:
    value = input.metadata.get("job_dir")
    if not value:
        return None
    return Path(value)


def render_pdf_to_images(pdf_path: str | Path, out_dir: str | Path, *, dpi: int = 200) -> list[Path]:
    try:
        import pypdfium2
    except ImportError as error:
        raise RuntimeError("missing_optional_dependency:pypdfium2") from error

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    document = pypdfium2.PdfDocument(str(pdf_path))
    scale = dpi / 72
    image_paths: list[Path] = []

    try:
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            image_path = out / f"page_{index + 1:04d}.png"
            image.save(image_path)
            image_paths.append(image_path)
            close_if_possible(image)
            close_if_possible(bitmap)
            close_if_possible(page)
    finally:
        close_if_possible(document)

    return image_paths


def run_ocr(ocr_engine: Any, image_path: Path) -> Any:
    try:
        return ocr_engine.ocr(str(image_path), cls=False)
    except TypeError:
        return ocr_engine.ocr(str(image_path))


def create_paddleocr_engine(paddleocr_cls: Any) -> Any:
    try:
        return paddleocr_cls(show_log=False, use_angle_cls=False)
    except TypeError:
        return paddleocr_cls()


def get_engine_versions() -> dict[str, str | None]:
    return {
        "engine_version": package_version("paddleocr"),
        "paddle_version": package_version("paddlepaddle"),
    }


def package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def short_error(error: Exception, *, max_length: int = 200) -> str:
    message = str(error).replace("\n", " ").strip()
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."


def extract_ocr_entries(raw_result: Any) -> list[OcrEntry]:
    entries: list[OcrEntry] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            texts = node.get("rec_texts")
            scores = node.get("rec_scores")
            if isinstance(texts, list):
                for index, text in enumerate(texts):
                    confidence = scores[index] if isinstance(scores, list) and index < len(scores) else None
                    add_entry(text, confidence)
                return
            if isinstance(node.get("text"), str):
                add_entry(node.get("text"), node.get("confidence") or node.get("score"))
                return
            for value in node.values():
                visit(value)
            return

        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and isinstance(node[1], (list, tuple)) and node[1] and isinstance(node[1][0], str):
                confidence = node[1][1] if len(node[1]) > 1 else None
                add_entry(node[1][0], confidence)
                return
            for value in node:
                visit(value)

    def add_entry(text: Any, confidence: Any) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        entries.append(OcrEntry(text=text.strip(), confidence=to_float_or_none(confidence)))

    visit(raw_result)
    return entries


def render_ocr_markdown(input: WorkerInput, pages: list[OcrPage]) -> str:
    title = Path(input.metadata.get("original_filename", input.input_path)).stem
    chunks = [f"# {title}"]
    for page in pages:
        chunks.append(f"<!-- page: {page.page_number} -->")
        chunks.append(f"## Page {page.page_number}")
        page_text = "\n\n".join(entry.text for entry in page.entries)
        chunks.append(page_text)
    return "\n\n".join(chunks).strip() + "\n"


def average_confidence(pages: list[OcrPage]) -> float | None:
    values = [entry.confidence for page in pages for entry in page.entries if entry.confidence is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def relative_artifact_path(path: Path, job_dir: Path) -> str:
    return path.relative_to(job_dir).as_posix()


def to_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def to_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, tuple):
            return [to_jsonable(item) for item in value]
        if isinstance(value, list):
            return [to_jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): to_jsonable(item) for key, item in value.items()}
        return str(value)


def close_if_possible(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()
