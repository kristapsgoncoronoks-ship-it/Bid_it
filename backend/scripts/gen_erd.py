"""Generate the domain-sharded ER diagrams from the SQLAlchemy models.

Why generated: diagram rot is the empirically dominant architecture-docs
failure mode (Fraunhofer IESE survey, n=147 — outdatedness is the most
reported problem), and an ER diagram of a 105-table schema is exactly the
artifact a human will never keep true by hand. So the diagram is DERIVED from
`Base.metadata` — the same source the running app uses — and
`tests/test_erd_truth.py` fails the backend CI job whenever the committed
file drifts from the models (the same inject+check pattern Paracelsus uses,
implemented in-repo so the gate needs no new dependency).

One 105-table diagram is illegible, so the output is SHARDED by domain: each
domain gets its own Mermaid `erDiagram` (GitHub renders these natively) with
the FK edges whose BOTH ends live in the shard; edges that cross domains are
listed under the diagram as text — a cross-domain FK is exactly the coupling
worth reading as a list, not squinting at as a line.

Usage (from backend/):
    python scripts/gen_erd.py          # rewrite docs/architecture/data-model-erd.md
    python scripts/gen_erd.py --check  # exit 1 if the committed file has drifted
"""

# ruff: noqa: T201 — a CLI tool talks through stdout
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

OUT = BACKEND.parent / "docs" / "architecture" / "data-model-erd.md"

# Ordered: the FIRST matching rule claims the table. Patterns are matched with
# re.fullmatch against the table name.
DOMAINS: list[tuple[str, list[str]]] = [
    (
        "Transport VAT recovery",
        [r"vat_.*", r"fuel_.*", r"supplier_vat_registrations"],
    ),
    (
        "Received invoices (AP) & capture",
        [
            r"invoices",
            r"line_items",
            r"vendors",
            r"vendor_change_requests",
            r"extraction_.*",
            r"inbound_invoices",
            r"email_intake.*",
            r"capture_.*",
            r"archived_invoices",
            r"invoice_collab.*",
            r"inbound_channel_health",
        ],
    ),
    (
        "Issued invoices (AR) & partners",
        [
            r"issued_.*",
            r"recurring_invoices",
            r"issuer_profiles",
            r"partners",
            r"partner_documents",
            r"dunning_policies",
            r"receipts",
            r"payments",
            r"customers",
            r"customer_contacts",
            r"customer_notes",
            r"customer_portal_tokens",
            r"offer_stage_events",
        ],
    ),
    (
        "Payments & settlement",
        [
            r"payment_runs",
            r"supplier_payments",
            r"reimbursement_batches",
            r"bank_statements",
            r"bank_lines",
        ],
    ),
    (
        "Expenses",
        [r"expense_.*"],
    ),
    (
        "Projects & scheduling",
        [
            r"projects",
            r"invoice_project_splits",
            r"project_.*",
            r"invoicing_plan_rows",
            r"org_templates",
            r"platform_templates",
            r"assignments",
            r"org_deadlines",
            r"calendar_feed_tokens",
            r"action_dismissals",
        ],
    ),
    (
        "Automation",
        [r"automation_.*"],
    ),
    (
        "Analytics & catalogs",
        [
            r"budget_targets",
            r"tax_codes",
            r"currencies",
            r"departments",
            r"cost_centers",
            r"ecb_rates",
            r"supplier_agreed_prices",
        ],
    ),
    (
        "Identity & tenancy",
        [
            r"organizations",
            r"users",
            r"memberships",
            r"invitations",
            r"sessions",
            r"sso_connections",
            r"auth_tokens",
            r"email_tokens",
        ],
    ),
    # Everything else — jobs, audit, billing, webhooks, retention, documents…
    ("Platform & compliance", [r".*"]),
]


def _domain_of(table: str) -> str:
    for name, patterns in DOMAINS:
        if any(re.fullmatch(p, table) for p in patterns):
            return name
    raise AssertionError(f"unreachable: {table}")  # the catch-all matches all


def generate() -> str:
    import app.main  # noqa: F401, PLC0415 — imports every model module
    from app.models.base import Base  # noqa: PLC0415 — after sys.path insert

    tables = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
    by_domain: dict[str, list] = {name: [] for name, _ in DOMAINS}
    for t in tables:
        by_domain[_domain_of(t.name)].append(t)

    lines: list[str] = [
        "# InvoiceIQ — Generated ER Diagrams (by domain)",
        "",
        "> **GENERATED FILE — do not edit by hand.** Derived from the live",
        "> SQLAlchemy metadata by `backend/scripts/gen_erd.py`; the backend CI",
        "> job fails when this file drifts from the models",
        "> (`tests/test_erd_truth.py`). Regenerate with:",
        "> `cd backend && python scripts/gen_erd.py`.",
        ">",
        "> One diagram per domain — the full schema in one picture would be",
        "> illegible. Edges shown inside a diagram are foreign keys whose both",
        "> ends are in the domain; **cross-domain foreign keys** are listed as",
        "> text under each diagram (that coupling reads better as a list). The",
        "> tenancy FK — every tenant table → `organizations` via `org_id`, the",
        "> RLS/guard backbone — is universal and therefore never drawn.",
        "> Companion: [data-model](./data-model.md) (the annotated logical",
        "> model), [diagram-matrix](./diagram-matrix.md) (what we diagram and",
        "> why).",
        "",
        f"_{len(tables)} tables across {len(DOMAINS)} domains._",
        "",
    ]

    for name, _ in DOMAINS:
        members = by_domain[name]
        if not members:
            continue
        member_names = {t.name for t in members}
        lines += [f"## {name} ({len(members)} tables)", "", "```mermaid", "erDiagram"]
        intra: list[str] = []
        cross: list[str] = []
        for t in members:
            for fkc in t.foreign_key_constraints:
                target = fkc.referred_table.name
                if target == t.name:
                    continue  # self-references clutter more than they explain
                # The tenancy FK is universal (every tenant table → organizations
                # via org_id) — stated once in the preamble, never drawn.
                cols = [c.name for c in fkc.columns if c.name != "org_id"]
                if not cols:
                    continue
                label = ",".join(cols)
                if target in member_names:
                    intra.append(f'  {target} ||--o{{ {t.name} : "{label}"')
                else:
                    cross.append(f"- `{t.name}.{label}` → `{target}` ({_domain_of(target)})")
        connected = {ln.split()[0] for ln in intra} | {ln.split()[2] for ln in intra}
        for t in members:  # entities with no intra-domain edge still appear
            if t.name not in connected:
                lines.append(f"  {t.name} {{\n  }}".replace("\n  ", "\n  "))
        lines += sorted(set(intra))
        lines += ["```", ""]
        if cross:
            lines.append("Cross-domain foreign keys:")
            lines += sorted(set(cross))
            lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    content = generate()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != content:
            print(
                "data-model-erd.md has drifted from the SQLAlchemy models.\n"
                "Regenerate: cd backend && python scripts/gen_erd.py"
            )
            return 1
        print("data-model-erd.md matches the models.")
        return 0
    OUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUT} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
