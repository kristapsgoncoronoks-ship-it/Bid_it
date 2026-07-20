"""Central file-security gate over attacker-controlled uploads/attachments.

Emails and uploads are untrusted input: an attachment can be malware, an
executable or a script disguised as an invoice. This gate runs BEFORE anything
parses or OCRs a file, at every intake choke point (manual upload, email intake,
bank statements, receipts):

  1. **Size cap** — bound memory / decompression-bomb blast radius.
  2. **Type validation** — sniff the real content (magic bytes + structure) and
     require it to match an allowlisted kind. A `.pdf` that is actually HTML,
     an EXE/ELF/Mach-O, a shell script or a zip is rejected — this is what stops
     "harmful scripts" (HTML/JS/PHP phishing payloads, macro-bearing archives).
  3. **Malware scan** — the EICAR test signature always, plus optional **ClamAV**
     (`clamd`) when configured: fail-CLOSED when a scanner is configured but
     unreachable (a configured scanner going dark is a security event), skipped
     in dev when none is configured.

XML is *additionally* hardened at parse time by `defusedxml` (XXE /
entity-expansion / billion-laughs), and served files go out inert
(`Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`), never
executed. Sandbox/resource isolation of the OCR worker is a deployment concern.
"""
from __future__ import annotations

import io
import logging

from app.core.config import settings

log = logging.getLogger("invoiceiq.filesec")

# The standard EICAR anti-malware test string — harmless, but every scanner (and
# this gate) must flag it. Lets us prove the scan path works with no AV daemon.
EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

# Kinds this platform accepts, grouped by intake surface.
INVOICE_KINDS = frozenset({"pdf", "xml", "csv", "json"})
RECEIPT_KINDS = frozenset({"pdf", "png", "jpeg"})

_EXT_KIND = {
    "pdf": "pdf", "xml": "xml", "csv": "csv", "json": "json",
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg",
}

# Binary signatures we NEVER accept, regardless of the claimed extension —
# executables, archives and other active-content containers.
_DANGEROUS_MAGICS: tuple[bytes, ...] = (
    b"MZ",                      # DOS/Windows PE (.exe/.dll)
    b"\x7fELF",                 # Linux ELF
    b"\xca\xfe\xba\xbe",        # Mach-O fat / Java class
    b"\xcf\xfa\xed\xfe",        # Mach-O 64-bit
    b"\xce\xfa\xed\xfe",        # Mach-O 32-bit
    b"PK\x03\x04",             # zip / xlsx / docx / jar (macro & script carrier)
    b"Rar!",                    # RAR archive
    b"\x1f\x8b",               # gzip
    b"7z\xbc\xaf\x27\x1c",     # 7-zip
    b"#!",                      # shebang script
    b"\xd0\xcf\x11\xe0",        # legacy OLE (old Office w/ macros)
)

# Active-content markers (a "document" that is really a web/script payload).
_SCRIPT_MARKERS: tuple[bytes, ...] = (
    b"<!doctype html", b"<html", b"<script", b"<?php", b"<%", b"<jsp:",
)


class FileRejected(Exception):
    """Raised when a file fails a security check. `reason` is user-safe."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _max_bytes() -> int:
    return int(getattr(settings, "max_upload_mb", 15)) * 1024 * 1024


def _looks_scripted(content: bytes) -> bool:
    head = content[:2048].lstrip().lower()
    return any(m in head for m in _SCRIPT_MARKERS)


def _kind_by_magic(content: bytes) -> str | None:
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    stripped = content.lstrip()[:64].lower()
    if stripped.startswith(b"<?xml") or (stripped.startswith(b"<") and not _looks_scripted(content)):
        return "xml"
    if stripped[:1] in (b"{", b"["):
        return "json"
    return None


def _ext_of(filename: str) -> str:
    base = (filename or "").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def validate(filename: str, content: bytes, allowed: frozenset[str] = INVOICE_KINDS) -> str:
    """Structural validation. Returns the detected kind or raises FileRejected."""
    if not content:
        raise FileRejected("Empty file")
    if len(content) > _max_bytes():
        raise FileRejected(f"File too large (max {settings.max_upload_mb if hasattr(settings, 'max_upload_mb') else 15} MB)")

    # Universal: reject executables / archives / active content up front.
    if content.startswith(_DANGEROUS_MAGICS):
        raise FileRejected("Executable, archive or active-content files are not allowed")

    # Resolve the claimed kind (extension first, then content sniff).
    kind = _EXT_KIND.get(_ext_of(filename)) or _kind_by_magic(content)
    if kind is None or kind not in allowed:
        raise FileRejected("Unsupported or unrecognised file type — allowed: " + ", ".join(sorted(allowed)))

    # Per-kind content check: the bytes must actually BE the claimed kind, so a
    # renamed HTML/script/binary can't slip through on a trusted extension.
    if kind == "pdf":
        if not content.startswith(b"%PDF"):
            raise FileRejected("Not a valid PDF (signature mismatch)")
    elif kind == "png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FileRejected("Not a valid PNG (signature mismatch)")
    elif kind == "jpeg":
        if not content.startswith(b"\xff\xd8\xff"):
            raise FileRejected("Not a valid JPEG (signature mismatch)")
    elif kind == "xml":
        if _looks_scripted(content):
            raise FileRejected("File looks like HTML/script, not e-invoice XML")
        if not content.lstrip()[:1] == b"<":
            raise FileRejected("Not valid XML")
    elif kind == "json":
        if _looks_scripted(content) or content.lstrip()[:1] not in (b"{", b"["):
            raise FileRejected("Not valid JSON")
    elif kind == "csv":
        if b"\x00" in content[:8192]:
            raise FileRejected("CSV appears to be a binary file")
        if _looks_scripted(content):
            raise FileRejected("File looks like HTML/script, not CSV")
        try:
            content[:8192].decode("utf-8-sig")
        except UnicodeDecodeError:
            raise FileRejected("CSV is not valid UTF-8 text")
    return kind


def scan_malware(content: bytes) -> None:
    """Malware scan. Raises FileRejected on a hit or (when a scanner is
    configured but unreachable) on scan failure."""
    # EICAR — always, no daemon required.
    if EICAR in content:
        raise FileRejected("Malware detected (EICAR test signature)")

    if not getattr(settings, "clamav_enabled", False):
        return  # no scanner configured (dev) — type validation still applied

    try:
        import clamd
        if getattr(settings, "clamav_unix_socket", None):
            cd = clamd.ClamdUnixSocket(settings.clamav_unix_socket)
        else:
            cd = clamd.ClamdNetworkSocket(settings.clamav_host, int(settings.clamav_port))
        result = cd.instream(io.BytesIO(content))
    except FileRejected:
        raise
    except Exception as exc:  # scanner configured but unreachable → fail CLOSED
        log.warning("ClamAV scan failed (failing closed): %s", exc)
        raise FileRejected("Virus scan is unavailable — file rejected") from exc

    status, signature = result.get("stream", ("ERROR", None))
    if status == "FOUND":
        raise FileRejected(f"Malware detected: {signature}")
    if status != "OK":
        raise FileRejected("Virus scan returned an error — file rejected")


def check(filename: str, content: bytes, allowed: frozenset[str] = INVOICE_KINDS) -> str:
    """Full gate: structural validation + malware scan. Returns the kind or
    raises FileRejected. Call this at every intake choke point before parsing."""
    kind = validate(filename, content, allowed)
    scan_malware(content)
    return kind
