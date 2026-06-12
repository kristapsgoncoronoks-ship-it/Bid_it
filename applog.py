"""
LOGGING LAYER — one place to get a configured logger, replacing silent `except: pass`
guards with a recorded trace.

    import applog
    log = applog.get(__name__)
    log.warning("schema migration skipped: %s", e)

Writes to logs/app.log (rotating, 1 MB × 5) and, for WARNING+, to stderr. Import-safe:
never raises if the log directory can't be created (falls back to stderr only).
"""
import logging
import logging.handlers
import os

WORKDIR = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.environ.get("FFS_LOGDIR", os.path.join(WORKDIR, "logs"))
_configured = False


def _configure():
    global _configured
    if _configured:
        return
    root = logging.getLogger("ffs")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(LOGDIR, "app.log"), maxBytes=1_000_000, backupCount=5,
            encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass                                    # no writable disk -> stderr only
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    _configured = True


def get(name="app"):
    """A namespaced logger under the shared 'ffs' hierarchy."""
    _configure()
    return logging.getLogger(f"ffs.{name}")
