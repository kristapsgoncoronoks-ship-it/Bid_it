# Deprecation warnings — inventory and ratchet (2026-09-08, reference R4, FLASK-P3-01)

The Flask cycle of the reference archive orders `filterwarnings = error`: a
deprecation the suite can see is a defect with a date on it, and a blanket
`ignore` is how a codebase arrives at a dependency major it cannot install.
`backend/pytest.ini` had `ignore::DeprecationWarning` — every deprecation the
3,266 tests could raise was silenced wholesale. The order was ADAPTED, not
flipped blindly: inventory first, then the exact policy the inventory justifies.

## 1. Inventory

Two measurements, both on the R4 tree, Python 3.11.15, the pinned
`requirements.txt` (pydantic 2.11, SQLAlchemy 2.0, pytest 9.1):

| Measurement | Command | Result |
|---|---|---|
| The full backend suite with the ignore overridden | `python -m pytest -q -W default::DeprecationWarning -W default::PendingDeprecationWarning` (worktree, 55:44) | **3246 passed / 20 skipped / 1 warning** |
| Import-time deprecations of the application | `python -W default -c "import app.main"` | **none** |

The one warning, verbatim from the summary:

```
tests/test_p2_batch4_perf.py::test_perf017_gen2_threshold_is_applied_at_startup_and_none_leaves_the_interpreter_alone
  tests/test_p2_batch4_perf.py:365: PydanticDeprecatedSince211: Accessing the 'model_fields'
  attribute on the instance is deprecated. Instead, you should access this attribute from the
  model class. Deprecated in Pydantic V2.11 to be removed in V3.0.
    assert settings.model_fields["gc_gen2_threshold"].default == 100
```

Classification: first-party, in a TEST, one line — `settings.model_fields` on
the instance instead of the class. Fixed (`type(settings).model_fields`). No
third-party deprecation reached the suite at all: SQLAlchemy 2.0, FastAPI,
httpx, asyncpg, aiosqlite, pytest-asyncio and Pydantic's own runtime raise
none on these pins.

## 2. Policy

`backend/pytest.ini` now reads:

```
filterwarnings =
    error::DeprecationWarning
    error::PendingDeprecationWarning
```

Both categories are errors for every source, first- and third-party. The
inventory justifies the strict form: with zero third-party deprecations on the
current pins, a per-source `ignore` list would be a list of nothing. The Flask
pattern — `error` plus a REASONED `ignore` per known upstream deprecation —
is what to do when a dependency bump introduces one: add the exact
`ignore:<message regex>:<category>:<module>` line with the upstream reference,
never the blanket line that was removed.

Seeded proof: with the new filter and the test line unfixed, the file reads
`1 failed, 5 passed` (`PydanticDeprecatedSince211` raised as an error); with
the line fixed, 6 passed. The `test_docs_truth.py` suite pins nothing here —
the policy is enforced by pytest itself on every run, locally and in CI's
`backend` job.

## 3. What the inventory does NOT cover (say it, do not assume it)

- **Python 3.11 only.** The venv and every CI job run 3.11; deprecations that
  3.12/3.13 add (for example `datetime.utcnow`, which `backend/app` calls
  once) do not appear until the interpreter moves. The interpreter bump is
  its own change and re-runs this inventory first.
- **Subprocess and thread warnings.** The `slow` tests shell out to Alembic;
  warnings raised in that process, or in worker threads, are not in pytest's
  summary. The Alembic path is covered separately by CI's migration-consistency
  step running under the same interpreter.
- **Runtime-only paths** no test executes (a warning is only visible where a
  test walks). The import-time run closes the module-load half of that gap.

## 4. Dependency bumps under this policy

A Dependabot bump that starts emitting a `DeprecationWarning` now turns the
suite red instead of silently accumulating debt. That is the intended
behaviour: triage the message, either fix the call site or add the reasoned
`ignore` line with the upstream issue, in the same pull request as the bump.
The 7-day Dependabot cooldown (reference R3) keeps the churn bounded.
