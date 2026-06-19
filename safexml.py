"""
SAFE XML PARSING for UNTRUSTED input.

stdlib `xml.etree.ElementTree` is safe against external-entity (XXE) reads on a
modern CPython build, but it still EXPANDS internal entities — a "billion laughs"
payload (a handful of nested entity definitions) blows up to gigabytes and DoSes
the worker. We parse a lot of untrusted XML here: uploaded e-invoices (UBL/CII),
supplier price files, the supplier XML ingest source, etc.

This module exposes `fromstring`/`parse` that delegate to `defusedxml` (which
forbids entity expansion + external entities) when it is importable, and fall back
to the stdlib parser otherwise so the app still RUNS without the dependency. The
dependency is pinned in requirements.txt; prefer it present.

Use this ONLY for parsing EXTERNAL/untrusted input. The app's own XML OUTPUT
(building/serialising via Element/SubElement/tostring) needs no change — defusedxml
is parse-only.
"""
try:                                            # pragma: no cover - import-path branch
    import defusedxml.ElementTree as _DET
    from defusedxml.ElementTree import ParseError  # noqa: F401  (re-export)
    DEFUSED = True

    def fromstring(text):
        """Parse an XML string/bytes from untrusted input; raises on entity bombs."""
        return _DET.fromstring(text)

    def parse(source):
        """Parse an XML file/path from untrusted input; raises on entity bombs."""
        return _DET.parse(source)
except Exception:                               # pragma: no cover - fallback branch
    import xml.etree.ElementTree as _ET
    from xml.etree.ElementTree import ParseError  # noqa: F401  (re-export)
    DEFUSED = False

    def fromstring(text):
        return _ET.fromstring(text)

    def parse(source):
        return _ET.parse(source)
