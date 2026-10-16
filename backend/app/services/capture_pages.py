"""N4 isolated, bounded rendering; originals and extraction are authoritative."""

import base64
import io
import json
import math
import subprocess
import sys

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_run import ExtractionRun
from app.schemas.capture_pages import CapturePage, CapturePages
from app.services import documents, filesec

PREFIX = "capture-pages"
MAX_PAGES = 6
MAX_EDGE = 960
MAX_BYTES = filesec.RESOURCE_BYTES["capture_pages"]
MAX_PAGE_BYTES = filesec.RESOURCE_BYTES["capture_page"]
RENDER_SECONDS = 12


def unavailable(
    message="Page previews are unavailable; use the original document.",
) -> CapturePages:
    return CapturePages(status="unavailable", message=message)


def _encode(image, number: int) -> dict:
    from PIL import Image

    image.thumbnail((MAX_EDGE, MAX_EDGE))
    # Composite transparency onto white, discard EXIF and all source metadata.
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    try:
        background.alpha_composite(rgba)
        rgb = background.convert("RGB")
        try:
            output = io.BytesIO()
            rgb.save(output, format="JPEG", quality=75)
            jpeg_bytes = output.getvalue()
            if len(jpeg_bytes) > MAX_PAGE_BYTES:
                raise ValueError("preview byte cap")
            return {
                "number": number,
                "width": rgb.width,
                "height": rgb.height,
                "data": base64.b64encode(jpeg_bytes).decode("ascii"),
            }
        finally:
            rgb.close()
    finally:
        rgba.close()
        background.close()


def _render(content: bytes) -> dict:
    from PIL import Image, ImageOps

    pages = []
    used = 0
    if b"%PDF" in content[:1024]:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(content) as pdf:
            total = len(pdf)
            for index in range(min(total, MAX_PAGES)):
                page = pdf[index]
                try:
                    edge = max(page.get_width(), page.get_height())
                    if not math.isfinite(edge) or edge <= 0:
                        raise ValueError("invalid page geometry")
                    bitmap = page.render(scale=min(2, MAX_EDGE / edge))
                    try:
                        image = bitmap.to_pil()
                        try:
                            item = _encode(image, index + 1)
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                size = len(base64.b64decode(item["data"]))
                if used + size > MAX_BYTES:
                    break
                pages.append(item)
                used += size
    else:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in ("JPEG", "PNG") or source.width * source.height > 8_000_000:
                raise ValueError("unsupported or oversized image")
            image = ImageOps.exif_transpose(source)
            try:
                pages.append(_encode(image, 1))
            finally:
                image.close()
        total = 1
    return {"pages": pages, "total_pages": total, "truncated": len(pages) < total}


def render(content: bytes) -> dict | None:
    if not (
        b"%PDF" in content[:1024]
        or content.startswith(b"\x89PNG")
        or content.startswith(b"\xff\xd8")
    ):
        return None
    # Native PDF/image decoding must not hold the worker forever. The child
    # emits at most 2 MiB of JPEG data (base64 envelope); stdin is the capped upload.
    if len(content) > filesec.max_bytes("capture_preview"):
        raise ValueError("preview input cap")
    result = subprocess.run(
        [sys.executable, "-m", "app.services.capture_pages", "--render"],
        input=content,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=RENDER_SECONDS,
        check=True,
    )
    return json.loads(result.stdout)


async def generate(db: AsyncSession, run: ExtractionRun, content: bytes) -> None:
    if run.preview_json:
        return  # same immutable source; do not repeat rendering after parse retries
    try:
        rendered = await run_in_threadpool(render, content)
        if rendered is None:
            manifest = CapturePages(
                status="unsupported",
                message="This file has no page images; use the original document.",
            )
        else:
            pages = []
            # A storage/registry failure rolls back only this advisory seam.
            async with db.begin_nested():
                for page in rendered["pages"]:
                    data = base64.b64decode(page["data"], validate=True)
                    sha, size = await documents.store(
                        PREFIX,
                        run.org_id,
                        data,
                        "image/jpeg",
                        db=db,
                        filename=f"page-{page['number']}.jpg",
                    )
                    pages.append(
                        CapturePage(
                            number=page["number"],
                            sha256=sha,
                            size=size,
                            width=page["width"],
                            height=page["height"],
                        )
                    )
            manifest = CapturePages(
                status="ready",
                pages=pages,
                total_pages=rendered["total_pages"],
                truncated=rendered["truncated"],
            )
            if not pages:
                manifest = unavailable()
    except Exception:  # noqa: BLE001 — advisory; never mask or fail the actual parse
        manifest = unavailable()
    run.preview_json = manifest.model_dump_json()


def manifest(run: ExtractionRun) -> CapturePages:
    if not run.preview_json:
        return unavailable(
            "Page previews were not recorded for this capture; use the original document."
        )
    try:
        return CapturePages.model_validate_json(run.preview_json)
    except ValueError:
        return unavailable()


if __name__ == "__main__":
    if sys.platform == "linux":
        import resource

        memory_budget = filesec.RESOURCE_BYTES["capture_renderer_memory"]
        resource.setrlimit(resource.RLIMIT_AS, (memory_budget, memory_budget))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    print(json.dumps(_render(sys.stdin.buffer.read())))  # noqa: T201 — bounded child protocol
