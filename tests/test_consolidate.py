"""
Engine smoke test: consolidate.py must map and reconcile every supplier against
its invoice totals. This replaces the inline `python consolidate.py` convention.
"""
import os
import subprocess
import sys

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPLIERS = ["Q8", "BP", "TFC", "E100", "MOEVE", "DKV"]


def test_consolidate_passes_all_suppliers():
    proc = subprocess.run(
        [sys.executable, os.path.join(WORKDIR, "consolidate.py")],
        cwd=WORKDIR, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"consolidate.py failed:\n{out}"
    for sup in SUPPLIERS:
        assert f"PASS {sup}:" in out, f"{sup} did not PASS:\n{out}"
    assert "VALIDATION FAILURES" not in out
    assert "Consolidated" in out


def test_norm_date_formats():
    sys.path.insert(0, WORKDIR)
    # norm_date is defined in consolidate.py, which executes on import; exercise
    # it through a tiny subprocess so we don't depend on import side effects.
    code = (
        "import consolidate as c;"
        "assert c.norm_date('2026-05-31')=='2026-05-31';"
        "assert c.norm_date('31/05/26')=='2026-05-31';"
        "assert c.norm_date('31-05-26')=='2026-05-31';"
        "print('ok')"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=WORKDIR,
                          capture_output=True, text=True)
    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stderr
