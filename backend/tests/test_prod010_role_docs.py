"""PROD-010 (audit 2026-09-05) — the role vocabulary a person sees is the one
the server enforces.

The SPA showed "User-free" (a historical storage label, not a role anyone would
pick), "Admin" and "User", while the server resolves eight business roles with
a deny-by-default matrix; the manual's role table listed four tiers and no
counts. Three sources, one vocabulary now:

- `frontend/src/lib/roles.ts::ROLE_LABELS` — what the Team page and the SSO
  default-role picker show;
- `docs/MANUAL.md` §1.2 — the role grid a customer reads;
- `app.core.authz.ROLE_PERMISSIONS` — the truth.

The manual's "Permissions" column is a mechanical figure: the size of the
role's permission set. A matrix change that forgets the manual fails here.
"""

from __future__ import annotations

import pathlib
import re

from app.core import authz
from app.models.user import UserRole

REPO = pathlib.Path(__file__).resolve().parents[2]
MANUAL = REPO / "docs" / "MANUAL.md"
ROLES_TS = REPO / "frontend" / "src" / "lib" / "roles.ts"

# The stored value a member carries → the business role the matrix keys on.
STORED_TO_BUSINESS: dict[str, authz.Role] = {
    "owner": authz.Role.OWNER,
    "admin": authz.Role.ADMINISTRATOR,
    "user": authz.Role.EMPLOYEE,
    "user_free": authz.Role.READ_ONLY,
    "finance_manager": authz.Role.FINANCE_MANAGER,
    "accountant": authz.Role.ACCOUNTANT,
    "approver": authz.Role.APPROVER,
    "auditor": authz.Role.AUDITOR,
}

# The human names — one place, asserted against both the SPA and the manual.
LABELS: dict[str, str] = {
    "owner": "Owner",
    "admin": "Administrator",
    "finance_manager": "Finance manager",
    "accountant": "Accountant",
    "auditor": "Auditor",
    "user_free": "Read-only",
    "approver": "Approver",
    "user": "Employee",
}


def _manual_grid() -> dict[str, dict[str, str]]:
    """The §1.2 table, keyed by the stored role value."""
    text = MANUAL.read_text()
    section = text.split("### 1.2", 1)[1].split("### 1.3", 1)[0]
    rows: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        m = re.match(
            r"^\|\s*\*\*(?P<label>[^*]+)\*\*\s*\|\s*`(?P<stored>\w+)`\s*\|(?P<person>[^|]*)\|(?P<can>[^|]*)\|\s*(?P<count>\d+)\s*\|\s*$",
            line,
        )
        if m:
            rows[m["stored"]] = {k: m[k].strip() for k in ("label", "person", "can", "count")}
    return rows


def _spa_labels() -> dict[str, str]:
    src = ROLES_TS.read_text()
    block = re.search(r"ROLE_LABELS[^{]*\{(?P<body>.*?)\};", src, re.S)
    assert block, "ROLE_LABELS not found in roles.ts"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', block["body"]))


def test_the_stored_role_values_are_exactly_the_eight_the_model_carries():
    assert set(STORED_TO_BUSINESS) == {r.value for r in UserRole}
    assert set(STORED_TO_BUSINESS.values()) == set(authz.Role)
    for stored, role in STORED_TO_BUSINESS.items():
        assert authz.business_role(type("U", (), {"role": stored})()) is role, stored


def test_the_spa_labels_every_stored_role_by_its_business_name():
    spa = _spa_labels()
    assert spa == LABELS, {
        k: (spa.get(k), LABELS.get(k))
        for k in set(spa) | set(LABELS)
        if spa.get(k) != LABELS.get(k)
    }
    assert "User-free" not in ROLES_TS.read_text()


def test_the_manual_grid_lists_every_role_with_its_label_and_true_permission_count():
    grid = _manual_grid()
    assert set(grid) == set(STORED_TO_BUSINESS), (
        f"MANUAL §1.2 lists {sorted(grid)}; the model carries {sorted(STORED_TO_BUSINESS)}"
    )
    assert "User-free" not in MANUAL.read_text()
    for stored, row in grid.items():
        role = STORED_TO_BUSINESS[stored]
        perms = authz.ROLE_PERMISSIONS[role]
        assert row["label"] == LABELS[stored], (stored, row["label"])
        assert int(row["count"]) == len(perms), (
            f"MANUAL says {LABELS[stored]} holds {row['count']} permissions; the matrix grants {len(perms)}"
        )
        # A role that can change nothing must SAY so; one that can must not.
        mutating = {p for p in perms if not p.value.endswith(".read") and p.value != "report.read"}
        read_only_claim = "never changes anything" in row["can"].lower()
        if not mutating:
            assert read_only_claim, f"{LABELS[stored]} can change nothing — the manual must say so"
        else:
            assert not read_only_claim or mutating <= {authz.Permission.EXPORT_RUN}, (
                f"{LABELS[stored]} claims to change nothing but holds {sorted(p.value for p in mutating)}"
            )
    # The rows that mention billing are exactly the ones that hold it.
    for stored, row in grid.items():
        holds = (
            authz.Permission.BILLING_MANAGE in authz.ROLE_PERMISSIONS[STORED_TO_BUSINESS[stored]]
        )
        says = bool(re.search(r"(?<!except )(?<!or )billing", row["can"]))
        assert holds == says, (stored, row["can"])


def test_the_matrix_size_the_manual_quotes_is_the_real_one():
    section = MANUAL.read_text().split("### 1.2", 1)[1].split("### 1.3", 1)[0]
    m = re.search(r"out of the\s+matrix's (\d+)", section)
    assert m, "the §1.2 preamble no longer quotes the matrix size"
    assert int(m.group(1)) == len(authz.ALL_PERMISSIONS)
