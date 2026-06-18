"""
DATA-ROOT RESOLVER — one redirectable home for the demo/product data DBs.

Every module that opens one of the five demo data DBs (customers.db, suppliers.db,
fuel_history.db, vat_claims.db, benchmark.db) resolves its on-disk path THROUGH
HERE, AT CALL TIME, so the directory can be redirected with a single environment
variable. With FFS_DATA_DIR unset this is byte-identical to the historical
``os.path.join(WORKDIR, name)`` behaviour (the repo dir); set it (e.g. a per-test
fixture) and every resolution point follows, giving each test its own fresh copies
with no file-swap-under-open-connection hazard.

Deliberately tiny and dependency-free. It governs ONLY the data-DB root, NOT
security.db (managed separately) nor any other app-owned runtime DB.
"""
import os

_REPO = os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Root dir for the data DBs. Honors FFS_DATA_DIR (per-test isolation); else repo."""
    return os.environ.get("FFS_DATA_DIR") or _REPO


def db_path(name):
    return os.path.join(data_dir(), name)
