import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * The VAT claims workspace (WO-78) over the LIVE app shell, API mocked via
 * `page.route` — deterministic, no backend, the same pattern as
 * `error-states.spec.ts` and `nav.spec.ts`.
 *
 * What this suite is actually proving:
 *  - the list/detail render from the WO-76/WO-77 wire shapes,
 *  - the permission mirror hides an action the role can't perform (cosmetic
 *    only — the server stays the control),
 *  - EVERY D5 submit refusal code renders its human sentence and the raw slug
 *    NEVER reaches the screen,
 *  - money renders from the wire STRING with no float round-trip,
 *  - the UI computes no total (the header figure is the server's, even when the
 *    lines deliberately don't sum to it),
 *  - the advisory checklist never blocks Submit (§4.19),
 *  - loading / empty / error states.
 *
 * Synthetic fixtures only (master context §10): fictional entities, suppliers,
 * invoice references and figures. No Fleet Fuel bytes.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

type Role = "owner" | "finance_manager" | "accountant" | "auditor" | "user";

function user(role: Role) {
  return {
    id: "user-1",
    email: "operator@test.io",
    name: "Test Operator",
    role,
    org_id: "org-1",
    is_platform_admin: false,
  };
}

const TRANSPORT_MODULE = {
  key: "transport",
  name: "Transport & VAT refunds (EU cross-border)",
  description: "",
  core: false,
  enabled: true,
  requires_issuer: false,
  ready: true,
};

const ENTITY = {
  id: "ent-1",
  name: "Northwind Haulage",
  is_default: true,
  legal_name: "Northwind Haulage OU",
  trade_name: null,
  vat_number: null,
  registration_number: null,
  address_line1: null,
  address_line2: null,
  city: null,
  postal_code: null,
  country: "EE",
  email: null,
  phone: null,
  iban: null,
  bic: null,
  default_currency: "EUR",
  invoice_prefix: "INV",
  next_number: 1,
};

const DRAFT_CLAIM = {
  id: "claim-1",
  entity_id: "ent-1",
  refund_country: "LV",
  ref_period: "2026-Q2",
  status: "draft",
  status_code: null,
  status_note: null,
  decision_date: null,
  action_deadline: null,
  submitted_date: null,
  approved_date: null,
  paid_date: null,
  paid_amount: null,
  vat_eur: null,
  vat_local: null,
  currency: null,
  fee_pct: null,
  fee_min: null,
  fee_eur: null,
  created_at: "2026-07-01T09:00:00Z",
};

// A FILED claim whose frozen header total deliberately does NOT equal the sum of
// the mocked lines below (1234.56 vs 999.99 + a 14-digit line). If the page ever
// starts computing a total, this fixture makes it fail loudly.
const FILED_CLAIM = {
  ...DRAFT_CLAIM,
  id: "claim-2",
  status: "submitted",
  status_code: "2",
  submitted_date: "2026-07-15",
  vat_eur: "1234.56",
  vat_local: "870.10",
  currency: "EUR",
  fee_eur: "185.18",
  status_note: "Filed under the standard fee schedule.",
};

const LINES = [
  {
    id: "line-1",
    claim_id: "claim-1",
    invoice_ref: "INV-4001",
    // Reuses the already-allowlisted all-nines synthetic VAT id (see
    // scripts/pii_allowlist.json) so the fixture is obviously fictional.
    vat_id: "LV99999999999",
    invoice_id: "doc-1",
    goods_code: "1",
    product_group: "diesel",
    net_eur: "4761.90",
    vat_eur: "999.99",
    net_local: "4761.90",
    vat_local: "999.99",
    currency: "EUR",
    frozen_at: null,
  },
  {
    // A synthetic (unresolved) reference — the page must flag it.
    id: "line-2",
    claim_id: "claim-1",
    invoice_ref: "UNMATCHED",
    vat_id: null,
    invoice_id: null,
    goods_code: "4",
    product_group: "toll",
    net_eur: "120.00",
    vat_eur: "25.20",
    net_local: "120.00",
    vat_local: "25.20",
    currency: "EUR",
    frozen_at: null,
  },
];

// A value with more significant digits than an IEEE-754 double can hold:
// Number("12345678901234.57") prints 12345678901234.57 only by luck of rounding,
// and Number("99999999999999.99") becomes 100000000000000. Use the latter.
const BIG_MONEY_LINE = {
  ...LINES[0],
  id: "line-big",
  invoice_ref: "INV-9999",
  net_eur: "99999999999999.99",
  vat_eur: "99999999999999.99",
};

// WO-79 — the fuel transactions the submit pick-list is built from. Field-for-
// field from `backend/app/schemas/transport_fuel.py`. Synthetic throughout.
const FUEL_TXNS = [
  {
    id: "txn-1",
    entity_id: "ent-1",
    supplier: "Q8",
    period: "2026-05",
    line_seq: 1,
    country: "LV",
    vehicle_ref: "CARD-0001",
    txn_date: "2026-05-15",
    txn_time: "07:42",
    station: "Riga Ring Station",
    product: "DIESEL",
    product_group: "Diesel",
    qty: "100.125",
    currency: "EUR",
    net_local: "2000.00",
    vat_local: "420.00",
    gross_local: "2420.00",
    net_eur: "2000.00",
    vat_eur: "420.00",
    net_eur_eff: "2000.00",
    invoice_ref: "INV-4001",
    provenance_note: null,
    invoice_id: null,
    fx_rate: null,
    fx_ecb_rate: null,
    fx_ecb_date: null,
    fx_source: "eur",
    created_at: "2026-06-01T09:00:00Z",
  },
  {
    // No invoice reference on the statement line (the nullable column's real
    // case) AND a value no IEEE-754 double can hold — one row proving both.
    id: "txn-2",
    entity_id: "ent-1",
    supplier: "BP",
    period: "2026-06",
    line_seq: 7,
    country: "LV",
    vehicle_ref: "CARD-0002",
    txn_date: "2026-06-02",
    txn_time: "",
    station: "Daugavpils Depot",
    product: "TOLL",
    product_group: "Toll/Fees",
    qty: "1.000",
    currency: "EUR",
    net_local: "99999999999999.99",
    vat_local: "99999999999999.99",
    gross_local: "99999999999999.99",
    net_eur: "99999999999999.99",
    vat_eur: "99999999999999.99",
    net_eur_eff: "99999999999999.99",
    invoice_ref: null,
    provenance_note: null,
    invoice_id: null,
    fx_rate: null,
    fx_ecb_rate: null,
    fx_ecb_date: null,
    fx_source: "eur",
    created_at: "2026-07-01T09:00:00Z",
  },
];

const FUEL_LIST = { items: FUEL_TXNS, total: 2, page: 1, page_size: 500 };

const CHECKLIST = [
  {
    key: "period_ended",
    label: "Reference period has ended",
    scope: "claim",
    ok: true,
    reason: null,
  },
  {
    key: "customer_data",
    label: "Customer master data complete",
    scope: "customer",
    ok: false,
    reason: "Bank account is missing on the customer record",
  },
];

const STATUS_CODES = {
  auto: ["1A", "1B", "1C", "1E"],
  manual: ["2A", "2B", "3", "3A", "3B", "3C", "3D", "4", "4A", "5"],
};

// WO-80 — the claim-scoped receipt waivers (R15). Synthetic supplier code.
const WAIVERS = [{ id: "wv-1", claim_id: "claim-1", supplier: "TOLLCO" }];

interface MockOpts {
  role?: Role;
  moduleEnabled?: boolean;
  claims?: unknown;
  claimsStatus?: number;
  claim?: unknown;
  lines?: unknown;
  checklist?: unknown;
  stage?: unknown;
  /** Refusals keyed by path suffix, e.g. "submit" | "workbook" | "evidence". */
  refuse?: Record<string, { status: number; code: string; detail: string }>;
  /** A refusal for `POST /transport/claims` only. It needs its own option
   * because that path's suffix ("claims") is shared with the LIST read, and
   * refusing both would stop the page rendering at all. */
  refuseCreate?: { status: number; code: string; detail: string };
  /** WO-80 — the claim-scoped receipt waivers (R15). */
  waivers?: unknown;
  /** WO-79 — the `GET /transport/fuel-transactions` body the pick-list reads. */
  fuel?: unknown;
  fuelStatus?: number;
  /** Delay (ms) applied to the fuel read, to observe the pick-list loading state. */
  fuelDelayMs?: number;
  /** Delay (ms) applied to GET /transport/claims, to observe the loading state. */
  listDelayMs?: number;
  /** Collects every POST body the page sends, keyed by path suffix. */
  captured?: Record<string, unknown[]>;
}

async function mockApi(page: Page, opts: MockOpts = {}): Promise<void> {
  const {
    role = "owner",
    moduleEnabled = true,
    claims = [DRAFT_CLAIM, FILED_CLAIM],
    claimsStatus,
    claim = DRAFT_CLAIM,
    lines = LINES,
    checklist = CHECKLIST,
    stage = { stage: "1A" },
    waivers = WAIVERS,
    refuse = {},
    refuseCreate,
    fuel = FUEL_LIST,
    fuelStatus,
    fuelDelayMs = 0,
    listDelayMs = 0,
    captured,
  } = opts;

  await page.addInitScript(() =>
    localStorage.setItem("invoiceiq_token", "e2e-token"),
  );

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (path === "/auth/me")
      return route.fulfill(json({ user: user(role), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(
        json([{ ...TRANSPORT_MODULE, enabled: moduleEnabled }]),
      );
    if (path === "/issuer/registry") return route.fulfill(json([ENTITY]));
    if (path === "/transport/status-codes")
      return route.fulfill(json(STATUS_CODES));

    // Record every mutating body so a test can assert what was actually sent.
    if (captured && method === "POST") {
      const key = path.split("/").pop() ?? path;
      (captured[key] ??= []).push(route.request().postDataJSON());
    }

    for (const [suffix, r] of Object.entries(refuse)) {
      if (path.endsWith(`/${suffix}`)) {
        return route.fulfill(
          json({ detail: r.detail, code: r.code }, r.status),
        );
      }
    }

    if (path === "/transport/fuel-transactions") {
      if (fuelDelayMs) await new Promise((res) => setTimeout(res, fuelDelayMs));
      if (fuelStatus && fuelStatus >= 400)
        return route.fulfill(
          json({ detail: "boom", code: "mocked_failure" }, fuelStatus),
        );
      return route.fulfill(json(fuel));
    }
    if (path === "/transport/claims" && method === "GET") {
      if (listDelayMs) await new Promise((res) => setTimeout(res, listDelayMs));
      if (claimsStatus && claimsStatus >= 400)
        return route.fulfill(
          json({ detail: "boom", code: "mocked_failure" }, claimsStatus),
        );
      return route.fulfill(json(claims));
    }
    if (path === "/transport/claims" && method === "POST") {
      if (refuseCreate)
        return route.fulfill(
          json(
            { detail: refuseCreate.detail, code: refuseCreate.code },
            refuseCreate.status,
          ),
        );
      return route.fulfill(json(DRAFT_CLAIM));
    }
    if (/\/transport\/claims\/[^/]+$/.test(path))
      return route.fulfill(json(claim));
    if (path.endsWith("/lines")) return route.fulfill(json(lines));
    if (path.endsWith("/checklist")) return route.fulfill(json(checklist));
    // WO-80 — the waivers panel: the list read, plus the set/remove verbs whose
    // bodies the `captured` collector already records.
    if (path.endsWith("/waivers"))
      return route.fulfill(
        method === "GET"
          ? json(waivers)
          : json({ id: "wv-2", claim_id: "claim-1", supplier: "NEWCO" }),
      );
    if (/\/waivers\/[^/]+$/.test(path))
      return route.fulfill(json({ removed: true }));
    if (path.endsWith("/stage")) return route.fulfill(json(stage));
    if (
      path.endsWith("/submit") ||
      path.endsWith("/withdraw") ||
      path.endsWith("/status-code")
    )
      return route.fulfill(json(FILED_CLAIM));
    if (path.endsWith("/workbook"))
      return route.fulfill({
        status: 200,
        contentType: "application/octet-stream",
        body: "xlsx",
      });
    if (path.endsWith("/evidence"))
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        body: "zip",
      });

    return route.fulfill(json({}));
  });
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

test("list: renders each claim's grain, status and frozen total", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/vat-claims");

  await expect(
    page.getByRole("heading", { name: "VAT refund claims" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Northwind Haulage" }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "2026-Q2" }).first(),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "LV" }).first()).toBeVisible();
  // The filed claim's frozen total, formatted from the wire string.
  await expect(page.getByRole("cell", { name: "€1,234.56" })).toBeVisible();
  // The draft's null total is an em dash, NOT €0.00 — "not frozen yet" is a
  // different fact from zero.
  await expect(page.getByText("€0.00")).toHaveCount(0);
});

test("list: shows a loading state before the API resolves", async ({
  page,
}) => {
  await mockApi(page, { listDelayMs: 1500 });
  await page.goto("/vat-claims");
  // The QueryState loading branch renders a skeleton (aria-hidden shimmer), and
  // no row and no empty copy are on screen yet.
  await expect(page.getByText("No VAT refund claims yet")).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "€1,234.56" })).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "€1,234.56" })).toBeVisible({
    timeout: 10_000,
  });
});

test("list: a genuinely empty result shows the empty copy, never an alert", async ({
  page,
}) => {
  await mockApi(page, { claims: [] });
  await page.goto("/vat-claims");

  await expect(page.getByText("No VAT refund claims yet")).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: "Couldn’t load VAT claims" }),
  ).toHaveCount(0);
});

test("list: a 500 shows the error state, never the empty copy", async ({
  page,
}) => {
  await mockApi(page, { claimsStatus: 500 });
  await page.goto("/vat-claims");

  await expect(
    page.getByRole("alert").filter({ hasText: "Couldn’t load VAT claims" }),
  ).toBeVisible();
  await expect(page.getByText("No VAT refund claims yet")).toHaveCount(0);
});

test("list: the module being off shows the module notice, not a table", async ({
  page,
}) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/vat-claims");

  await expect(
    page.getByText("Transport & VAT refunds", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("isn't active", { exact: false })).toBeVisible();
  await expect(page.getByRole("cell", { name: "€1,234.56" })).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Navigation gating
// ---------------------------------------------------------------------------

test("nav: a VAT_READ role sees the Transport group; an employee does not", async ({
  page,
}) => {
  await mockApi(page, { role: "auditor" });
  await page.goto("/vat-claims");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "VAT claims" }),
  ).toBeVisible();

  await mockApi(page, { role: "user" });
  await page.goto("/vat-claims");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "VAT claims" }),
  ).toHaveCount(0);
});

test("nav: the entry is hidden when the transport module is off", async ({
  page,
}) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/vat-claims");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "VAT claims" }),
  ).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

test("detail: renders the grain, the lines and the server's own totals", async ({
  page,
}) => {
  await mockApi(page, { claim: FILED_CLAIM });
  await page.goto("/vat-claims/claim-2");

  await expect(
    page.getByRole("heading", { name: "LV · 2026-Q2" }),
  ).toBeVisible();
  await expect(page.getByText("INV-4001")).toBeVisible();
  // SCOPED, not weakened (the WO-80 decision-2 precedent). The assertion was
  // always about the product-group CELL of the claim's line table; a bare
  // `getByText("diesel")` was unambiguous only while nothing else in the shell
  // said "diesel", and WO-92's "Diesel excise" nav destination — which is on
  // every page — made it ambiguous. Naming the role the cell always had is
  // strictly stronger than the substring match it replaces.
  await expect(page.getByRole("cell", { name: "diesel" })).toBeVisible();
  await expect(
    page.getByText("Filed under the standard fee schedule."),
  ).toBeVisible();
  // The unresolved line is flagged from the is_synthetic mirror.
  await expect(page.getByText("UNMATCHED")).toBeVisible();
  await expect(page.getByText("unresolved")).toBeVisible();
});

test("detail: the header total is the server's, never a sum of the lines", async ({
  page,
}) => {
  await mockApi(page, { claim: FILED_CLAIM });
  await page.goto("/vat-claims/claim-2");

  // Header: the claim's own frozen figure.
  await expect(page.getByText("€1,234.56")).toBeVisible();
  // The lines sum to 1,025.19 — if the page ever computed a total, it would show.
  await expect(page.getByText("€1,025.19")).toHaveCount(0);
});

test("detail: money renders exactly from the wire string, with no float round-trip", async ({
  page,
}) => {
  await mockApi(page, { claim: FILED_CLAIM, lines: [BIG_MONEY_LINE] });
  await page.goto("/vat-claims/claim-2");

  // Number("99999999999999.99") === 100000000000000, so a float path would print
  // €100,000,000,000,000.00. The string formatter keeps every digit.
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
  await expect(page.getByText("€100,000,000,000,000.00")).toHaveCount(0);
});

test("detail: a failing checklist item is shown and never disables Submit", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/vat-claims/claim-1");

  await expect(page.getByText("Customer master data complete")).toBeVisible();
  await expect(
    page.getByText("Bank account is missing on the customer record"),
  ).toBeVisible();
  // §4.19 — advisory never blocks.
  await expect(
    page.getByRole("button", { name: "Submit claim" }),
  ).toBeEnabled();
});

test("detail: the stage ladder marks the service-derived stage on a draft claim", async ({
  page,
}) => {
  await mockApi(page, { stage: { stage: "1C" } });
  await page.goto("/vat-claims/claim-1");

  const ladder = page.getByRole("list", { name: "Claim stage" });
  await expect(ladder).toBeVisible();
  await expect(ladder.getByText("1C", { exact: true })).toHaveAttribute(
    "aria-current",
    "step",
  );
});

// ---------------------------------------------------------------------------
// Permission-gated action visibility
// ---------------------------------------------------------------------------

test("permissions: an accountant sees Build lines but no Submit or Withdraw", async ({
  page,
}) => {
  await mockApi(page, { role: "accountant" });
  await page.goto("/vat-claims/claim-1");

  await expect(page.getByRole("button", { name: "Build lines" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit claim" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "Withdraw" })).toHaveCount(0);
});

test("permissions: a read-only auditor sees no mutating action at all", async ({
  page,
}) => {
  await mockApi(page, { role: "auditor", claim: FILED_CLAIM });
  await page.goto("/vat-claims/claim-2");

  await expect(page.getByRole("button", { name: "Build lines" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "Submit claim" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "Withdraw" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Set status code" }),
  ).toHaveCount(0);
  // Reads are still available.
  await expect(
    page.getByRole("button", { name: "Claim workbook" }),
  ).toBeVisible();
});

test("permissions: an accountant sees no create form on the list", async ({
  page,
}) => {
  await mockApi(page, { role: "auditor" });
  await page.goto("/vat-claims");
  await expect(page.getByText("Start a claim")).toHaveCount(0);

  await mockApi(page, { role: "accountant" });
  await page.goto("/vat-claims");
  await expect(page.getByText("Start a claim")).toBeVisible();
});

test("create refusal: invalid_period keeps the CLAIM instruction (quarter or year)", async ({
  page,
}) => {
  // WO-91's split, from the other side. `invalid_period` is one wire code shared
  // by seven services; the ACTION depends on the period shape the PAGE asks for.
  // This page asks for a claim reference period, so it must keep saying
  // "2026-Q2 / 2026-YEAR" — the split must not have flipped it to a month.
  await mockApi(page, {
    refuseCreate: {
      status: 422,
      code: "invalid_period",
      detail:
        "'2026-04' is not a valid claim period — use YYYY-Q1..YYYY-Q4 or YYYY-YEAR",
    },
  });
  await page.goto("/vat-claims");
  await page.getByLabel("Legal entity").selectOption(ENTITY.id);
  await page.getByLabel("Refunding country").fill("LV");
  await page.getByLabel("Reference period").fill("2026-04");
  await page.getByRole("button", { name: "Open or create" }).click();

  const alert = page.getByRole("alert");
  await expect(alert).toContainText(
    "isn\u2019t a reference period this service can file",
  );
  await expect(alert).toContainText("Use a quarter such as 2026-Q2");
  // The month-shaped instruction must NOT appear on a claim-shaped page.
  await expect(alert).not.toContainText("Use a month such as 2026-04.");
  await expect(page.getByText("invalid_period")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// The D5 submit refusal vocabulary — one case per code
// ---------------------------------------------------------------------------

async function submitWith(
  page: Page,
  code: string,
  detail: string,
  status = 409,
) {
  await mockApi(page, { refuse: { submit: { status, code, detail } } });
  await page.goto("/vat-claims/claim-1");
  await page.getByRole("button", { name: "Submit claim" }).click();
  await page.getByRole("button", { name: "File claim" }).click();
}

const REFUSALS: {
  code: string;
  detail: string;
  status?: number;
  expect: string;
}[] = [
  {
    code: "period_not_ended",
    detail: "The '2026-Q2' period has not ended yet — cannot submit",
    expect: "The reference period hasn’t finished yet",
  },
  {
    code: "customer_not_active",
    detail: "Customer is not active",
    expect: "This entity isn’t activated for refund claims",
  },
  {
    code: "country_not_activated",
    detail: "Refund country LV is not activated for this entity",
    expect: "This refund country isn’t activated for the entity",
  },
  {
    code: "unresolved_invoice_refs",
    detail: "Unresolved references: UNMATCHED",
    expect: "Some claim lines aren’t tied to a real invoice yet",
  },
  {
    code: "invoice_document_missing",
    detail: "No stored document for invoice INV-4001",
    expect: "An invoice on this claim has no stored document",
  },
  {
    code: "duplicate_invoice_lock",
    detail: "INV-4001 is already locked by another claim",
    expect: "One of these invoices is already claimed on another filing",
  },
  {
    code: "claim_not_draft",
    detail: "Claim is 'submitted', not 'draft' — cannot submit",
    expect: "This claim has already left draft",
  },
  {
    code: "empty_claim_set",
    detail: "Cannot submit a claim with an empty invoice set",
    status: 422,
    expect: "Add at least one invoice before filing",
  },
  {
    code: "claim_line_mixed_currency",
    detail: "Claim line (INV-4001, diesel) spans multiple currencies",
    status: 422,
    expect: "A claim line mixes currencies",
  },
];

for (const r of REFUSALS) {
  test(`submit refusal: ${r.code} renders its human message, never the slug`, async ({
    page,
  }) => {
    await submitWith(page, r.code, r.detail, r.status ?? 409);

    const alert = page.getByRole("alert").filter({ hasText: r.expect });
    await expect(alert).toBeVisible();
    // The server's own specifics ride underneath.
    await expect(alert).toContainText(r.detail);
    // The machine slug never reaches the screen.
    await expect(page.getByText(r.code, { exact: false })).toHaveCount(0);
  });
}

test("submit refusal: below_minimum offers the override and re-posts with override_minimum", async ({
  page,
}) => {
  const captured: Record<string, unknown[]> = {};
  await mockApi(page, {
    captured,
    refuse: {
      submit: {
        status: 409,
        code: "below_minimum",
        detail: "Claim VAT 40.00 EUR is below the 400.00 EUR minimum for LV",
      },
    },
  });
  await page.goto("/vat-claims/claim-1");
  await page.getByRole("button", { name: "Submit claim" }).click();
  await page.getByRole("button", { name: "File claim" }).click();

  const alert = page.getByRole("alert").filter({
    hasText:
      "The claim is below the minimum refundable amount for this country",
  });
  await expect(alert).toBeVisible();
  await expect(page.getByText("below_minimum", { exact: false })).toHaveCount(
    0,
  );

  await alert
    .getByRole("button", { name: "File it anyway and record the override" })
    .click();
  await expect.poll(() => (captured.submit ?? []).length).toBeGreaterThan(1);
  const bodies = captured.submit as { override_minimum: boolean }[];
  expect(bodies[0].override_minimum).toBe(false);
  expect(bodies[bodies.length - 1].override_minimum).toBe(true);
});

test("submit refusal: an accountant never gets the override action (VAT_SUBMIT only)", async ({
  page,
}) => {
  await mockApi(page, { role: "accountant" });
  await page.goto("/vat-claims/claim-1");
  await expect(
    page.getByRole("button", {
      name: "File it anyway and record the override",
    }),
  ).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Filing artifacts
// ---------------------------------------------------------------------------

test("artifacts: a workbook refusal renders its human message, never the slug", async ({
  page,
}) => {
  await mockApi(page, {
    refuse: {
      workbook: {
        status: 409,
        code: "claim_not_frozen",
        detail: "Claim has no frozen lines — submit it first",
      },
    },
  });
  await page.goto("/vat-claims/claim-1");
  await page.getByRole("button", { name: "Claim workbook" }).click();

  await expect(
    page.getByRole("alert").filter({
      hasText: "The filing pack isn’t available until the claim is filed",
    }),
  ).toBeVisible();
  await expect(
    page.getByText("claim_not_frozen", { exact: false }),
  ).toHaveCount(0);
});

test("artifacts: an evidence-pack refusal explains that the workbook still downloads", async ({
  page,
}) => {
  await mockApi(page, {
    claim: FILED_CLAIM,
    refuse: {
      evidence: {
        status: 409,
        code: "evidence_document_unavailable",
        detail: "Vaulted document for INV-4001 could not be read",
      },
    },
  });
  await page.goto("/vat-claims/claim-2");
  await page.getByRole("button", { name: "Evidence pack" }).click();

  const alert = page
    .getByRole("alert")
    .filter({ hasText: "An invoice document in the pack can’t be read" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("The workbook still downloads");
});

test("artifacts: the evidence pack downloads on a filed claim", async ({
  page,
}) => {
  await mockApi(page, { claim: FILED_CLAIM });
  await page.goto("/vat-claims/claim-2");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Evidence pack" }).click();
  expect((await download).suggestedFilename()).toBe(
    "claim-claim-2-evidence.zip",
  );
});

// ---------------------------------------------------------------------------
// The submit PICK-LIST (WO-79) — the fuel-transaction read surface in the UI.
//
// What replaced what: WO-78's dialog asked an operator to TYPE a supplier and a
// fuel-transaction UUID, because no route enumerated transactions. WO-79 routed
// them, so the tuple `lock.submit_claim` keys its locks on now comes off the
// selected row. These specs prove the payload is the transaction's own data,
// that the typed path survives, and that money still never round-trips a float.
// ---------------------------------------------------------------------------

async function openPickList(page: Page, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/vat-claims/claim-1");
  await page.getByRole("button", { name: "Submit claim" }).click();
}

test("pick-list: renders the period's fuel transactions with their own values", async ({
  page,
}) => {
  await openPickList(page);

  await expect(
    page.getByRole("cell", { name: "Q8", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "BP", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Riga Ring Station")).toBeVisible();
  await expect(page.getByText("Daugavpils Depot")).toBeVisible();
  // Litres come off the wire as an exact string — no rounding, no arithmetic.
  await expect(page.getByRole("cell", { name: "100.125" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "€2,000.00" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "€420.00" })).toBeVisible();
});

test("pick-list: money renders exactly from the wire string, with no float round-trip", async ({
  page,
}) => {
  await openPickList(page);
  // Number("99999999999999.99") is 100000000000000 — the exact digits below can
  // only appear if the formatter never touched a double.
  await expect(
    page.getByRole("cell", { name: "€99,999,999,999,999.99" }).first(),
  ).toBeVisible();
  await expect(page.getByText("€100,000,000,000,000.00")).toHaveCount(0);
});

test("pick-list: ticking a transaction posts its supplier, reference and id verbatim", async ({
  page,
}) => {
  const captured: Record<string, unknown[]> = {};
  await openPickList(page, { captured });

  await page.getByRole("checkbox", { name: /Include Q8/ }).check();
  await page.getByRole("button", { name: "File claim" }).click();

  await expect.poll(() => (captured.submit ?? []).length).toBe(1);
  const body = captured.submit[0] as {
    invoices: unknown[];
    override_minimum: boolean;
  };
  expect(body.invoices).toEqual([
    { supplier: "Q8", invoice_ref: "INV-4001", fuel_transaction_id: "txn-1" },
  ]);
  expect(body.override_minimum).toBe(false);
});

test("pick-list: a transaction with no invoice reference takes the one typed on its row", async ({
  page,
}) => {
  const captured: Record<string, unknown[]> = {};
  await openPickList(page, { captured });

  // `invoice_ref` is nullable by design on a fuel transaction, while the submit
  // schema requires a non-empty one — so the field stays editable.
  await page.getByRole("checkbox", { name: /Include BP/ }).check();
  await page
    .getByRole("textbox", { name: /Invoice reference for BP/ })
    .fill("INV-7777");
  await page.getByRole("button", { name: "File claim" }).click();

  await expect.poll(() => (captured.submit ?? []).length).toBe(1);
  const body = captured.submit[0] as { invoices: unknown[] };
  expect(body.invoices).toEqual([
    { supplier: "BP", invoice_ref: "INV-7777", fuel_transaction_id: "txn-2" },
  ]);
});

test("pick-list: unticking a transaction takes it back out of the payload", async ({
  page,
}) => {
  const captured: Record<string, unknown[]> = {};
  await openPickList(page, { captured });

  const q8 = page.getByRole("checkbox", { name: /Include Q8/ });
  const bp = page.getByRole("checkbox", { name: /Include BP/ });
  await q8.check();
  await bp.check();
  await bp.uncheck();
  await page.getByRole("button", { name: "File claim" }).click();

  await expect.poll(() => (captured.submit ?? []).length).toBe(1);
  const body = captured.submit[0] as {
    invoices: { fuel_transaction_id: string }[];
  };
  expect(body.invoices.map((i) => i.fuel_transaction_id)).toEqual(["txn-1"]);
});

test("pick-list: a loading state renders before the fuel read resolves", async ({
  page,
}) => {
  await openPickList(page, { fuelDelayMs: 1500 });

  await expect(
    page.getByText("No fuel transactions for this period"),
  ).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "Q8", exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByRole("cell", { name: "Q8", exact: true })).toBeVisible(
    {
      timeout: 10_000,
    },
  );
});

test("pick-list: an empty period shows the empty copy and falls back to the typed rows", async ({
  page,
}) => {
  await openPickList(page, {
    fuel: { items: [], total: 0, page: 1, page_size: 500 },
  });

  await expect(
    page.getByText("No fuel transactions for this period"),
  ).toBeVisible();
  // WO-78's behaviour preserved: the manual section is pre-seeded with the
  // non-synthetic references on the claim's materialized lines (INV-4001), and
  // never with the synthetic "UNMATCHED" one.
  await expect(
    page.getByRole("textbox", { name: "Invoice reference" }),
  ).toHaveValue("INV-4001");
  await expect(
    page.getByRole("button", { name: "Add a row manually" }),
  ).toBeVisible();
});

test("pick-list: a fuel read failure shows the error state and the typed path still files", async ({
  page,
}) => {
  const captured: Record<string, unknown[]> = {};
  await openPickList(page, { captured, fuelStatus: 500 });

  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Couldn’t load this period’s fuel transactions" }),
  ).toBeVisible();

  // The dialog is still usable: the pre-seeded typed row files as before.
  // Scoped to the submit dialog: WO-80 added a "Waive a supplier" field to the
  // page behind it, and an unscoped accessible-name match would find both.
  await page
    .getByRole("dialog")
    .getByRole("textbox", { name: "Supplier" })
    .fill("Q8");
  await page
    .getByRole("textbox", { name: "Fuel transaction id" })
    .fill("txn-manual");
  await page.getByRole("button", { name: "File claim" }).click();

  await expect.poll(() => (captured.submit ?? []).length).toBe(1);
  const body = captured.submit[0] as { invoices: unknown[] };
  expect(body.invoices).toEqual([
    {
      supplier: "Q8",
      invoice_ref: "INV-4001",
      fuel_transaction_id: "txn-manual",
    },
  ]);
});

test("pick-list: a manual row and a picked transaction can be filed together", async ({
  page,
}) => {
  const captured: Record<string, unknown[]> = {};
  await openPickList(page, { captured });

  await page.getByRole("checkbox", { name: /Include Q8/ }).check();
  await page.getByRole("button", { name: "Add a row manually" }).click();
  // Scoped to the submit dialog: WO-80 added a "Waive a supplier" field to the
  // page behind it, and an unscoped accessible-name match would find both.
  await page
    .getByRole("dialog")
    .getByRole("textbox", { name: "Supplier" })
    .fill("DKV");
  await page
    .getByRole("textbox", { name: "Invoice reference", exact: true })
    .fill("INV-5555");
  await page
    .getByRole("textbox", { name: "Fuel transaction id" })
    .fill("txn-manual");
  await page.getByRole("button", { name: "File claim" }).click();

  await expect.poll(() => (captured.submit ?? []).length).toBe(1);
  const body = captured.submit[0] as { invoices: unknown[] };
  expect(body.invoices).toEqual([
    { supplier: "Q8", invoice_ref: "INV-4001", fuel_transaction_id: "txn-1" },
    {
      supplier: "DKV",
      invoice_ref: "INV-5555",
      fuel_transaction_id: "txn-manual",
    },
  ]);
});

test("pick-list: an advisory checklist failure never disables the file action", async ({
  page,
}) => {
  // §4.19 re-proven at the dialog level: the checklist fixture fails
  // `customer_data`, and the pick-list adds no gate of its own either.
  await openPickList(page);
  await expect(page.getByRole("button", { name: "File claim" })).toBeEnabled();
  await page.getByRole("checkbox", { name: /Include Q8/ }).check();
  await expect(page.getByRole("button", { name: "File claim" })).toBeEnabled();
});

// ---------------------------------------------------------------------------
// Receipt waivers (R15) — WO-80's claim-scoped addition to this page
// ---------------------------------------------------------------------------

test("waivers: the claim lists the suppliers waived on it", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/vat-claims/claim-1");

  await expect(
    page.getByRole("heading", { name: "Suppliers waived on this claim" }),
  ).toBeVisible();
  await expect(page.getByText("TOLLCO")).toBeVisible();
  // The copy must steer an operator away from waiving a merely-unresolved
  // supplier — waiving one drops reclaimable VAT.
  await expect(
    page.getByText("waiving would drop VAT you can reclaim"),
  ).toBeVisible();
});

test("waivers: an empty list shows the empty copy, never an alert", async ({
  page,
}) => {
  await mockApi(page, { waivers: [] });
  await page.goto("/vat-claims/claim-1");

  await expect(
    page.getByText("No supplier is waived on this claim"),
  ).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: "Couldn’t load the waivers" }),
  ).toHaveCount(0);
});

test("waivers: waiving a supplier posts its code", async ({ page }) => {
  const captured: Record<string, unknown[]> = {};
  await mockApi(page, { captured });
  await page.goto("/vat-claims/claim-1");

  await page.getByRole("textbox", { name: "Waive a supplier" }).fill("NEWCO");
  await page.getByRole("button", { name: "Waive", exact: true }).click();

  await expect.poll(() => (captured.waivers ?? []).length).toBe(1);
  expect(captured.waivers[0]).toEqual({ supplier: "NEWCO" });
});

test("waivers: waiver_supplier_has_invoices renders its sentence, not the slug", async ({
  page,
}) => {
  await mockApi(page, {
    refuse: {
      waivers: {
        status: 422,
        code: "waiver_supplier_has_invoices",
        detail: "Supplier TOLLCO has 2 registered invoices for LV",
      },
    },
  });
  await page.goto("/vat-claims/claim-1");

  await page.getByRole("textbox", { name: "Waive a supplier" }).fill("TOLLCO");
  await page.getByRole("button", { name: "Waive", exact: true }).click();

  await expect(
    page.getByText(
      "That supplier does have a registered invoice for this country",
    ),
  ).toBeVisible();
  // The server's own specifics ride underneath; the raw slug never appears.
  await expect(
    page.getByText("Supplier TOLLCO has 2 registered invoices for LV"),
  ).toBeVisible();
  await expect(page.getByText("waiver_supplier_has_invoices")).toHaveCount(0);
});

test("waivers: a read-only role sees the list and no waive control", async ({
  page,
}) => {
  await mockApi(page, { role: "auditor" });
  await page.goto("/vat-claims/claim-1");

  await expect(page.getByText("TOLLCO")).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Waive a supplier" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Un-waive" })).toHaveCount(0);
});
