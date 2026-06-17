"""
capture_folder.py — co-locate each upload's ORIGINAL document(s) and the TEXT captured
from them in ONE human-browsable folder, so an operator can open a single folder and see
both the source PDF and exactly what the system read out of it.

Layout (under WORKDIR/captures/, gitignored — may contain IBANs/bank details):

    captures/<upload_sha256>/
        <original filename(s)>     the source PDF/XML bytes, verbatim
        captured.txt               the FULL text READ from the document(s) (line by line)
        capture.json               the AI-vision capture document  (only when vision ran)
        capture.txt                a readable rendering of that capture (only when vision ran)
        MANIFEST.json              {sha, created, source_name, files:[{name,size}]}

This is a CONVENIENCE MIRROR for human inspection and verification; the authoritative
stores are unchanged — the document vault (on confirm) and the content-addressed data
lake. It is BEST-EFFORT and NEVER raises into a web request: any failure is logged and
swallowed so it can never break capture or the review screen.
"""
import json
import os
import re
import time

import applog

log = applog.get("capture_folder")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(WORKDIR, "captures")

READ_TEXT_NAME = "captured.txt"
CAPTURE_JSON_NAME = "capture.json"
CAPTURE_TEXT_NAME = "capture.txt"
MANIFEST_NAME = "MANIFEST.json"


def _safe_name(name, default="document"):
    """A filesystem-safe basename — strips any directory parts and unusual characters so a
    crafted filename can never escape the folder (no traversal, no separators)."""
    base = os.path.basename((name or "").strip()) or default
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)
    base = base.strip(". ") or default
    return base[:150]


def _sha_dir(upload_sha256):
    sha = re.sub(r"[^0-9a-fA-F]", "", str(upload_sha256 or ""))
    if len(sha) < 16:                 # require a real sha to key the folder
        return None
    return os.path.join(ROOT, sha)


def folder(upload_sha256):
    """The folder path for an upload sha if it EXISTS, else None."""
    d = _sha_dir(upload_sha256)
    return d if (d and os.path.isdir(d)) else None


def save(upload_sha256, pdfs, read_text, source_name=None,
         capture_json=None, capture_text=None):
    """Write the paired folder for one upload. `pdfs` is a list of (name, bytes); both the
    source document(s) and the captured text land in the SAME folder. Returns the folder
    path on success or None. NEVER raises."""
    d = _sha_dir(upload_sha256)
    if not d:
        log.warning("capture_folder.save: missing/short sha — skipped")
        return None
    try:
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)        # the folder can hold sensitive bank details
        except OSError:
            pass
        files = []
        used = set()
        for name, data in (pdfs or []):
            fn = _safe_name(name)
            # de-dup names within the folder so two same-named PDFs don't clobber
            stem, ext = os.path.splitext(fn)
            i = 2
            while fn in used:
                fn = f"{stem}_{i}{ext}"
                i += 1
            used.add(fn)
            with open(os.path.join(d, fn), "wb") as f:
                f.write(data or b"")
            files.append({"name": fn, "size": len(data or b"")})
        # the full text READ from the document(s)
        txt = (read_text or "").encode("utf-8")
        with open(os.path.join(d, READ_TEXT_NAME), "wb") as f:
            f.write(txt)
        files.append({"name": READ_TEXT_NAME, "size": len(txt)})
        # the AI-vision capture document, when present
        if capture_json is not None:
            blob = json.dumps(capture_json, ensure_ascii=False, indent=2,
                              default=str).encode("utf-8")
            with open(os.path.join(d, CAPTURE_JSON_NAME), "wb") as f:
                f.write(blob)
            files.append({"name": CAPTURE_JSON_NAME, "size": len(blob)})
        if capture_text:
            ct = capture_text.encode("utf-8")
            with open(os.path.join(d, CAPTURE_TEXT_NAME), "wb") as f:
                f.write(ct)
            files.append({"name": CAPTURE_TEXT_NAME, "size": len(ct)})
        manifest = {"sha256": re.sub(r"[^0-9a-fA-F]", "", str(upload_sha256)),
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source_name": source_name or "",
                    "files": [f for f in files if f["name"] != MANIFEST_NAME]}
        with open(os.path.join(d, MANIFEST_NAME), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return d
    except Exception as e:
        log.warning("capture_folder.save failed for %s: %s", upload_sha256, e)
        return None


def listing(upload_sha256):
    """List the files in an upload's folder as [{name,size}], newest manifest order if
    available, else directory order. Read-only; NEVER raises -> []."""
    d = folder(upload_sha256)
    if not d:
        return []
    try:
        mpath = os.path.join(d, MANIFEST_NAME)
        if os.path.exists(mpath):
            with open(mpath, encoding="utf-8") as f:
                man = json.load(f)
            out = list(man.get("files", []))
            # include the manifest itself at the end for completeness
            out.append({"name": MANIFEST_NAME, "size": os.path.getsize(mpath)})
            return out
        return [{"name": n, "size": os.path.getsize(os.path.join(d, n))}
                for n in sorted(os.listdir(d))]
    except Exception as e:
        log.warning("capture_folder.listing failed for %s: %s", upload_sha256, e)
        return []


def file_bytes(upload_sha256, name):
    """Read one file from the folder by (sanitised) name, or None. Path-traversal safe —
    the name is reduced to a basename and must resolve INSIDE the folder."""
    d = folder(upload_sha256)
    if not d:
        return None
    fn = _safe_name(name)
    path = os.path.join(d, fn)
    if os.path.commonpath([os.path.realpath(path), os.path.realpath(d)]) != os.path.realpath(d):
        return None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:
        log.warning("capture_folder.file_bytes failed: %s", e)
        return None


def read_text_of(upload_sha256):
    """The captured.txt content for an upload, or '' when absent. NEVER raises."""
    b = file_bytes(upload_sha256, READ_TEXT_NAME)
    return b.decode("utf-8", "replace") if b else ""
