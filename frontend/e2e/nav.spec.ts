import { test, expect, type Page } from "@playwright/test";

/**
 * The grouped navigation IA (WO-17 / I1.2) over the LIVE app shell, API mocked via
 * `page.route` — deterministic, no backend. Proves the three things ARCH_plan's
 * acceptance criterion names: the nav is grouped, permission-aware (admin/owner
 * flags) and module-aware (add-on modules), plus the shared shell behaviours
 * (`docs/DESIGN_SYSTEM.md`) now reachable from the live app: the org switcher, the
 * mobile drawer and the breadcrumb.
 *
 * Synthetic fixtures only (master context §10): fictional names.
 */

const GROUPS = ["Overview", "Payables", "Receivables", "Insights", "Workspace"];

const MODULE_KEYS = ["issuing", "email_intake", "budget", "expenses"];

function modules(enabled: boolean): Record<string, unknown>[] {
  return MODULE_KEYS.map((key) => ({
    key,
    name: key,
    description: key,
    core: false,
    enabled,
    requires_issuer: false,
    ready: true,
  }));
}

// A minimal dashboard payload — nav tests don't care about its content, only that
// the page under the shell renders without erroring (mirrors dashboard.spec.ts's
// REDUCED_DASHBOARD shape).
const DASHBOARD = {
  as_of: "2026-07-27",
  approvals: null,
  captures: { pending: 0, low_confidence_fields: 0 },
  payables: null,
  receivables: null,
  cash: null,
};

// PROD-003 (audit 2026-09-05): the nav is drawn from the permissions the API
// SERVES on `/auth/me`, so each role fixture carries the matrix row the real
// server would send (`app/core/authz.py::ROLE_PERMISSIONS`, verbatim). A
// fixture with NO `permissions` exercises the SPA's fallback mirror instead.
const PERMS: Record<string, string[]> = {
  owner: [
    "archive.read", "audit.read", "billing.manage", "expense.approve", "expense.read", "expense.write",
    "export.run", "invoice.approve", "invoice.delete", "invoice.read", "invoice.restore", "invoice.write",
    "issued.read", "issued.send", "issued.write", "member.manage", "member.read", "payment.read",
    "payment.write", "report.read", "role.assign", "settings.manage", "transport.read", "vat.read",
    "vat.submit", "vat.write",
  ],
  // EMPLOYEE: submits their own expenses, reads invoices. Nothing else.
  user: ["expense.read", "expense.write", "invoice.read"],
  finance_manager: [
    "audit.read", "expense.approve", "expense.read", "expense.write", "export.run", "invoice.approve",
    "invoice.delete", "invoice.read", "invoice.write", "issued.read", "issued.send", "issued.write",
    "payment.read", "payment.write", "report.read", "transport.read", "vat.read", "vat.submit", "vat.write",
  ],
  approver: ["expense.approve", "expense.read", "invoice.approve", "invoice.read", "report.read"],
};

interface MockOpts {
  role: "owner" | "user" | "finance_manager" | "approver";
  orgs: { org_id: string; name: string; role: string; current: boolean }[];
  modulesEnabled: boolean;
  isPlatformAdmin?: boolean;
  /** Override the served permission list (default: the role's matrix row);
   * `null` sends none, so the SPA falls back to its mirror. */
  permissions?: string[] | null;
}

async function mockApi(page: Page, opts: MockOpts): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const user = {
    id: "user-1",
    email: "owner@test.io",
    name: "Test User",
    role: opts.role,
    org_id: "org-1",
    is_platform_admin: opts.isPlatformAdmin ?? false,
  };
  const org = { id: "org-1", name: "Test Workspace", status: "active" };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") {
      const permissions = opts.permissions === undefined ? PERMS[opts.role] : opts.permissions;
      return route.fulfill(
        json(permissions === null ? { user, organization: org } : { user, organization: org, permissions }),
      );
    }
    if (path === "/auth/organizations") return route.fulfill(json(opts.orgs));
    if (path.startsWith("/auth/switch-org/")) return route.fulfill(json({ ok: true }));
    if (path === "/modules") return route.fulfill(json(modules(opts.modulesEnabled)));
    if (path === "/dashboard") return route.fulfill(json(DASHBOARD));
    if (path === "/invoices") return route.fulfill(json({ items: [], total: 0, page: 1, page_size: 20 }));
    if (path.startsWith("/analytics/")) return route.fulfill(json([]));
    return route.fulfill(json([]));
  });
}

const OWNER_SINGLE_ORG = [{ org_id: "org-1", name: "Test Workspace", role: "owner", current: true }];
const OWNER_MULTI_ORG = [
  { org_id: "org-1", name: "Test Workspace", role: "owner", current: true },
  { org_id: "org-2", name: "Second Workspace", role: "owner", current: false },
];

test("owner sees every group with a representative item per group, every module enabled", async ({ page }) => {
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/");

  const nav = page.getByRole("navigation", { name: "Primary" });
  for (const g of GROUPS) await expect(nav.getByText(g, { exact: true })).toBeVisible();

  await expect(nav.getByRole("link", { name: "Invoices" })).toHaveAttribute("href", "/invoices");
  await expect(nav.getByRole("link", { name: "Issue" })).toHaveAttribute("href", "/issue");
  await expect(nav.getByRole("link", { name: "Explore" })).toHaveAttribute("href", "/explore");
  await expect(nav.getByRole("link", { name: "Access" })).toHaveAttribute("href", "/access");
  await expect(nav.getByRole("link", { name: "Audit log" })).toHaveAttribute("href", "/audit");
});

test("platform admin gets a Platform link in Workspace", async ({ page }) => {
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: true, isPlatformAdmin: true });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Platform" })).toHaveAttribute("href", "/platform");
});

test("an employee sees only what the API serves an employee — no dead links", async ({ page }) => {
  await mockApi(page, { role: "user", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });

  // Anchors first (items the EMPLOYEE row serves) — they prove the nav actually
  // rendered for this role, so the absences below cannot pass vacuously against
  // a blank shell (check-e2e.mjs).
  await expect(nav.getByRole("link", { name: "Invoices" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Expenses", exact: true })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Cost objects" })).toBeVisible(); // invoice.read

  // PROD-003: before this, "Team", "Issue" and "Upload" were listed for an
  // employee and answered 403 — member.read, issued.read and invoice.write are
  // not in the EMPLOYEE row.
  for (const label of [
    "Team",
    "Issue",
    "Upload",
    "Payment runs",
    "Access",
    "Audit log",
    "Tax codes",
    "Currencies",
    "Documents",
    "Dunning",
    "Expense policy",
    "Explore",
  ]) {
    await expect(nav.getByRole("link", { name: label, exact: true })).toHaveCount(0);
  }
});

test("a finance manager sees the finance surfaces the ladder used to hide", async ({ page }) => {
  await mockApi(page, { role: "finance_manager", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });
  // Served by the FINANCE_MANAGER row; hidden by the old `owner`/`admin` flags.
  await expect(nav.getByRole("link", { name: "Audit log" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Reimbursements" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Payment runs" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Issue", exact: true })).toBeVisible();
  // Not served: no settings.manage, member.read or billing.manage in that row.
  for (const label of ["Team", "Settings", "Billing", "Dunning", "Automation", "Tax codes"]) {
    await expect(nav.getByRole("link", { name: label, exact: true })).toHaveCount(0);
  }
});

test("an approver sees the approval surfaces and nothing that would 403", async ({ page }) => {
  await mockApi(page, { role: "approver", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Review" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Reimbursements" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Explore" })).toBeVisible(); // report.read
  // Suppliers stays: /vendors is invoice.read, which an approver holds.
  for (const label of ["Upload", "Issue", "Payment runs", "Team", "Audit log"]) {
    await expect(nav.getByRole("link", { name: label, exact: true })).toHaveCount(0);
  }
});

test("the SERVED list wins over the stored role, and the mirror covers a response without one", async ({
  page,
}) => {
  // Stored role "user" but the server says this person holds audit.read (say,
  // a matrix change deployed before the SPA's mirror caught up): the nav
  // follows the server.
  await mockApi(page, {
    role: "user",
    orgs: OWNER_SINGLE_ORG,
    modulesEnabled: true,
    permissions: ["invoice.read", "audit.read"],
  });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Audit log" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Expenses", exact: true })).toHaveCount(0);

  // No `permissions` on the wire at all (an older API): the mirror row for the
  // stored role renders, so an owner still gets a full nav.
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: true, permissions: null });
  await page.goto("/");
  await expect(nav.getByRole("link", { name: "Access" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Billing" })).toBeVisible();
});

test("disabled modules remove their items and the now-empty Receivables group disappears", async ({ page }) => {
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: false });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });

  // Anchors first: Payables and Insights survive (they have non-module items),
  // proving the nav rendered before any absence is asserted (check-e2e.mjs).
  await expect(nav.getByText("Payables", { exact: true })).toBeVisible();
  await expect(nav.getByText("Insights", { exact: true })).toBeVisible();

  for (const label of [
    "Issue",
    "Customers",
    "Receipts",
    "Reconciliation",
    "Invoice reports",
    "Partners",
    "Dunning",
    "Email intake",
    "Budget",
    "Expenses",
    "Expense policy",
  ]) {
    await expect(nav.getByRole("link", { name: label, exact: true })).toHaveCount(0);
  }
  // Every Receivables item was issuing-gated, so the group heading itself is gone.
  await expect(nav.getByText("Receivables", { exact: true })).toHaveCount(0);
});

test("org switcher: a single org renders static text", async ({ page }) => {
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/");
  await expect(page.getByText("Test Workspace")).toBeVisible();
  await expect(page.getByRole("button", { name: "Switch organization" })).toHaveCount(0);
});

test("org switcher: multiple orgs render a working picker that posts the switch", async ({ page }) => {
  await mockApi(page, { role: "owner", orgs: OWNER_MULTI_ORG, modulesEnabled: true });
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "Switch organization" });
  await expect(trigger).toBeVisible();
  await trigger.click();
  const menu = page.getByRole("menu", { name: "Switch organization" });
  await expect(menu).toBeVisible();

  const switchRequest = page.waitForRequest((r) => r.url().includes("/auth/switch-org/org-2"));
  await menu.getByRole("menuitem", { name: /Second Workspace/ }).click();
  await switchRequest;
});

test("mobile drawer opens via the hamburger and contains the same grouped nav", async ({ page }) => {
  await page.setViewportSize({ width: 500, height: 800 });
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/");

  await page.getByRole("button", { name: "Open navigation menu" }).click();
  const drawerNav = page.getByRole("dialog", { name: "Menu" }).getByRole("navigation", { name: "Primary" });
  for (const g of GROUPS) await expect(drawerNav.getByText(g, { exact: true })).toBeVisible();
  await expect(drawerNav.getByRole("link", { name: "Invoices" })).toBeVisible();
});

test("breadcrumb reflects the current page", async ({ page }) => {
  await mockApi(page, { role: "owner", orgs: OWNER_SINGLE_ORG, modulesEnabled: true });
  await page.goto("/invoices");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Invoices");
});

// ---------------------------------------------------------------------------
// WO-45 (UX1): `PageHeader` is now the sole page-title mechanism on the eight
// money-bearing pages it touched — each renders exactly ONE `<h1>` (a bare
// `getByRole("heading", { level: 1 })` count), collapsing the duplicate
// `<h1>`s `Expenses.tsx`/`IssuedReports.tsx` used to render across their two
// branches (module-inactive/gated vs the live page).
// ---------------------------------------------------------------------------

const INVOICE_ITEM_H1 = {
  id: "inv-1",
  vendor_id: "vendor-1",
  invoice_number: "INV-H1-TEST",
  issue_date: "2026-05-01",
  due_date: null,
  currency: "EUR",
  status: "pending",
  subtotal: "100.00",
  tax_amount: "21.00",
  total: "121.00",
  validation_status: "none",
  source_filename: null,
  cost_center: null,
  department: null,
  project: null,
  vehicle: null,
  property_ref: null,
};

async function mockApiForOneH1(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const user = { id: "user-1", email: "owner@test.io", name: "Owner", role: "owner", org_id: "org-1" };
  const org = { id: "org-1", name: "Test Workspace", status: "active" };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user, organization: org }));
    if (path === "/auth/organizations") return route.fulfill(json([org]));
    if (path === "/modules") {
      return route.fulfill(
        json([
          { key: "expenses", name: "Expenses", description: "", core: false, enabled: true, requires_issuer: false, ready: true },
          { key: "issuing", name: "Issuing", description: "", core: false, enabled: true, requires_issuer: false, ready: true },
        ]),
      );
    }
    if (path === "/invoices" && !url.searchParams.get("validation_status")) {
      return route.fulfill(json({ items: [INVOICE_ITEM_H1], total: 1, page: 1, page_size: 20 }));
    }
    if (path === "/invoices" && url.searchParams.get("validation_status")) {
      return route.fulfill(json({ items: [], total: 0, page: 1, page_size: 100 }));
    }
    if (path === "/invoices/inv-1") return route.fulfill(json({ ...INVOICE_ITEM_H1, vendor_name: "Fictional Vendor", notes: null, line_items: [], validation_findings: [], validated_by: null, validated_at: null, workflow_state: null, amount_paid: "0.00", paid_date: null, outstanding: "121.00" }));
    if (path === "/invoices/inv-1/payments") return route.fulfill(json([]));
    if (path === "/expenses/summary") return route.fulfill(json({ my_reimbursable: "0.00", reclaimable_vat: "0.00", my_submitted: 0, pending_approvals: 0 }));
    if (path === "/expenses" || path === "/expenses/transactions") return route.fulfill(json(path === "/expenses" ? { items: [], total: 0 } : []));
    if (path === "/payment-runs" || path === "/payment-runs/payable") return route.fulfill(json([]));
    if (path === "/analytics/cash-position") {
      return route.fulfill(
        json({
          currency: "EUR",
          receivables: { outstanding: "0.00", overdue: "0.00", avg_days_to_pay: null, aging: [] },
          payables: { outstanding: "0.00", overdue: "0.00", scheduled: 0, count: 0, in_run: 0 },
          reconciliation: { unmatched: 0, matched: 0, ignored: 0, unmatched_amount: "0.00" },
          net_position: "0.00",
        }),
      );
    }
    if (path === "/analytics/ap-aging") {
      return route.fulfill(
        json({ currency: "EUR", due_soon_count: 0, due_soon_amount: "0.00", overdue_count: 0, overdue_amount: "0.00", other_currencies: [], items: [] }),
      );
    }
    if (path === "/analytics/cash-flow") return route.fulfill(json([]));
    if (path === "/issued/reports/summary") {
      return route.fulfill(
        json({ count: 0, currency: "EUR", net: "0.00", vat: "0.00", gross: "0.00", collected: "0.00", outstanding: "0.00", series: [] }),
      );
    }
    if (path === "/receipts") return route.fulfill(json([]));
    if (path === "/settings/validation") return route.fulfill(json({ ai_validation_enabled: true, human_validation_enabled: true }));

    return route.fulfill(json({}));
  });
}

test("every audited page renders exactly one h1", async ({ page }) => {
  await mockApiForOneH1(page);

  const routes = [
    "/invoices",
    "/expenses",
    "/payment-runs",
    "/cash-position",
    "/issue/reports",
    "/receipts",
    "/review",
    "/invoices/inv-1",
  ];
  for (const route of routes) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 }), `route ${route}`).toHaveCount(1);
  }
});
