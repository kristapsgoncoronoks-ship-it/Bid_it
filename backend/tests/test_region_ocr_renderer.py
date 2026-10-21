"""Original-quality geometry and real native-process controls for region OCR."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services import filesec, region_ocr

REQUEST = {"page": 1, "x": 0, "y": 0, "width": 1, "height": 1, "rotation": 0, "language": "eng"}
COLORS = ((230, 10, 10), (10, 180, 10), (10, 10, 230), (20, 20, 20))


def colored_image(*, exif=False):
    image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    for box, color in zip(
        ((0, 0, 200, 100), (200, 0, 400, 100), (0, 100, 200, 200), (200, 100, 400, 200)),
        COLORS,
        strict=True,
    ):
        draw.rectangle(box, fill=color)
    output = io.BytesIO()
    if exif:
        metadata = Image.Exif()
        metadata[274] = 6
        image.save(output, "JPEG", quality=98, exif=metadata)
    else:
        image.save(output, "PNG")
    image.close()
    return output.getvalue()


def colored_pdf():
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(400, 200))
    for box, color in zip(((0, 100), (200, 100), (0, 0), (200, 0)), COLORS, strict=True):
        pdf.setFillColorRGB(*(channel / 255 for channel in color))
        pdf.rect(*box, 200, 100, fill=1, stroke=0)
    pdf.save()
    return output.getvalue()


@pytest.mark.parametrize("kind", ["image", "pdf"])
@pytest.mark.parametrize("rotation,color_index", [(0, 0), (90, 2), (180, 3), (270, 1)])
def test_selector_geometry_matches_clockwise_postrotation_coordinates(kind, rotation, color_index):
    content = colored_pdf() if kind == "pdf" else colored_image()
    image, geometry = region_ocr._render_region(
        content, {**REQUEST, "width": 0.5, "height": 0.5, "rotation": rotation}
    )
    try:
        actual = image.getpixel((image.width // 2, image.height // 2))
        assert all(abs(a - b) < 4 for a, b in zip(actual, COLORS[color_index], strict=True))
        assert geometry["render_dpi"] == (300 if kind == "pdf" else None)
    finally:
        image.close()


def test_exif_orientation_matches_preview_before_user_rotation():
    image, geometry = region_ocr._render_region(
        colored_image(exif=True), {**REQUEST, "width": 0.5, "height": 0.5}
    )
    try:
        assert geometry["source_width"] == 200 and geometry["source_height"] == 400
        assert image.size == (100, 200)
        assert all(
            abs(a - b) < 10 for a, b in zip(image.getpixel((50, 100)), COLORS[2], strict=True)
        )
    finally:
        image.close()


def test_pdf_crop_retains_300dpi_instead_of_using_960px_preview():
    image, geometry = region_ocr._render_region(
        colored_pdf(), {**REQUEST, "width": 0.25, "height": 0.5}
    )
    try:
        # 100 PDF points at 300 DPI is about417px; a960px wholepage preview
        # would provide only240px for exactly the same selected area.
        assert 415 <= image.width <= 418 and 415 <= image.height <= 418
        assert geometry["source_unit"] == "pdf_points"
    finally:
        image.close()


@pytest.mark.parametrize(
    "replacement",
    [
        {"x": float("nan")},
        {"height": float("inf")},
        {"width": -1},
        {"height": 0},
        {"x": 0.8, "width": 0.3},
        {"y": -0.1},
        {"page": True},
        {"page": 51},
        {"rotation": 45},
        {"rotation": True},
        {"language": "eng --psm 0"},
        {"x": "0.5"},
    ],
)
def test_invalid_or_injected_settings_are_refused_before_native_work(replacement, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid geometry must never start a native process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(region_ocr.RegionOcrError) as failure:
        region_ocr.recognize(b"stored-original", {**REQUEST, **replacement})
    assert failure.value.code == "invalid_region"


def test_large_page_can_read_small_region_without_fullpage_bitmap():
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(4000, 4000))
    pdf.drawString(25, 3950, "Only this corner is rendered")
    pdf.save()
    with pytest.raises(region_ocr.RegionOcrError, match="smaller area"):
        region_ocr._render_region(output.getvalue(), REQUEST)
    image, geometry = region_ocr._render_region(
        output.getvalue(), {**REQUEST, "width": 0.05, "height": 0.05}
    )
    try:
        assert image.width * image.height < 1_000_000
        assert geometry["render_dpi"] == 300
    finally:
        image.close()


def test_transparent_crop_is_composited_onto_white():
    image = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
    output = io.BytesIO()
    image.save(output, "PNG")
    image.close()
    selected, _ = region_ocr._render_region(output.getvalue(), REQUEST)
    try:
        assert selected.mode == "RGB" and selected.getpixel((50, 40)) == (255, 255, 255)
    finally:
        selected.close()


@pytest.mark.parametrize(
    "content,selection,code",
    [
        (b"plain text", REQUEST, "unsupported_source"),
        (colored_image(), {**REQUEST, "page": 2}, "invalid_page"),
        (colored_image(), {**REQUEST, "width": 0.000001}, "invalid_region"),
    ],
)
def test_source_and_page_refusals_are_specific(content, selection, code):
    with pytest.raises(region_ocr.RegionOcrError) as failure:
        region_ocr._render_region(content, selection)
    assert failure.value.code == code


def invoice_image():
    image = Image.new("RGB", (2400, 1400), "white")
    font = ImageFont.truetype("DejaVuSans.ttf", 66)
    draw = ImageDraw.Draw(image)
    draw.text((100, 150), "INV-2026-12345", font=font, fill="black")
    draw.text((100, 800), "DO NOT READ THIS OTHER AREA", font=font, fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    image.close()
    return output.getvalue()


def test_native_ocr_reads_only_selected_original_area_and_does_not_change_bytes():
    import hashlib

    content = invoice_image()
    original = hashlib.sha256(content).hexdigest()
    result = region_ocr.recognize(content, {**REQUEST, "height": 0.35})
    assert "INV-2026-12345" in result["text"]
    assert "OTHER AREA" not in result["text"]
    assert result["crop_width"] == 2400 and result["crop_height"] == 490
    assert result["language"] == "eng" and "eng" in result["installed_languages"]
    assert result["engine"] == "tesseract" and "confidence" not in result
    assert hashlib.sha256(content).hexdigest() == original


def test_fractional_pdf_dimensions_survive_native_ocr_and_proposal_validation():
    import hashlib
    import uuid

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.schemas.capture_region import RegionReadIn
    from app.services.capture_region import _proposals

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setFont("Helvetica", 24)
    pdf.drawString(25, A4[1] - 65, "INV-A4-12345")
    pdf.save()
    content = output.getvalue()
    selection = {**REQUEST, "width": 0.8, "height": 0.2}
    result = region_ocr.recognize(content, selection)
    assert "INV-A4-12345" in result["text"]
    assert result["source_width"] != int(result["source_width"])
    proposal = _proposals(
        result,
        RegionReadIn(
            **selection,
            command_key=str(uuid.uuid4()),
            expected_attempt_seq=0,
            source_sha256=hashlib.sha256(content).hexdigest(),
            target="field",
            field="invoice_number",
        ),
    )
    assert proposal.source_width == result["source_width"]
    assert proposal.fields[0].value == "INV-A4-12345"


def test_native_child_has_no_inherited_credentials_and_is_reaped(monkeypatch):
    real = subprocess.Popen
    seen = []
    monkeypatch.setenv("SECRET_KEY", "synthetic-private-worker-value")
    monkeypatch.setenv("DATABASE_URL", "synthetic-private-database-value")

    def spawn(command, **kwargs):
        assert "SECRET_KEY" not in kwargs["env"] and "DATABASE_URL" not in kwargs["env"]
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
        assert kwargs["start_new_session"] is True
        process = real(command, **kwargs)
        seen.append((process, kwargs["cwd"]))
        return process

    monkeypatch.setattr(region_ocr.subprocess, "Popen", spawn)
    assert "INV-2026" in region_ocr.recognize(invoice_image(), {**REQUEST, "height": 0.35})["text"]
    process, directory = seen[0]
    assert process.poll() == 0 and not Path(directory).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)


@pytest.mark.parametrize(
    "program,code",
    [
        ("while True: pass", "ocr_timeout"),
        ("print('x' * 9000000)", "region_output_too_large"),
        ("print('not-json')", "ocr_failed"),
    ],
)
def test_child_failure_is_bounded_reaped_and_next_crop_proceeds(monkeypatch, program, code):
    real = subprocess.Popen
    seen = []

    def spawn(command, **kwargs):
        process = real([sys.executable, "-c", program], **kwargs)
        seen.append(process)
        return process

    with monkeypatch.context() as patch:
        patch.setattr(region_ocr.subprocess, "Popen", spawn)
        patch.setattr(region_ocr, "WALL_SECONDS", 0.2)
        with pytest.raises(region_ocr.RegionOcrError) as failure:
            region_ocr.recognize(invoice_image(), REQUEST)
        assert failure.value.code == code
    assert seen[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(seen[0].pid, 0)
    assert "INV-2026" in region_ocr.recognize(invoice_image(), {**REQUEST, "height": 0.35})["text"]


def test_blank_region_is_empty_evidence_not_invented_invoice_fields():
    result = region_ocr.recognize(
        invoice_image(), {**REQUEST, "x": 0.75, "width": 0.25, "height": 0.3}
    )
    assert result["text"] == ""
    assert not any(key in result for key in ("invoice_number", "currency", "supplier"))


def test_configured_source_and_central_output_caps_are_enforced(monkeypatch):
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 0)
    with pytest.raises(region_ocr.RegionOcrError) as failure:
        region_ocr.recognize(b"x", REQUEST)
    assert failure.value.code == "source_too_large"


def test_native_inner_language_and_error_codes_are_sanitized(monkeypatch):
    real = subprocess.Popen
    program = (
        "from app.services import region_ocr\n"
        "import pytesseract\n"
        "pytesseract.get_languages = lambda: ['osd']\n"
        "import json,sys\n"
        "region_ocr._child(json.loads(sys.argv[1]))\n"
    )

    def spawn(command, **kwargs):
        return real([sys.executable, "-c", program, command[-1]], **kwargs)

    monkeypatch.setattr(region_ocr.subprocess, "Popen", spawn)
    with pytest.raises(region_ocr.RegionOcrError) as failure:
        region_ocr.recognize(invoice_image(), REQUEST)
    assert failure.value.code == "language_unavailable"


@pytest.mark.parametrize(
    "response",
    [
        {"result": 5},
        {"result": {"text": "x" * (filesec.RESOURCE_BYTES["region_ocr_text"] + 1)}},
        {"code": "native-private-value", "error": "PRIVATE DOCUMENT TEXT AND INTERNAL NATIVE PATH"},
        {"code": "ocr_failed", "error": "PRIVATE DOCUMENT TEXT AND INTERNAL NATIVE PATH"},
    ],
)
def test_parent_treats_native_result_envelope_as_untrusted(monkeypatch, response):
    real = subprocess.Popen
    # A generated child program supplies only synthetic malformed protocol data.
    program = f"import sys;sys.stdout.write({json.dumps(response)!r})"

    def spawn(command, **kwargs):
        return real([sys.executable, "-c", program], **kwargs)

    monkeypatch.setattr(region_ocr.subprocess, "Popen", spawn)
    with pytest.raises(region_ocr.RegionOcrError) as failure:
        region_ocr.recognize(b"synthetic-original", REQUEST)
    assert failure.value.code in {"ocr_failed", "region_output_too_large"}
    assert "PRIVATE" not in str(failure.value)
    assert "native-private-value" not in str(failure.value)


@pytest.mark.parametrize("mode", ["early_exit", "timeout"])
def test_parent_cleanup_kills_descendants(monkeypatch, tmp_path, mode):
    real = subprocess.Popen
    pid_file = tmp_path / "descendant.pid"
    program = (
        "import subprocess,sys,json\n"
        "from pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'])\n"
        f"Path({str(pid_file)!r}).write_text(str(p.pid))\n"
        + (
            "print(json.dumps({'result':{'text':'bounded synthetic result'}}))\n"
            if mode == "early_exit"
            else "while True: pass\n"
        )
    )

    def spawn(command, **kwargs):
        return real([sys.executable, "-c", program], **kwargs)

    monkeypatch.setattr(region_ocr.subprocess, "Popen", spawn)
    # Allow Python startup under a one-CPU container while still exercising
    # bounded timeout and process-group cleanup in both exit modes.
    monkeypatch.setattr(region_ocr, "WALL_SECONDS", 2.0)
    if mode == "early_exit":
        assert region_ocr.recognize(b"synthetic", REQUEST)["text"] == "bounded synthetic result"
    else:
        with pytest.raises(region_ocr.RegionOcrError) as failure:
            region_ocr.recognize(b"synthetic", REQUEST)
        assert failure.value.code == "ocr_timeout"
    descendant = int(pid_file.read_text())
    status = Path(f"/proc/{descendant}/stat")
    # An orphaned killed child may briefly be a zombie until PID1 reaps it;
    # the group cleanup must ensure it cannot continue consuming CPU/work.
    import time

    for _ in range(100):
        if not status.exists() or status.read_text().split()[2] == "Z":
            break
        time.sleep(0.01)
    assert not status.exists() or status.read_text().split()[2] == "Z"


@pytest.mark.parametrize("kind", ["cpu", "memory"])
def test_real_child_kernel_cpu_and_memory_limits(monkeypatch, kind):
    real = subprocess.Popen
    action = "bytes(2_000_000_000)" if kind == "memory" else "spin()"
    program = (
        "from app.services import region_ocr\n"
        "import json,sys\n"
        "region_ocr.CPU_SECONDS=1\n"
        "def spin():\n    while True: pass\n"
        f"region_ocr._recognize=lambda *args: {action}\n"
        "region_ocr._child(json.loads(sys.argv[1]))\n"
    )

    def spawn(command, **kwargs):
        return real([sys.executable, "-c", program, command[-1]], **kwargs)

    monkeypatch.setattr(region_ocr.subprocess, "Popen", spawn)
    with pytest.raises(region_ocr.RegionOcrError) as failure:
        region_ocr.recognize(b"synthetic", REQUEST)
    assert failure.value.code == "ocr_resource_limit"
