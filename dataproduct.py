"""
DATA-PRODUCT READ ACCESSOR — the app's READ-ONLY window onto the engine-owned DBs.

The data-processing engine (consolidate → validate → build_master → history) OWNS
and WRITES the product databases:
    fuel_history.db   transactions + reporting views  (written by history.py)
    suppliers.db      supplier master                 (written by the master-data layer)

Everything on the *app* side that only needs to READ those products opens them
THROUGH HERE, with a read-only handle. A read-only handle is a boundary guard, not
just a performance setting: any stray INSERT/UPDATE from the web path raises
sqlite3.OperationalError instead of silently writing a DB the app does not own.

  • connect("fuel_history")  — read-only handle to fuel_history.db (the ~20 read
                               routes feed queries.py, which only SELECTs).
  • connect("suppliers")     — read-only handle to suppliers.db (future read callers).

NOT routed here (deliberately left read-write by their owning modules / later orders):
  • vat_refund.connect()        — vat_claims.db, app/compliance-owned (read-write).
  • pricing_intelligence.connect() — still WRITES my_prices/wholesale_prices into
                                     fuel_history.db; that write moves in D3.
  • register_statement / suppliers.db writes — move in D4.

Postgres seam: `_role()` documents where a Postgres cutover swaps to a read-only
ROLE/DSN. SQLite has no per-connection role, so read-only is expressed as a
`mode=ro` URI; Postgres would instead connect as a SELECT-only role (mirror db.py's
engine abstraction) WITHOUT changing any caller.
"""
import os
import sqlite3
import db
import db_tuning

WORKDIR = os.path.dirname(os.path.abspath(__file__))

# Logical product name -> on-disk file. Only engine-owned product DBs belong here.
_PATHS = {
    "fuel_history": f"{WORKDIR}/fuel_history.db",
    "suppliers":    f"{WORKDIR}/suppliers.db",
}


def _role():
    """The access role this accessor connects under. For SQLite this is always the
    read-only ('ro') intent expressed via a `mode=ro` URI. The seam exists so a
    Postgres cutover can return a read-only ROLE/DSN here and have connect() build a
    SELECT-only connection — without touching any caller. (Postgres support is the
    later cutover described in db.py; on SQLite the URI handles it.)"""
    return "ro"


def connect(which="fuel_history", path=None):
    """Open a READ-ONLY handle to an engine-owned product DB. Returns a connection
    with `row_factory = sqlite3.Row`. Any write (INSERT/UPDATE/DELETE/DDL) raises
    sqlite3.OperationalError — the app must not write a DB it does not own.

    `which` selects the logical product (and its on-disk path). `path` overrides the
    resolved file for a caller that already tracks its own location-independent path
    (e.g. vat_refund.ANALYTICS_DB, monkeypatched in tests) — the read-only window is
    the same, only the file differs.

    Tuning: a `mode=ro` handle cannot flip journal mode, so WAL is NOT requested
    (the engine writer in history.py owns the WAL setting); db_tuning.tune still
    applies the read-safe busy_timeout. tune() no-ops on Postgres."""
    if path is None:
        try:
            path = _PATHS[which]
        except KeyError:
            raise ValueError(f"unknown product DB {which!r}; expected one of {sorted(_PATHS)}")
    # Postgres cutover would branch on db.ENGINE here and connect under _role()'s
    # read-only DSN; on SQLite the read-only intent is the mode=ro URI.
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # Read-safe tuning only: no WAL (a read-only handle can't set journal mode and
    # the engine writer owns it); busy_timeout still helps under concurrent writers.
    db_tuning.tune(con, wal=False)
    return con
