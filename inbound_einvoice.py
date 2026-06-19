"""
INBOUND E-INVOICE INTAKE — the PUSH side of automated document capture.

The platform already PARSES structured e-invoices deterministically
(`extract.parse_einvoice` for UBL/CII/Factur-X) and EXPORTS them (`einvoice_export.py`).
This module is the complementary INBOUND channel: it RECEIVES a structured e-invoice
(a UBL/CII XML document, or a Factur-X/ZUGFeRD hybrid PDF that embeds the EN-16931 XML)
and feeds it into the SAME extract -> review-draft -> register pipeline an UPLOAD uses.

HOW (deliberately thin — it reuses, it does not re-parse):
  • `intake_einvoice(data, filename, user)` does a cheap, SAFE pre-validation (size cap +
    defused XML probe via extract._is_xml/_xml_invoice_root, or a Factur-X embedded-XML
    probe for a PDF) so a non-e-invoice / malformed / oversized blob is REJECTED before it
    ever reaches the worker, then ENQUEUES the original bytes on the existing intake queue
    (`waiting_room.enqueue`). The worker's standard EXTRACT path then runs
    `extract.extract()`, which detects the structured XML and parses it via
    `parse_einvoice` at high confidence with NO AI — producing the NORMAL review draft.
  • From there it is byte-identical to an uploaded e-invoice: ADVISORY until a human
    confirms (or auto-filed only when autopilot is ON), then registered via the existing
    `waiting_room`/draft path. No new bespoke parser, no new draft shape, no new register.

SECURITY / SAFETY:
  • XML is parsed only through `safexml` (defusedxml) — billion-laughs / XXE safe; the
    pre-validation probe and the worker both use it. We never expand entities.
  • An oversized document is rejected up front (MAX_INBOUND_BYTES) so a hostile push
    cannot exhaust the inbox.
  • The bytes are vaulted/processed exactly like an upload — same dedup, same audit at
    confirm time.

THE PEPPOL / ACCESS-POINT BOUNDARY (what a live go-live still needs from the operator):
  A real PEPPOL inbound flow terminates at a PEPPOL ACCESS POINT (a certified SMP/AS4
  endpoint) that authenticates the sender, validates the AS4 envelope, and hands you the
  decoded EN-16931 business document (the UBL/CII XML). That access point is EXTERNAL
  infrastructure (a certified service / partner) and is OUT OF SCOPE here — it cannot be
  built or tested without it. THIS module is everything ON OUR SIDE of that boundary: the
  authenticated intake function the AP (or an email/SFTP/API drop) calls with the XML, the
  parse, and the pipeline wiring. To go live an operator wires their Access Point's
  delivery webhook/poller to call `intake_einvoice(xml_bytes, filename, user)` (or drops
  files into a watched folder a worker feeds here). Until then the admin "Import e-invoice"
  entry point exercises the entire downstream path with a real document.
"""
import os

import applog

log = applog.get("inbound_einvoice")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

# Reject anything larger than this up front (an e-invoice XML is KBs; a Factur-X PDF a
# few MB). Mirrors extract.ZIP_MAX_MEMBER_BYTES headroom without importing it as a hard
# dep. Override with INBOUND_EINVOICE_MAX_BYTES.
MAX_INBOUND_BYTES = int(os.environ.get("INBOUND_EINVOICE_MAX_BYTES", str(25 * 1024 * 1024)))


class InboundRejected(ValueError):
    """The inbound document was rejected (not an e-invoice / malformed / oversized).
    A ValueError subclass so callers can catch it distinctly from a queue error."""


def _looks_like_pdf(data):
    return data[:5] == b"%PDF-" or data[:8].lstrip()[:5] == b"%PDF-"


def is_einvoice(data, filename):
    """True if `data` is a structured e-invoice this channel can ingest: a UBL/CII/XML
    invoice document, OR a Factur-X/ZUGFeRD hybrid PDF that embeds the EN-16931 XML.
    Uses the SAME defused probes the deterministic extract path uses — never raises."""
    import extract as EX
    try:
        if _looks_like_pdf(data):
            return EX._pdf_embedded_xml(data) is not None
        # XML (by extension or sniff) whose root is an invoice document.
        if EX._is_xml(filename or "", data):
            return EX._xml_invoice_root(data)
        return False
    except Exception as e:
        log.warning("is_einvoice probe failed for %r: %s", filename, e)
        return False


def intake_einvoice(data, filename="einvoice.xml", user="einvoice-inbound"):
    """Receive ONE inbound structured e-invoice and enqueue it into the existing intake
    pipeline. Returns (job_id, status) from waiting_room.enqueue.

    Validates (SAFE, no AI):
      • non-empty and within MAX_INBOUND_BYTES (else InboundRejected),
      • is a parseable UBL/CII/XML invoice or a Factur-X PDF (else InboundRejected) —
        the defused probe also rejects an entity-bomb / malformed XML.

    On success the ORIGINAL bytes are parked on the intake queue (kind=extract); the
    worker's standard path parses them via extract.parse_einvoice into the normal review
    draft — ADVISORY until confirmed (or auto-filed when autopilot is ON). The caller (the
    worker tier, or the admin import endpoint) is responsible for being authenticated."""
    if not data:
        raise InboundRejected("empty inbound e-invoice")
    if len(data) > MAX_INBOUND_BYTES:
        raise InboundRejected(
            f"inbound e-invoice too large ({len(data)} bytes > {MAX_INBOUND_BYTES} cap)")
    if not is_einvoice(data, filename):
        raise InboundRejected(
            "not a recognised structured e-invoice (expected UBL/CII XML or a Factur-X PDF)")
    import waiting_room as IQ
    name = filename or "einvoice.xml"
    jid, st = IQ.enqueue(data, name, backend="einvoice-inbound", user=user)
    log.info("inbound e-invoice %r enqueued as job %s (%s)", name, jid, st)
    return jid, st


# ---------------------------------------------------------------- CLI / self-test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        raw = open(sys.argv[1], "rb").read()
        print(intake_einvoice(raw, os.path.basename(sys.argv[1]), user="cli"))
    else:
        print("usage: python inbound_einvoice.py <invoice.xml|invoice.pdf>")
