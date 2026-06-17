"""
DOCUMENT METADATA (A3) — typed CUSTOM FIELDS + hierarchical TAGS over any vaulted
document / invoice. The Paperless-ngx model: typed custom fields (text / number /
monetary / date / select / boolean / documentlink) and NESTED tags, attachable to any
document by its stable reference.

DATA-PRODUCT BOUNDARY. The data-processing ENGINE owns and WRITES the product DBs
(fuel_history.db = invoice_documents + transactions; suppliers.db = supplier master);
the app reads them strictly READ-ONLY via dataproduct.connect(). Metadata is APP DATA,
not a product, so it lives in its OWN app-owned DB (metadata.db, gitignored) keyed by a
document REFERENCE string (`subject_ref`) — this module NEVER opens a product DB and
never adds a column to one. The reference is the SAME `doc:<id>` rowkey the A2 search
index already mints for a registered invoice_documents row, so search can pull a
document's tags / field values straight back by that key.

OWN DB. Like every other module this owns its SQLite file via connect() + db_migrate,
audit-installed, db_tuning-tuned. Tables carry a tenant_id (tenancy seam, inert today):
stamped with tenancy.write_tenant() on INSERT, never filtered yet.

BEST-EFFORT / NEVER-RAISE. Every public API is best-effort and returns AS A VALUE
((obj, "") / (None, err) / [] / {} / bool) — it never raises into the caller, mirroring
sharing.py / tenancy.py / search.py read paths. A bad value (a non-option select, an
unparseable date, junk for a number) is REJECTED gracefully with an error string, not an
exception. Failures are logged via applog (and the cycle guard fails CLOSED — refusing a
re-parent that would create a loop).

TYPES (set_value coerces + validates per the field's type before storing the TEXT value):
  text        — stored as-is (trimmed).
  number      — must parse as a number; stored as a canonical numeric string.
  monetary    — quantized via money.f2 (Decimal, ROUND_HALF_UP); stored as "123.45".
  date        — normalised to ISO "YYYY-MM-DD"; common input formats accepted.
  boolean     — coerced to "0" / "1" (truthy strings recognised).
  select      — must be one of the field's `options`; anything else is rejected.
  documentlink— stores another subject_ref (a free-text document reference).
"""
import os
import json
import sqlite3
import datetime

import applog
import audit
import db_tuning
import db_migrate
import tenancy
import money

log = applog.get("metadata")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned metadata DB (gitignored). A module-level attr so tests can repoint it the
# same way they repoint search.DB / history.DB.
DB = f"{WORKDIR}/metadata.db"

# The supported custom-field types (the Paperless-ngx set).
FIELD_TYPES = ("text", "number", "monetary", "date", "boolean", "select", "documentlink")

SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_fields (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'text',
    options    TEXT,                       -- JSON list for a select field
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id  TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS field_values (
    id         INTEGER PRIMARY KEY,
    field_id   INTEGER NOT NULL,
    subject_ref TEXT NOT NULL,
    value      TEXT,
    tenant_id  TEXT NOT NULL DEFAULT 'default'
);
-- One value per (field, subject).
CREATE UNIQUE INDEX IF NOT EXISTS ux_field_values_fs
    ON field_values(field_id, subject_ref);
CREATE INDEX IF NOT EXISTS ix_field_values_subject ON field_values(subject_ref);
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  INTEGER,                    -- nullable: a top-level tag has no parent
    color      TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id  TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS tag_links (
    id         INTEGER PRIMARY KEY,
    tag_id     INTEGER NOT NULL,
    subject_ref TEXT NOT NULL,
    tenant_id  TEXT NOT NULL DEFAULT 'default'
);
-- One link per (tag, subject).
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_links_ts ON tag_links(tag_id, subject_ref);
CREATE INDEX IF NOT EXISTS ix_tag_links_subject ON tag_links(subject_ref);
CREATE INDEX IF NOT EXISTS ix_tag_links_tag ON tag_links(tag_id);
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = [
    # ── TENANT-QUALIFIED UNIQUE re-key (multi-tenant correctness) ───────────────
    # Two natural-key UNIQUEs here could collide across tenants when the `multitenant`
    # switch is ON:
    #   • field_values(field_id, subject_ref) — "one value per (field, subject)";
    #   • tag_links(tag_id, subject_ref)       — "one link per (tag, subject)".
    # Two tenants assigning the same field/subject (or tag/subject) would COLLIDE on
    # the UNIQUE — and set_value's ON CONFLICT(field_id, subject_ref) would even
    # OVERWRITE the other tenant's value. Re-key each UNIQUE to lead with tenant_id so
    # uniqueness (and the upsert conflict target) is per-tenant.
    #
    # These are UNIQUE *indexes* (not table-level constraints / PKs), so the re-key is
    # a simple DROP INDEX + CREATE — NO table rebuild: the surrogate `id` PK, the rows
    # and the audit triggers (on the TABLE, not the index) are untouched. Existing rows
    # already carry tenant_id='default' (the P1 column DEFAULT) so the new indexes build
    # cleanly. set_value()'s ON CONFLICT target is updated in lockstep below. OFF
    # byte-identical: with one tenant a (tenant_id, …) UNIQUE rejects a duplicate exactly
    # as before. APPEND-ONLY — keep at END; idempotent (DROP … IF EXISTS re-runnable).
    "DROP INDEX IF EXISTS ux_field_values_fs",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_field_values_tfs "
    "ON field_values(tenant_id, field_id, subject_ref)",
    "DROP INDEX IF EXISTS ux_tag_links_ts",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_links_tts "
    "ON tag_links(tenant_id, tag_id, subject_ref)",
]

_SCHEMA_READY = set()   # DB files whose schema is set up this process


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "metadata", _MIGRATIONS)
        audit.install_audit(con, ["custom_fields", "field_values", "tags", "tag_links"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ============================================================ type coercion / validation
def _coerce_value(ftype, options, raw):
    """Validate + coerce `raw` for a field of type `ftype`. Returns (stored_text, "")
    on success or (None, error_message) — NEVER raises. `options` is the parsed list
    for a select. The stored form is always TEXT (the canonical string for that type).

      text         -> trimmed string (empty allowed).
      number       -> a parseable number, stored canonically ("12", "12.5").
      monetary     -> money.f2 (Decimal, ROUND_HALF_UP), stored as "123.45".
      date         -> ISO "YYYY-MM-DD" (several input formats accepted).
      boolean      -> "0" / "1" (truthy strings recognised).
      select       -> must equal one of `options`; otherwise rejected.
      documentlink -> a free-text subject_ref (trimmed, non-empty).
    """
    s = "" if raw is None else str(raw).strip()
    try:
        if ftype == "text":
            return s, ""
        if ftype == "number":
            if s == "":
                return None, "a number is required"
            try:
                f = float(s)
            except (TypeError, ValueError):
                return None, f"{raw!r} is not a number"
            # canonical: keep integers integral, drop trailing-zero noise otherwise.
            return (str(int(f)) if f == int(f) else repr(f)), ""
        if ftype == "monetary":
            if s == "":
                return None, "an amount is required"
            try:
                float(s)
            except (TypeError, ValueError):
                return None, f"{raw!r} is not a valid amount"
            return f"{money.f2(s):.2f}", ""
        if ftype == "date":
            iso = _normalise_date(s)
            if iso is None:
                return None, f"{raw!r} is not a recognisable date (use YYYY-MM-DD)"
            return iso, ""
        if ftype == "boolean":
            return ("1" if _truthy(s) else "0"), ""
        if ftype == "select":
            opts = options or []
            if s not in opts:
                return None, f"{raw!r} is not one of the allowed options"
            return s, ""
        if ftype == "documentlink":
            if not s:
                return None, "a linked document reference is required"
            return s, ""
        return None, f"unknown field type {ftype!r}"
    except Exception as e:   # belt-and-braces — coercion must never raise
        log.warning("_coerce_value(%r) failed for %r: %s", ftype, raw, e)
        return None, "could not store that value"


def _truthy(s):
    return str(s).strip().lower() in ("1", "true", "yes", "on", "y", "t", "checked")


def _normalise_date(s):
    """Parse a date in a few common formats to ISO 'YYYY-MM-DD', or None."""
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def format_value(ftype, value):
    """Render a stored TEXT value for DISPLAY. Monetary is formatted via money (2dp);
    boolean as Yes/No; everything else as-is. Never raises -> the raw value on error."""
    try:
        if value is None:
            return ""
        if ftype == "monetary":
            return f"{money.f2(value):,.2f}"
        if ftype == "boolean":
            return "Yes" if str(value) == "1" else "No"
        return str(value)
    except Exception as e:
        log.warning("format_value(%r,%r) failed: %s", ftype, value, e)
        return str(value)


# ============================================================ custom fields
def _parse_options(options):
    """Normalise a select field's options into a list of non-empty strings. Accepts a
    JSON list, a list/tuple, or a newline/comma-separated string."""
    if not options:
        return []
    if isinstance(options, (list, tuple)):
        items = options
    else:
        s = str(options).strip()
        if s.startswith("["):
            try:
                items = json.loads(s)
            except Exception:
                items = []
        else:
            items = [p for p in s.replace(",", "\n").split("\n")]
    out = []
    for x in items:
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    return out


def define_field(name, ftype="text", options=None):
    """Define a custom field. Returns (field_dict, "") or (None, error). Never raises.
    `options` is required for a select (a JSON/list/comma-or-newline string)."""
    name = (name or "").strip()
    if not name:
        return None, "a field name is required"
    if ftype not in FIELD_TYPES:
        return None, f"unknown field type {ftype!r}"
    opts = _parse_options(options) if ftype == "select" else []
    if ftype == "select" and not opts:
        return None, "a select field needs at least one option"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO custom_fields (name, type, options, tenant_id)
                   VALUES (?,?,?,?)""",
                (name, ftype, json.dumps(opts) if opts else None,
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM custom_fields WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (_field_dict(row) if row else None), ""
    except Exception as e:
        log.exception("define_field failed for name=%r", name)
        return None, f"could not define field ({str(e)[:80]})"


def _field_dict(row):
    """Row -> dict with `options` parsed back into a list."""
    d = dict(row)
    d["options"] = _parse_options(d.get("options"))
    return d


def list_fields():
    """All custom fields (by name), each with `options` as a list. Never raises -> []."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT * FROM custom_fields WHERE 1=1" + frag
                + " ORDER BY name, id", tp).fetchall()
        finally:
            con.close()
        return [_field_dict(r) for r in rows]
    except Exception as e:
        log.warning("list_fields failed: %s", e)
        return []


def get_field(field_id):
    """Return a field dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM custom_fields WHERE id=?" + frag,
                              [field_id, *tp]).fetchone()
        finally:
            con.close()
        return _field_dict(row) if row else None
    except Exception as e:
        log.warning("get_field failed for %s: %s", field_id, e)
        return None


def delete_field(field_id):
    """Delete a field AND its stored values. Returns (True, "") or (False, error).
    Idempotent, best-effort, never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute("DELETE FROM field_values WHERE field_id=?" + frag,
                        [field_id, *tp])
            con.execute("DELETE FROM custom_fields WHERE id=?" + frag,
                        [field_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("delete_field failed for %s", field_id)
        return False, f"could not delete field ({str(e)[:80]})"


# ============================================================ field values
def set_value(field_id, subject_ref, raw):
    """Set (upsert) the value of `field_id` for the document `subject_ref`, validating
    + coercing `raw` per the field's type. Returns (True, "") or (False, error). Never
    raises. A value that fails its type's validation is rejected with the error, nothing
    stored. An empty value for a text field clears it (stores '')."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return False, "a document reference is required"
    field = get_field(field_id)
    if field is None:
        return False, "no such field"
    stored, err = _coerce_value(field["type"], field.get("options"), raw)
    if err:
        return False, err
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO field_values (field_id, subject_ref, value, tenant_id)
                   VALUES (?,?,?,?)
                   ON CONFLICT(tenant_id, field_id, subject_ref)
                   DO UPDATE SET value=excluded.value""",
                (field_id, subject_ref, stored, tenancy.write_tenant()))
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("set_value failed for field %s subject %r", field_id, subject_ref)
        return False, f"could not store value ({str(e)[:80]})"


def clear_value(field_id, subject_ref):
    """Remove a field's value for a subject. Returns (True, ""). Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute("DELETE FROM field_values WHERE field_id=? AND subject_ref=?" + frag,
                        [field_id, (subject_ref or "").strip(), *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("clear_value failed for field %s subject %r", field_id, subject_ref)
        return False, f"could not clear value ({str(e)[:80]})"


def get_values(subject_ref):
    """All custom-field values set on `subject_ref`, joined to their field definition.
    Returns a list of dicts:
        {field_id, name, type, options, value, display}
    `display` is format_value(type, value). Newest field name order. Never raises -> []."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return []
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("v.tenant_id")
            rows = con.execute(
                """SELECT v.field_id, v.value, f.name, f.type, f.options
                   FROM field_values v JOIN custom_fields f ON f.id=v.field_id
                   WHERE v.subject_ref=?""" + frag + " ORDER BY f.name, f.id",
                [subject_ref, *tp]).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            ftype = r["type"]
            out.append({
                "field_id": r["field_id"], "name": r["name"], "type": ftype,
                "options": _parse_options(r["options"]),
                "value": r["value"],
                "display": format_value(ftype, r["value"])})
        return out
    except Exception as e:
        log.warning("get_values failed for %r: %s", subject_ref, e)
        return []


def subjects_for_field_value(field_id, value):
    """All subject_refs whose `field_id` value equals `value` (raw stored form). Used by
    the document filter. Never raises -> []."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT subject_ref FROM field_values WHERE field_id=? AND value=?" + frag,
                [field_id, value, *tp]).fetchall()
        finally:
            con.close()
        return [r["subject_ref"] for r in rows]
    except Exception as e:
        log.warning("subjects_for_field_value failed: %s", e)
        return []


# ============================================================ tags (hierarchical)
def create_tag(name, parent_id=None, color=None):
    """Create a tag, optionally nested under `parent_id`. Returns (tag_dict, "") or
    (None, error). Never raises. A parent that does not exist is rejected (so the tree
    stays connected); a self/ancestor parent is impossible on create (no id yet)."""
    name = (name or "").strip()
    if not name:
        return None, "a tag name is required"
    parent_id = _as_int_or_none(parent_id)
    if parent_id is not None and get_tag(parent_id) is None:
        return None, "no such parent tag"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO tags (name, parent_id, color, tenant_id)
                   VALUES (?,?,?,?)""",
                (name, parent_id, (color or "").strip() or None,
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM tags WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_tag failed for name=%r", name)
        return None, f"could not create tag ({str(e)[:80]})"


def get_tag(tag_id):
    """Return a tag row dict by id, or None. Never raises."""
    tag_id = _as_int_or_none(tag_id)
    if tag_id is None:
        return None
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM tags WHERE id=?" + frag,
                              [tag_id, *tp]).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_tag failed for %s: %s", tag_id, e)
        return None


def _all_tags():
    """Raw list of every tag row dict. Never raises -> []."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT * FROM tags WHERE 1=1" + frag + " ORDER BY name, id", tp).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("_all_tags failed: %s", e)
        return []


def list_tags():
    """The tag hierarchy as a TREE. Returns a list of root tag dicts, each with a
    nested `children` list (recursively), ordered by name. A tag whose parent is
    missing/broken is surfaced as a root (never dropped). Never raises -> []."""
    tags = _all_tags()
    by_id = {t["id"]: dict(t, children=[]) for t in tags}
    roots = []
    for t in tags:
        node = by_id[t["id"]]
        pid = t.get("parent_id")
        if pid is not None and pid in by_id and pid != t["id"]:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)
    def _sort(nodes):
        nodes.sort(key=lambda n: (str(n.get("name") or "").lower(), n["id"]))
        for n in nodes:
            _sort(n["children"])
    _sort(roots)
    return roots


def flat_tags():
    """Every tag as a flat list with an indented display `path` (e.g. "Tax / VAT / EU"),
    in tree order. Handy for a <select>/checkbox list. Never raises -> []."""
    out = []
    def _walk(nodes, depth, prefix):
        for n in nodes:
            path = (prefix + " / " + n["name"]) if prefix else n["name"]
            out.append({"id": n["id"], "name": n["name"], "color": n.get("color"),
                        "parent_id": n.get("parent_id"), "depth": depth, "path": path})
            _walk(n["children"], depth + 1, path)
    _walk(list_tags(), 0, "")
    return out


def _ancestors(con, tag_id):
    """The set of ancestor ids of `tag_id` (walking parent_id up). Cycle-safe (a visited
    set stops an already-corrupt loop)."""
    frag, tp = tenancy.scope_clause("tenant_id")
    seen = set()
    cur = tag_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        row = con.execute("SELECT parent_id FROM tags WHERE id=?" + frag,
                          [cur, *tp]).fetchone()
        cur = row["parent_id"] if row else None
    seen.discard(tag_id)
    return seen


def _would_cycle(con, tag_id, new_parent_id):
    """True iff re-parenting `tag_id` under `new_parent_id` would create a cycle — i.e.
    new_parent is the tag itself or one of its DESCENDANTS. We test by walking UP from
    new_parent: if we reach tag_id, it is a descendant of tag_id and a cycle results."""
    if new_parent_id is None:
        return False
    if new_parent_id == tag_id:
        return True
    frag, tp = tenancy.scope_clause("tenant_id")
    cur = new_parent_id
    seen = set()
    while cur is not None and cur not in seen:
        if cur == tag_id:
            return True
        seen.add(cur)
        row = con.execute("SELECT parent_id FROM tags WHERE id=?" + frag,
                          [cur, *tp]).fetchone()
        cur = row["parent_id"] if row else None
    return False


def rename_tag(tag_id, name=None, color=None, parent_id="__keep__"):
    """Rename / recolor / re-parent a tag. Returns (True, "") or (False, error). Never
    raises. Re-parenting is GUARDED against cycles (a tag can't become its own ancestor /
    be moved under one of its own descendants); such a move is refused. `parent_id` left
    at the sentinel keeps the current parent; pass None to make it a root."""
    tag = get_tag(tag_id)
    if tag is None:
        return False, "no such tag"
    new_name = tag["name"] if name is None else (name or "").strip()
    if not new_name:
        return False, "a tag name is required"
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            if parent_id == "__keep__":
                new_parent = tag.get("parent_id")
            else:
                new_parent = _as_int_or_none(parent_id)
                if new_parent is not None and con.execute(
                        "SELECT 1 FROM tags WHERE id=?" + frag,
                        [new_parent, *tp]).fetchone() is None:
                    return False, "no such parent tag"
                if _would_cycle(con, tag_id, new_parent):
                    return False, "that move would create a tag cycle"
            new_color = tag.get("color") if color is None else ((color or "").strip() or None)
            con.execute("UPDATE tags SET name=?, color=?, parent_id=? WHERE id=?" + frag,
                        [new_name, new_color, new_parent, tag_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("rename_tag failed for %s", tag_id)
        return False, f"could not update tag ({str(e)[:80]})"


def delete_tag(tag_id):
    """Delete a tag, its links, and re-parent its CHILDREN up to its own parent (so the
    subtree is not orphaned). Returns (True, "") or (False, error). Never raises."""
    tag = get_tag(tag_id)
    if tag is None:
        return True, ""   # idempotent
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute("UPDATE tags SET parent_id=? WHERE parent_id=?" + frag,
                        [tag.get("parent_id"), tag_id, *tp])
            con.execute("DELETE FROM tag_links WHERE tag_id=?" + frag, [tag_id, *tp])
            con.execute("DELETE FROM tags WHERE id=?" + frag, [tag_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("delete_tag failed for %s", tag_id)
        return False, f"could not delete tag ({str(e)[:80]})"


# ============================================================ tag links
def assign_tag(tag_id, subject_ref):
    """Tag the document `subject_ref` with `tag_id` (idempotent). Returns (True, "") or
    (False, error). Never raises."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return False, "a document reference is required"
    if get_tag(tag_id) is None:
        return False, "no such tag"
    try:
        con = connect()
        try:
            con.execute(
                """INSERT OR IGNORE INTO tag_links (tag_id, subject_ref, tenant_id)
                   VALUES (?,?,?)""",
                (tag_id, subject_ref, tenancy.write_tenant()))
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("assign_tag failed for tag %s subject %r", tag_id, subject_ref)
        return False, f"could not assign tag ({str(e)[:80]})"


def unassign_tag(tag_id, subject_ref):
    """Remove a tag from a document. Returns (True, ""). Idempotent, never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute("DELETE FROM tag_links WHERE tag_id=? AND subject_ref=?" + frag,
                        [tag_id, (subject_ref or "").strip(), *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("unassign_tag failed for tag %s subject %r", tag_id, subject_ref)
        return False, f"could not unassign tag ({str(e)[:80]})"


def tags_for(subject_ref):
    """All tags assigned to `subject_ref`, as dicts (id, name, parent_id, color),
    ordered by name. Never raises -> []."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return []
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("l.tenant_id")
            rows = con.execute(
                """SELECT t.id, t.name, t.parent_id, t.color
                   FROM tag_links l JOIN tags t ON t.id=l.tag_id
                   WHERE l.subject_ref=?""" + frag + " ORDER BY t.name, t.id",
                [subject_ref, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("tags_for failed for %r: %s", subject_ref, e)
        return []


def subjects_for_tag(tag_id, include_descendants=False):
    """All subject_refs tagged with `tag_id`. With `include_descendants` the result also
    covers documents tagged with any DESCENDANT of `tag_id` (the natural hierarchical
    filter — "everything under Tax"). Returns a sorted list. Never raises -> []."""
    tag_id = _as_int_or_none(tag_id)
    if tag_id is None:
        return []
    try:
        con = connect()
        try:
            ids = {tag_id}
            if include_descendants:
                ids |= _descendant_ids(con, tag_id)
            qmarks = ",".join("?" for _ in ids)
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                f"SELECT DISTINCT subject_ref FROM tag_links WHERE tag_id IN ({qmarks})"
                + frag, [*ids, *tp]).fetchall()
        finally:
            con.close()
        return sorted(r["subject_ref"] for r in rows)
    except Exception as e:
        log.warning("subjects_for_tag failed for %s: %s", tag_id, e)
        return []


def _descendant_ids(con, tag_id):
    """The set of all descendant tag ids of `tag_id` (cycle-safe BFS)."""
    frag, tp = tenancy.scope_clause("tenant_id")
    out = set()
    frontier = [tag_id]
    while frontier:
        nxt = []
        for pid in frontier:
            for r in con.execute("SELECT id FROM tags WHERE parent_id=?" + frag,
                                 [pid, *tp]):
                cid = r["id"]
                if cid not in out and cid != tag_id:
                    out.add(cid)
                    nxt.append(cid)
        frontier = nxt
    return out


# ============================================================ search integration helper
def index_text(subject_ref):
    """The metadata of `subject_ref` rendered as a single searchable text blob (tag names
    + tag paths + custom-field values/names), for the A2 search index to fold into a
    document's body. Best-effort: empty metadata -> "" and any failure -> "" (search must
    never break on metadata). Never raises."""
    try:
        parts = []
        for t in tags_for(subject_ref):
            n = (t.get("name") or "").strip()
            if n:
                parts.append(n)
        for v in get_values(subject_ref):
            n = (v.get("name") or "").strip()
            disp = (v.get("display") or "").strip()
            if n:
                parts.append(n)
            if disp:
                parts.append(disp)
        return " ".join(parts)
    except Exception as e:
        log.warning("index_text failed for %r: %s", subject_ref, e)
        return ""


def _as_int_or_none(v):
    if v in (None, "", "__keep__"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    # offline smoke (uses the live metadata.db): define -> set -> tag -> filter.
    f, err = define_field("Amount due", "monetary")
    print("field:", (f or {}).get("id"), err or "ok")
    if f:
        ok, e = set_value(f["id"], "doc:1", "12.005")
        print("set_value:", ok, e, "->", get_values("doc:1"))
    root, _ = create_tag("Tax")
    child, _ = create_tag("VAT", parent_id=(root or {}).get("id"))
    if child:
        assign_tag(child["id"], "doc:1")
        print("tags_for doc:1:", [t["name"] for t in tags_for("doc:1")])
        print("subjects under Tax:", subjects_for_tag(root["id"], include_descendants=True))
