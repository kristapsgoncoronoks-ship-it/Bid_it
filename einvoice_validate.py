"""
EINVOICE_VALIDATE — REAL EN 16931 / PEPPOL BIS Billing 3.0 Schematron validation.

This module validates a UBL 2.1 Invoice/CreditNote (the bytes produced by
`invoicing.einvoice_xml`) against the OFFICIAL OpenPEPPOL schematrons:

  - schematron/CEN-EN16931-UBL.sch    (the EN 16931 model bound to UBL)
  - schematron/PEPPOL-EN16931-UBL.sch (the PEPPOL BIS Billing 3.0 rules)

WHY NOT `lxml.isoschematron`: both schematrons declare `queryBinding="xslt2"`. lxml
only bundles the XSLT 1.0 ISO skeleton, so it SILENTLY DROPS every xslt2-bound rule
(the BR-CO total/category arithmetic, codelist look-ups, …) and reports a FALSE PASS.
We therefore compile the schematrons with a REAL XSLT 2.0 engine, SaxonC-HE (saxonche),
through the canonical ISO three-stage pipeline:

    iso_dsdl_include.xsl  ->  iso_abstract_expand.xsl  ->  iso_svrl_for_xslt2.xsl

That turns each `.sch` into an SVRL-emitting XSLT 2.0 stylesheet; we run THAT over the
invoice and parse the SVRL (`svrl:failed-assert` / `svrl:successful-report`).

FAIL-SOFT. saxonche is an OPTIONAL server dependency (requirements.txt, pinned). If it is
not importable, or the vendored schematron/skeleton files are missing, `validate_ubl`
returns `{"available": False, "ok": None, ...}` with a clear message — it NEVER raises and
NEVER reports a false PASS. The rest of the app must not import-depend on saxonche; only
this validate feature does (the import is local, inside the functions).

CACHE. Compiling the schematrons is slow (the XSLT2 skeleton is large), so the compiled
SVRL stylesheets (saxonche `XsltExecutable`s, one per `.sch`) are cached process-wide on
first use, keyed by the PySaxonProcessor instance.
"""
import os
import threading

WORKDIR = os.path.dirname(os.path.abspath(__file__))
SCHEMATRON_DIR = os.path.join(WORKDIR, "schematron")

# The two OFFICIAL schematrons, run in order (CEN model first, then the PEPPOL profile).
SCH_FILES = ("CEN-EN16931-UBL.sch", "PEPPOL-EN16931-UBL.sch")
# The ISO Schematron XSLT2 skeleton, applied in this exact order to compile a .sch.
ISO_PIPELINE = ("iso_dsdl_include.xsl", "iso_abstract_expand.xsl",
                "iso_svrl_for_xslt2.xsl")
# iso_svrl_for_xslt2.xsl `xsl:import`s this saxon skeleton (must sit beside it). It is not
# itself a pipeline STAGE — it is a dependency of stage 3 — but it must be present.
ISO_SUPPORT = ("iso_schematron_skeleton_for_saxon.xsl",)

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"

try:
    import applog
    log = applog.get("einvoice_validate")
except Exception:  # pragma: no cover - applog always present in-app
    import logging
    log = logging.getLogger("einvoice_validate")

# Process-wide cache of compiled SVRL stylesheets, guarded by a lock (compilation is slow
# and must happen at most once). Maps schematron filename -> saxonche XsltExecutable.
_lock = threading.Lock()
_compiled = None          # dict | None
_processor = None         # PySaxonProcessor (kept alive for the lifetime of the cache)


def available():
    """True iff saxonche imports AND every vendored file is present. Never raises."""
    try:
        import saxonche  # noqa: F401
    except Exception:
        return False
    return _files_present()


def _files_present():
    for f in SCH_FILES + ISO_PIPELINE + ISO_SUPPORT:
        if not os.path.exists(os.path.join(SCHEMATRON_DIR, f)):
            return False
    return True


def _missing_message():
    if not _files_present():
        return ("Schematron validation unavailable: the vendored schematron/skeleton "
                "files are missing under schematron/.")
    return ("Schematron validation unavailable: the optional 'saxonche' package is not "
            "installed (pip install saxonche).")


def _compile(proc):
    """Compile each .sch through the ISO three-stage pipeline into an SVRL stylesheet.
    Returns {sch_filename: XsltExecutable}. Raises on a genuine compile error (callers
    convert that into a fail-soft 'available: False')."""
    xc = proc.new_xslt30_processor()
    # Pre-compile the three skeleton stages once; they are reused for every schematron.
    stages = []
    for stage in ISO_PIPELINE:
        path = os.path.join(SCHEMATRON_DIR, stage)
        stages.append(xc.compile_stylesheet(stylesheet_file=path))

    out = {}
    for sch in SCH_FILES:
        src = _read_text(os.path.join(SCHEMATRON_DIR, sch))
        # Stage 1: resolve includes; Stage 2: expand abstract patterns; Stage 3: emit the
        # SVRL XSLT2 stylesheet. Each stage transforms the previous stage's STRING output.
        for ex in stages:
            node = proc.parse_xml(xml_text=src)
            src = ex.transform_to_string(xdm_node=node)
            if src is None:
                raise RuntimeError("ISO pipeline stage produced no output for %s" % sch)
        # `src` is now an XSLT 2.0 stylesheet that emits SVRL. Compile it for reuse.
        out[sch] = xc.compile_stylesheet(stylesheet_text=src)
    return out


def _read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _ensure_compiled():
    """Lazily build (and cache) the compiled SVRL stylesheets. Returns (proc, compiled)
    or (None, None) when unavailable. Never raises."""
    global _compiled, _processor
    if _compiled is not None:
        return _processor, _compiled
    with _lock:
        if _compiled is not None:
            return _processor, _compiled
        try:
            from saxonche import PySaxonProcessor
        except Exception as e:
            log.info("saxonche not importable; schematron validation unavailable: %s", e)
            return None, None
        if not _files_present():
            log.info("vendored schematron files missing; validation unavailable")
            return None, None
        try:
            proc = PySaxonProcessor(license=False)
            compiled = _compile(proc)
        except Exception:
            log.exception("schematron compilation failed; validation unavailable")
            return None, None
        _processor, _compiled = proc, compiled
        return _processor, _compiled


def _parse_svrl(svrl_text):
    """Parse an SVRL document into a list of finding dicts. A failed-assert and a
    successful-report are BOTH findings; the @flag (fatal/error/warning) classifies them.
    Returns a list of {kind, rule, flag, text, location, test}."""
    import xml.etree.ElementTree as ET
    findings = []
    if not svrl_text:
        return findings
    root = ET.fromstring(svrl_text.encode("utf-8")
                         if isinstance(svrl_text, str) else svrl_text)
    for kind in ("failed-assert", "successful-report"):
        for el in root.iter("{%s}%s" % (SVRL_NS, kind)):
            text_el = el.find("{%s}text" % SVRL_NS)
            text = (text_el.text or "").strip() if text_el is not None else ""
            findings.append({
                "kind": kind,
                "rule": (el.get("id") or "").strip(),
                "flag": (el.get("flag") or "").strip().lower(),
                "text": text,
                "location": (el.get("location") or "").strip(),
                "test": (el.get("test") or "").strip(),
            })
    return findings


def validate_ubl(xml_bytes):
    """Validate UBL invoice bytes against BOTH official schematrons (CEN then PEPPOL).

    Returns a dict:
      {"available": bool, "ok": bool|None, "errors": [...], "warnings": [...],
       "message": str}
    Each error/warning is {schematron, rule, flag, text, location, test}.

    `flag` fatal/error -> errors; warning -> warnings; `ok = (no errors)`.
    FAIL-SOFT: unavailable -> {"available": False, "ok": None, ...}; never raises, never a
    false PASS."""
    proc, compiled = _ensure_compiled()
    if compiled is None:
        return {"available": False, "ok": None, "errors": [], "warnings": [],
                "message": _missing_message()}
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    errors, warnings = [], []
    try:
        for sch in SCH_FILES:
            ex = compiled[sch]
            node = proc.parse_xml(xml_text=xml_bytes.decode("utf-8"))
            # The SVRL stylesheet reads a global param (e.g. ProfileID) off the document
            # context item, so the source must be the GLOBAL CONTEXT ITEM, not only the
            # initial match selection. transform_to_string sets both.
            svrl = ex.transform_to_string(xdm_node=node)
            for f in _parse_svrl(svrl):
                rec = {"schematron": sch, **f}
                # warning -> warnings; everything else that fired -> error.
                if f.get("flag") == "warning":
                    warnings.append(rec)
                else:
                    errors.append(rec)
    except Exception as e:
        # A genuine engine failure must NOT masquerade as a pass.
        log.exception("schematron run failed")
        return {"available": True, "ok": None, "errors": [], "warnings": [],
                "message": "Schematron validation error: %s" % (str(e)[:200])}
    ok = len(errors) == 0
    return {"available": True, "ok": ok, "errors": errors, "warnings": warnings,
            "message": ("PASSED — 0 errors" if ok
                        else "%d error(s)" % len(errors))}
