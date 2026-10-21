"""Original-quality region OCR in a bounded native child process.

Coordinates describe the EXIF-upright page AFTER clockwise rotation, matching
the visual selector. The preview JPEG is never the OCR input. Resource isolation
does not make the native decoder an OS sandbox for arbitrary executables.
"""

from __future__ import annotations

import io
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.services import filesec

WALL_SECONDS = 45
CPU_SECONDS = 30
OCR_SECONDS = 25
MAX_CROP_PIXELS = 12_000_000
MAX_SOURCE_PIXELS = 40_000_000
MAX_CROP_EDGE = 20_000
MAX_PAGE_EDGE = 100_000
DPI = 300

# A compromised/malformed native result must not turn its stderr or document
# text into the job's exception message. The parent accepts only known codes
# and supplies its own bounded explanations.
_CHILD_ERRORS = {
    "invalid_region": "Select an area inside the displayed page.",
    "invalid_page": "The selected page is not available for reading.",
    "region_too_large": "Select a smaller area and read it separately.",
    "unsupported_source": "Region reading supports PDF, PNG and JPEG originals.",
    "source_too_large": "The stored document exceeds the processing size limit.",
    "language_unavailable": "English OCR is not installed on this worker.",
    "region_output_too_large": "This area contains too much text. Select a smaller area.",
    "ocr_unavailable": "Region reading is not installed on this worker.",
    "ocr_resource_limit": "Region reading reached its processing limit. Select a smaller area.",
    "ocr_timeout": "Reading this area took too long. Select a smaller area and try again.",
    "ocr_failed": "This area could not be read safely. Try a clearer or smaller area.",
}


class RegionOcrError(ValueError):
    """A bounded, user-safe refusal; native stderr and document data stay private."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _invalid() -> RegionOcrError:
    return RegionOcrError("invalid_region", "Select an area inside the displayed page.")


def _request(value: dict) -> dict:
    if not isinstance(value, dict):
        raise _invalid()
    page = value.get("page")
    rotation = value.get("rotation", 0)
    language = value.get("language", "eng")
    if (
        type(page) is not int
        or not 1 <= page <= 50
        or type(rotation) is not int
        or rotation not in (0, 90, 180, 270)
        or language != "eng"
    ):
        raise _invalid()
    numbers = {}
    for key in ("x", "y", "width", "height"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _invalid()
        if not math.isfinite(raw) or not 0 <= raw <= 1:
            raise _invalid()
        numbers[key] = float(raw)
    if (
        numbers["width"] <= 0
        or numbers["height"] <= 0
        or numbers["x"] + numbers["width"] > 1 + 1e-8
        or numbers["y"] + numbers["height"] > 1 + 1e-8
    ):
        raise _invalid()
    return {"page": page, "rotation": rotation, "language": language, **numbers}


def _bounds(width: float, height: float, request: dict) -> tuple[int, int, int, int]:
    if not all(math.isfinite(v) and 0 < v <= MAX_PAGE_EDGE for v in (width, height)):
        raise RegionOcrError(
            "region_too_large", "This page exceeds the safe region-reading limits."
        )
    left = max(0, math.floor(request["x"] * width))
    top = max(0, math.floor(request["y"] * height))
    right = min(math.ceil(width), math.ceil((request["x"] + request["width"]) * width))
    bottom = min(math.ceil(height), math.ceil((request["y"] + request["height"]) * height))
    crop_width, crop_height = right - left, bottom - top
    if min(crop_width, crop_height) < 2:
        raise RegionOcrError("invalid_region", "Select a larger area so the text can be read.")
    if (
        crop_width > MAX_CROP_EDGE
        or crop_height > MAX_CROP_EDGE
        or crop_width * crop_height > MAX_CROP_PIXELS
    ):
        raise RegionOcrError("region_too_large", "Select a smaller area and read it separately.")
    return left, top, right, bottom


def _flatten(image):
    from PIL import Image

    rgba = image.convert("RGBA")
    try:
        background = Image.new("RGBA", rgba.size, "white")
        try:
            background.alpha_composite(rgba)
            return background.convert("RGB")
        finally:
            background.close()
    finally:
        rgba.close()


def _render_region(content: bytes, value: dict):
    """Return a detached RGB selection and honest source/render geometry."""
    from PIL import Image, ImageOps

    request = _request(value)
    rotation = request["rotation"]
    if b"%PDF" in content[:1024]:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(content) as document:
            if len(document) > 50 or request["page"] > len(document):
                raise RegionOcrError(
                    "invalid_page", "The selected page is not available for reading."
                )
            page = document[request["page"] - 1]
            try:
                source_width, source_height = page.get_width(), page.get_height()
                if not all(math.isfinite(v) and v > 0 for v in (source_width, source_height)):
                    raise _invalid()
                width, height = (
                    (source_height, source_width)
                    if rotation in (90, 270)
                    else (source_width, source_height)
                )
                scale = DPI / 72
                # PDFium crops AFTER rotation. Rasterise only this selected
                # rectangle at 300 DPI, avoiding an enormous full-page bitmap.
                left, top, right, bottom = _bounds(width * scale, height * scale, request)
                crop = (
                    left / scale,
                    max(0, height - bottom / scale),
                    max(0, width - right / scale),
                    top / scale,
                )
                bitmap = page.render(scale=scale, rotation=rotation, crop=crop)
                try:
                    image = bitmap.to_pil()
                    try:
                        if image.width * image.height > MAX_CROP_PIXELS:
                            raise RegionOcrError(
                                "region_too_large", "Select a smaller area and read it separately."
                            )
                        result = _flatten(image)
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        return result, {
            "source_width": source_width,
            "source_height": source_height,
            "source_unit": "pdf_points",
            "rotated_width": width,
            "rotated_height": height,
            "render_dpi": DPI,
        }
    if not (content.startswith(b"\x89PNG") or content.startswith(b"\xff\xd8")):
        raise RegionOcrError(
            "unsupported_source", "Region reading supports PDF, PNG and JPEG originals."
        )
    if request["page"] != 1:
        raise RegionOcrError("invalid_page", "This image has only one page.")
    with Image.open(io.BytesIO(content)) as source:
        if source.format not in ("PNG", "JPEG") or source.width * source.height > MAX_SOURCE_PIXELS:
            raise RegionOcrError(
                "region_too_large", "This image exceeds the safe region-reading limits."
            )
        oriented = ImageOps.exif_transpose(source)
        try:
            source_width, source_height = oriented.size
            rotated = oriented.rotate(-rotation, expand=True) if rotation else oriented
            try:
                width, height = rotated.size
                bounds = _bounds(width, height, request)
                selected = rotated.crop(bounds)
                try:
                    result = _flatten(selected)
                finally:
                    selected.close()
            finally:
                if rotated is not oriented:
                    rotated.close()
        finally:
            oriented.close()
    return result, {
        "source_width": source_width,
        "source_height": source_height,
        "source_unit": "pixels",
        "rotated_width": width,
        "rotated_height": height,
        "render_dpi": None,
    }


def _recognize(content: bytes, request: dict) -> dict:
    import pytesseract

    from app.core.config import settings
    from app.services.pdf_ocr import _reconstruct_lines

    try:
        installed = sorted(pytesseract.get_languages())
    except pytesseract.TesseractNotFoundError:
        raise RegionOcrError(
            "ocr_unavailable", "Region reading is not installed on this worker."
        ) from None
    except pytesseract.TesseractError:
        raise RegionOcrError(
            "ocr_failed", "The OCR worker could not load its language inventory."
        ) from None
    if len(installed) > 32 or any(
        not isinstance(name, str) or len(name) > 32 for name in installed
    ):
        raise RegionOcrError(
            "ocr_resource_limit", "The OCR worker language inventory exceeds its processing limit."
        )
    if request["language"] not in installed:
        raise RegionOcrError("language_unavailable", "English OCR is not installed on this worker.")
    image, geometry = _render_region(content, request)
    try:
        data = pytesseract.image_to_data(
            image,
            lang=request["language"],
            output_type=pytesseract.Output.DICT,
            config="--psm 6",
            timeout=min(OCR_SECONDS, settings.ocr_process_timeout_seconds),
        )
        text = _reconstruct_lines(data).strip()
        if len(text.encode("utf-8")) > filesec.RESOURCE_BYTES["region_ocr_text"]:
            raise RegionOcrError(
                "region_output_too_large",
                "This area contains too much text. Select a smaller area.",
            )
        return {
            "text": text,
            "engine": "tesseract",
            "engine_version": str(pytesseract.get_tesseract_version())[:120],
            "language": request["language"],
            "segmentation_mode": 6,
            "installed_languages": installed,
            "page": request["page"],
            "rotation": request["rotation"],
            "crop_width": image.width,
            "crop_height": image.height,
            **geometry,
        }
    except pytesseract.TesseractNotFoundError:
        raise RegionOcrError(
            "ocr_unavailable", "Region reading is not installed on this worker."
        ) from None
    except pytesseract.TesseractError:
        raise RegionOcrError(
            "ocr_failed", "The OCR worker could not read this area. Try a clearer selection."
        ) from None
    finally:
        image.close()


def _stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def recognize(content: bytes, value: dict) -> dict:
    """Run bounded OCR outside the worker process; never log document bytes."""
    request = _request(value)
    if len(content) > filesec.max_bytes():
        raise RegionOcrError(
            "source_too_large", "The stored document exceeds the processing size limit."
        )
    if not content:
        raise RegionOcrError("unsupported_source", "The original document is empty.")
    if sys.platform != "linux":
        raise RegionOcrError("ocr_unavailable", "Region reading requires a Linux OCR worker.")
    from app.core.config import settings

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "OMP_THREAD_LIMIT": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "MAX_UPLOAD_MB": str(settings.max_upload_mb),
        "OCR_PROCESS_TIMEOUT_SECONDS": str(settings.ocr_process_timeout_seconds),
    }
    with tempfile.TemporaryDirectory(prefix="invoice-region-") as directory:
        env["TMPDIR"] = directory
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
            source.write(content)
            source.seek(0)
            process = subprocess.Popen(
                [sys.executable, "-m", "app.services.region_ocr", json.dumps(request)],
                stdin=source,
                stdout=output,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env=env,
                start_new_session=True,
            )
            deadline = time.monotonic() + WALL_SECONDS
            try:
                while process.poll() is None:
                    if (
                        os.fstat(output.fileno()).st_size
                        > filesec.RESOURCE_BYTES["region_ocr_output"]
                    ):
                        raise RegionOcrError(
                            "region_output_too_large", "Region reading exceeded its output limit."
                        )
                    if time.monotonic() >= deadline:
                        raise RegionOcrError(
                            "ocr_timeout",
                            "Reading this area took too long. Select a smaller area and try again.",
                        )
                    time.sleep(0.03)
                output.seek(0)
                encoded = output.read(filesec.RESOURCE_BYTES["region_ocr_output"] + 1)
                if len(encoded) > filesec.RESOURCE_BYTES["region_ocr_output"]:
                    raise RegionOcrError(
                        "region_output_too_large", "Region reading exceeded its output limit."
                    )
                if process.returncode != 0:
                    resource_signal = process.returncode in (
                        -signal.SIGKILL,
                        -signal.SIGXCPU,
                        -signal.SIGXFSZ,
                    )
                    raise RegionOcrError(
                        "ocr_resource_limit" if resource_signal else "ocr_failed",
                        _CHILD_ERRORS["ocr_resource_limit" if resource_signal else "ocr_failed"],
                    )
                try:
                    response = json.loads(encoded)
                    if "error" in response:
                        code = response.get("code")
                        if not isinstance(code, str) or code not in _CHILD_ERRORS:
                            code = "ocr_failed"
                        raise RegionOcrError(code, _CHILD_ERRORS[code])
                    result = response["result"]
                    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                        raise ValueError("invalid result")
                    if (
                        len(result["text"].encode("utf-8"))
                        > filesec.RESOURCE_BYTES["region_ocr_text"]
                    ):
                        raise RegionOcrError(
                            "region_output_too_large", _CHILD_ERRORS["region_output_too_large"]
                        )
                    return result
                except (ValueError, KeyError, TypeError) as exc:
                    if isinstance(exc, RegionOcrError):
                        raise
                    raise RegionOcrError(
                        "ocr_failed",
                        "Region reading did not return a usable result. Try a clearer or smaller area.",
                    ) from None
            finally:
                _stop(process)


def _child(value: dict) -> None:
    import resource

    memory = filesec.RESOURCE_BYTES["region_ocr_memory"]
    temporary = filesec.RESOURCE_BYTES["region_ocr_temp_file"]
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (temporary, temporary))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    response: dict
    try:
        request = _request(value)
        content = sys.stdin.buffer.read(filesec.max_bytes() + 1)
        if len(content) > filesec.max_bytes():
            raise RegionOcrError(
                "source_too_large", "The stored document exceeds the processing size limit."
            )
        result = _recognize(content, request)
        response = {"result": result}
    except RegionOcrError as exc:
        response = {"code": exc.code, "error": exc.detail}
    except (ImportError, FileNotFoundError):
        response = {
            "code": "ocr_unavailable",
            "error": "Region reading is not installed on this worker.",
        }
    except MemoryError:
        response = {
            "code": "ocr_resource_limit",
            "error": "Region reading reached its memory limit. Select a smaller area.",
        }
    except RuntimeError:
        response = {
            "code": "ocr_timeout",
            "error": "Region reading could not finish within its processing budget. Try a smaller area.",
        }
    except Exception:
        response = {
            "code": "ocr_failed",
            "error": "This area could not be read safely. Try a clearer or smaller area.",
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    _child(json.loads(sys.argv[1]))
