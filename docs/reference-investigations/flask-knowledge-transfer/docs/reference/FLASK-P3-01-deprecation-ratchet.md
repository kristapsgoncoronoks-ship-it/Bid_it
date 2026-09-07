# FLASK-P3-01 — Deprecation-warning ratchet

## Reference lesson

Flask runs pytest with `filterwarnings = ["error"]`. InvoiceIQ currently has:

```ini
filterwarnings =
    ignore::DeprecationWarning
```

That is too broad: it can hide framework / SQLAlchemy / Pydantic / Python
deprecations until an upgrade turns them into removals.

## Lead decision

**ADAPT, DO NOT BLINDLY FLIP.**

Before changing the suite to warnings-as-errors, run one full CI inventory:

```bash
cd backend
python -m pytest -W default::DeprecationWarning \
  2>&1 | tee deprecation-warnings.txt
```

Classify each distinct warning:

1. first-party InvoiceIQ deprecation → fix immediately;
2. dependency warning with an available fixed release → upgrade intentionally;
3. unavoidable third-party warning → add an exact module/message filter with an
   owner and removal issue;
4. warning caused only by a test fixture → fix the fixture.

Target configuration after the inventory:

```ini
filterwarnings =
    error
    # Exact, temporary third-party exceptions only. Never restore a blanket
    # ignore::DeprecationWarning.
```

## Acceptance

- no blanket DeprecationWarning ignore;
- every remaining warning exception is exact and documented;
- full backend suite green;
- future deprecations fail CI.

Status: **P3 / OWNER-FREE / NOT IMPLEMENTED YET because the warning inventory
cannot be truthfully produced without running InvoiceIQ's full environment.**
