"""PROD-003 (audit 2026-09-05) — the navigation is drawn from what the API
will serve, not from a ladder that disagreed with it.

The SPA's nav used to gate items on the 4-tier stored-role ladder
(`admin`/`owner` flags) while every router gates on the 8-role permission
matrix. Six roles therefore saw dead links (an EMPLOYEE saw Upload and Team,
both 403) and missed live ones (a FINANCE_MANAGER holds `audit.read` and
`expense.approve` but saw neither Audit log nor Reimbursements).

Three things hold it now:

1. every identity response (`/auth/me`, login, register, accept-invite)
   carries the caller's effective permissions — exactly `authz.permissions_for`;
2. every nav item in `frontend/src/lib/nav.ts` declares the permission its
   destination's router requires, the value is a real `Permission`, and the
   ladder flags are gone;
3. the SPA's fallback mirror (`PERMISSIONS_BY_ROLE` in `lib/roles.ts`, used only
   when a response carries no `permissions`, i.e. an older API during a rolling
   deploy or an unmocked e2e fixture) equals the backend matrix role for role.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import authz
from app.models.user import User

REPO = Path(__file__).resolve().parents[2]
NAV_TS = REPO / "frontend" / "src" / "lib" / "nav.ts"
ROLES_TS = REPO / "frontend" / "src" / "lib" / "roles.ts"

PERMISSION_VALUES = {p.value for p in authz.Permission}


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_every_identity_response_carries_the_effective_permissions(client, db_session):
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Haulage Co",
            "name": "O",
            "email": "owner@haulage.example",
            "password": "supersecret",
        },
    )
    assert reg.status_code == 201, reg.text
    owner = await db_session.scalar(select(User).where(User.email == "owner@haulage.example"))
    expected = sorted(p.value for p in authz.permissions_for(owner))
    assert reg.json()["permissions"] == expected
    tok = reg.json()["token"]["access_token"]

    me = await client.get("/api/v1/auth/me", headers=_h(tok))
    assert me.json()["permissions"] == expected

    login = await client.post(
        "/api/v1/auth/login", json={"email": "owner@haulage.example", "password": "supersecret"}
    )
    assert login.status_code == 200, login.text
    assert login.json()["permissions"] == expected

    # A business role gets ITS matrix row, not the owner's.
    inv = await client.post(
        "/api/v1/team/invites",
        json={"email": "books@haulage.example", "role": "accountant"},
        headers=_h(tok),
    )
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "A", "password": "supersecret"},
    )
    assert acc.status_code in (200, 201), acc.text
    accountant_perms = acc.json()["permissions"]
    assert accountant_perms == sorted(
        p.value for p in authz.ROLE_PERMISSIONS[authz.Role.ACCOUNTANT]
    )
    assert "audit.read" not in accountant_perms and "invoice.write" in accountant_perms


def _nav_items() -> list[dict[str, str]]:
    """Each `{ to: "...", ... }` object literal in LIVE_NAV, as a flat dict of
    its string-valued keys (labels, `to`, `module`, `perm`) plus any bare flags."""
    src = NAV_TS.read_text(encoding="utf-8")
    body = src[src.index("export const LIVE_NAV") : src.index("export function matchNavItem")]
    items = []
    for m in re.finditer(r"\{\s*(?:(?://[^\n]*\n\s*)*)to:\s*\"([^\"]+)\"", body):
        start = m.start()
        depth = 0
        for k in range(start, len(body)):
            depth += body[k] == "{"
            depth -= body[k] == "}"
            if depth == 0:
                break
        literal = body[start : k + 1]
        literal = re.sub(r"//[^\n]*", "", literal)  # comments carry the word "admin"
        item = {"to": m.group(1)}
        for key, val in re.findall(r"\b(label|module|perm)\s*:\s*\"([^\"]*)\"", literal):
            item[key] = val
        for flag in re.findall(r"\b(admin|owner|end)\s*:\s*(?:true|false)", literal):
            item[flag] = "flag"
        items.append(item)
    return items


def test_every_nav_item_declares_a_real_permission_and_no_ladder_flag_remains():
    items = _nav_items()
    assert len(items) > 40, [i["to"] for i in items]  # the whole IA, not a fragment
    for item in items:
        assert "perm" in item, f"nav item {item['to']} declares no permission"
        assert item["perm"] in PERMISSION_VALUES, (item["to"], item["perm"])
        assert "admin" not in item and "owner" not in item, (
            f"nav item {item['to']} still carries a ladder flag; the nav is drawn from "
            "served permissions now (PROD-003)"
        )


def _ts_permissions_by_role() -> dict[str, list[str]]:
    """Read the mirror without a JS runtime. Three value shapes are allowed:
    an array literal, the `ALL_PERMISSIONS` constant, or `ALL_PERMISSIONS`
    minus a filtered-out value (the administrator row)."""
    src = ROLES_TS.read_text(encoding="utf-8")
    all_start = src.index("const ALL_PERMISSIONS = [")
    all_body = src[all_start + len("const ALL_PERMISSIONS = [") : src.index("];", all_start)]
    all_permissions = json.loads(f"[{all_body.strip().rstrip(',')}]")

    start = src.index("export const PERMISSIONS_BY_ROLE")
    block = src[start : src.index("\n};", start) + 3]
    out: dict[str, list[str]] = {}
    for role, expr in re.findall(
        r"\n  (\w+):\s*((?:\[[^\]]*\])|ALL_PERMISSIONS[^\n]*),?(?=\n)", block
    ):
        expr = expr.strip().rstrip(",")
        if expr.startswith("["):
            body = expr[1:-1].strip().rstrip(",")
            out[role] = sorted(json.loads(f"[{body}]")) if body else []
        elif expr == "ALL_PERMISSIONS":
            out[role] = sorted(all_permissions)
        else:
            excluded = re.findall(r"!==\s*\"([^\"]+)\"", expr)
            assert excluded, f"unreadable mirror expression for {role}: {expr}"
            out[role] = sorted(p for p in all_permissions if p not in excluded)
    return out


def test_the_spa_fallback_mirror_equals_the_backend_matrix_role_for_role():
    matrix = authz.matrix()
    # The SPA stores the legacy 4-tier names; the matrix speaks business roles.
    spa_to_matrix = {
        "owner": "organization_owner",
        "admin": "administrator",
        "user": "employee",
        "user_free": "read_only",
        "finance_manager": "finance_manager",
        "accountant": "accountant",
        "approver": "approver",
        "auditor": "auditor",
    }
    mirror = _ts_permissions_by_role()
    assert set(mirror) == set(spa_to_matrix), set(mirror) ^ set(spa_to_matrix)
    for spa_role, matrix_role in spa_to_matrix.items():
        assert mirror[spa_role] == sorted(matrix[matrix_role]), (
            f"frontend/src/lib/roles.ts PERMISSIONS_BY_ROLE[{spa_role}] has drifted from "
            f"authz.ROLE_PERMISSIONS[{matrix_role}]"
        )
