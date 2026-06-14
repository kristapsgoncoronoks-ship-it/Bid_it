"""
DOCUMENT RENDERING — .docx -> PDF via LibreOffice (soffice), best-effort.

Keeps the subprocess/tempfile/soffice concern out of customer_master.py. Import lazily
where used; `docx_to_pdf` NEVER raises — on any failure (no binary, timeout, non-zero
exit, missing output) it logs via applog and returns None so the caller can fall back to
delivering the prefilled .docx as-is.
"""
import os
import shutil
import subprocess
import tempfile

import applog

WORKDIR = os.path.dirname(os.path.abspath(__file__))


def _soffice_bin():
    """Resolve the LibreOffice headless binary defensively, or None if absent."""
    return shutil.which("soffice") or shutil.which("libreoffice") or (
        "/usr/bin/soffice" if os.path.exists("/usr/bin/soffice") else None)


def docx_to_pdf(docx_bytes):
    """Convert .docx bytes to PDF bytes using a headless soffice. Returns the PDF bytes,
    or None on any failure (caller delivers the .docx instead). Never raises.

    Each call gets its OWN LibreOffice profile dir via `-env:UserInstallation` so the
    single shared-profile lock can't serialize/deadlock concurrent conversions."""
    soffice = _soffice_bin()
    if not soffice:
        applog.get("doc_render").warning("docx_to_pdf: no soffice/libreoffice binary found")
        return None
    tmp = tempfile.mkdtemp(prefix="ffs_docx2pdf_")
    try:
        in_path = os.path.join(tmp, "in.docx")
        with open(in_path, "wb") as fh:
            fh.write(docx_bytes)
        profile = "file://" + os.path.join(tmp, "profile")
        try:
            r = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp,
                 "-env:UserInstallation=" + profile, in_path],
                capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            applog.get("doc_render").warning("docx_to_pdf: soffice timed out")
            return None
        except OSError as e:
            applog.get("doc_render").warning("docx_to_pdf: soffice failed to start: %s", e)
            return None
        if r.returncode != 0:
            applog.get("doc_render").warning(
                "docx_to_pdf: soffice exit %s: %s", r.returncode,
                (r.stderr or b"").decode("utf-8", "replace")[:500])
            return None
        out_path = os.path.join(tmp, "in.pdf")
        if not os.path.exists(out_path):
            applog.get("doc_render").warning("docx_to_pdf: soffice produced no PDF output")
            return None
        with open(out_path, "rb") as fh:
            return fh.read()
    except OSError as e:
        applog.get("doc_render").warning("docx_to_pdf: I/O error: %s", e)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
