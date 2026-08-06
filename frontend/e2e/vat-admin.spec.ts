import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * The transport ADMIN/CONFIG workspace (WO-80) over the LIVE app shell, API
 * mocked via `page.route` — the same deterministic pattern as
 * `vat-claims.spec.ts` and `error-states.spec.ts`, no backend involved.
 *
 * What this suite is actually proving:
 *  - each of the six `/vat-admin` panels renders from the WO-77 wire shape and
 *    reads only its own route,
 *  - the permission mirror hides every mutation from a read-only role and
 *    shows it to a VAT_WRITE role (cosmetic only — the server is the control),
 *  - each WO-77 refusal code renders its human sentence and the raw slug NEVER
 *    reaches the screen,
 *  - the ADVISORY receipt-control grid says so in words, and the tie-out panel
 *    says the opposite (it halts the close) — the distinction §4.19 turns on,
 *  - figures render and re-post exactly from the wire string, with no float
 *    round-trip anywhere,
 *  - the customer/country ladders show the state unambiguously and offer only
 *    the transitions legal from it,
 *  - loading / empty / error states, module gating, nav gating.
 *
 * Synthetic fixtures only (master context §10): fictional entities, supplier
 * codes, references and figures. No Fleet Fuel bytes.
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

const RULES = [
  {
    id: "rule-1",
    key: "customer_data",
    label: "Customer master data complete",
    scope: "customer",
    check_type: "data",
    reference: null,
    active: true,
    sort: 10,
  },
  {
    id: "rule-2",
    key: "power_of_attorney",
    label: "Power of attorney on file",
    scope: "customer",
    check_type: "data",
    reference: "Art. 18",
    active: false,
    sort: 20,
  },
];

const CONTROLS = [
  {
    id: "ctl-1",
    entity_id: "ent-1",
    supplier: "Q8",
    period: "2026-05",
    slot: "H1",
    country: "LV",
    status: "missing",
    txn_count: 12,
    waived: false,
    note: null,
  },
  {
    id: "ctl-2",
    entity_id: "ent-1",
    supplier: "BP",
    period: "2026-05",
    slot: "M",
    country: "",
    status: "received_doc",
    txn_count: 4,
    waived: false,
    note: null,
  },
];

const CADENCES = [{ id: "cad-1", supplier: "Q8", cadence: "semi-monthly" }];

const OVERRIDES = [
  {
    id: "ovr-1",
    entity_id: "ent-1",
    supplier: "Q8",
    refund_country: "LV",
    invoice_ref: "REF-77",
    target_invoice_id: "inv-1",
  },
];

// A figure with more significant digits than an IEEE-754 double can hold:
// Number("99999999999999.99") becomes 100000000000000. If any float path
// existed, these digits would not survive the round-trip.
const EXPECTATIONS = [
  {
    id: "exp-1",
    entity_id: "ent-1",
    supplier: "Q8",
    period: "2026-05",
    currency: "EUR",
    expected_lines: 42,
    expected_gross_local: "99999999999999.99",
    gross_local_tolerance: "0.02",
    expected_net_eur: "4761.90",
    expected_gross_eur: "5762.90",
    expected_diesel_litres: "1234.567",
  },
];

const STATUS_CODES = {
  auto: ["1A", "1B", "1C", "1E"],
  manual: ["2A", "2B", "3", "3A", "3B", "3C", "3D", "4", "4A", "5"],
};

const INVOICES = {
  items: [
    {
      id: "inv-1",
      vendor_id: "ven-1",
      invoice_number: "SUP-0001",
      issue_date: "2026-05-31",
      due_date: null,
      currency: "EUR",
      status: "approved",
      subtotal: "1000.00",
      tax_amount: "210.00",
      total: "1210.00",
      validation_status: "passed",
      source_filename: null,
    },
  ],
  total: 1,
  page: 1,
  page_size: 100,
};

const LIFECYCLE_PROSPECT = {
  entity_id: "ent-1",
  id: "lc-1",
  status: "prospect",
  countries: [],
};

const LIFECYCLE_ACTIVE = {
  entity_id: "ent-1",
  id: "lc-1",
  status: "active",
  countries: [
    { id: "ca-1", country: "LV", status: "active" },
    { id: "ca-2", country: "PL", status: "requested" },
  ],
};

const NEVER_ONBOARDED = { entity_id: "ent-1", id: null, status: null, countries: [] };

interface MockOpts {
  role?: Role;
  moduleEnabled?: boolean;
  rules?: unknown;
  rulesStatus?: number;
  rulesDelayMs?: number;
  controls?: unknown;
  cadences?: unknown;
  overrides?: unknown;
  expectations?: unknown;
  lifecycle?: unknown;
  /** Refusals keyed by a path fragment, e.g. "cadences" | "tie-out-expectations". */
  refuse?: Record<string, { status: number; code: string; detail: string }>;
  /** Collects every mutating body/URL, keyed by the matched path fragment. */
  captured?: Record<string, { method: string; url: string; body: unknown }[]>;
  /** Paths whose GET was issued — used to prove a tab reads only its own route. */
  seen?: string[];
}

const TRANSPORT_PATHS = [
  "checklist-rules",
  "receipt-controls",
  "cadences",
  "note-overrides",
  "tie-out-expectations",
  "status-codes",
  "customers",
];

async function mockApi(page: Page, opts: MockOpts = {}): Promise<void> {
  const {
    role = "owner",
    moduleEnabled = true,
    rules = RULES,
    rulesStatus,
    rulesDelayMs = 0,
    controls = CONTROLS,
    cadences = CADENCES,
    overrides = OVERRIDES,
    expectations = EXPECTATIONS,
    lifecycle = LIFECYCLE_ACTIVE,
    refuse = {},
    captured,
    seen,
  } = opts;

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (path === "/auth/me") return route.fulfill(json({ user: user(role), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(json([{ ...TRANSPORT_MODULE, enabled: moduleEnabled }]));
    if (path === "/issuer/registry") return route.fulfill(json([ENTITY]));
    if (path === "/invoices") return route.fulfill(json(INVOICES));

    if (seen && method === "GET") {
      const hit = TRANSPORT_PATHS.find((p) => path.includes(p));
      if (hit) seen.push(hit);
    }
    if (captured && method !== "GET") {
      const key = TRANSPORT_PATHS.find((p) => path.includes(p)) ?? path;
      (captured[key] ??= []).push({
        method,
        url: url.pathname + url.search,
        body: method === "DELETE" ? null : route.request().postDataJSON(),
      });
    }

    for (const [fragment, r] of Object.entries(refuse)) {
      if (path.includes(fragment) && method !== "GET") {
        return route.fulfill(json({ detail: r.detail, code: r.code }, r.status));
      }
    }

    if (path === "/transport/status-codes") return route.fulfill(json(STATUS_CODES));
    if (path.startsWith("/transport/checklist-rules")) {
      if (method === "GET") {
        if (rulesDelayMs) await new Promise((res) => setTimeout(res, rulesDelayMs));
        if (rulesStatus && rulesStatus >= 400)
          return route.fulfill(json({ detail: "boom", code: "mocked_failure" }, rulesStatus));
        return route.fulfill(json(rules));
      }
      return route.fulfill(json(path.endsWith("/seed") ? RULES : RULES[0]));
    }
    if (path.startsWith("/transport/receipt-controls"))
      return route.fulfill(json(method === "GET" ? controls : CONTROLS[0]));
    if (path.startsWith("/transport/cadences"))
      return route.fulfill(
        json(method === "GET" ? cadences : method === "DELETE" ? { removed: true } : CADENCES[0]),
      );
    if (path.startsWith("/transport/note-overrides"))
      return route.fulfill(json(method === "GET" ? overrides : OVERRIDES[0]));
    if (path.startsWith("/transport/tie-out-expectations")) {
      if (method === "DELETE") return route.fulfill({ status: 204, body: "" });
      return route.fulfill(json(method === "GET" ? expectations : EXPECTATIONS[0]));
    }
    if (path.includes("/transport/customers/")) return route.fulfill(json(lifecycle));

    return route.fulfill(json({}));
  });
}

/** Open `/vat-admin` and switch to a tab by its label. */
async function openTab(page: Page, label: string, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/vat-admin");
  if (label !== "Checklist rules") {
    await page.getByRole("tab", { name: label }).click();
  }
}

// ---------------------------------------------------------------------------
// Checklist rules (R45)
// ---------------------------------------------------------------------------

test("rules: renders each rule with its scope and active state", async ({ page }) => {
  await openTab(page, "Checklist rules");

  await expect(page.getByRole("heading", { name: "VAT refund configuration" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Customer master data complete" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "power_of_attorney" })).toBeVisible();
  await expect(page.getByText("Active", { exact: true })).toBeVisible();
  await expect(page.getByText("Off", { exact: true })).toBeVisible();
});

test("rules: an empty table is a first run, with the seed action and no alert", async ({ page }) => {
  await openTab(page, "Checklist rules", { rules: [] });

  await expect(page.getByText("No checklist rules in this workspace yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "Seed the default rules" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("rules: a 500 shows the error state, never the empty copy", async ({ page }) => {
  await openTab(page, "Checklist rules", { rulesStatus: 500 });

  await expect(
    page.getByRole("alert").filter({ hasText: "Couldn’t load the checklist rules" }),
  ).toBeVisible();
  await expect(page.getByText("No checklist rules in this workspace yet")).toHaveCount(0);
});

test("rules: a loading state renders before the API resolves", async ({ page }) => {
  await openTab(page, "Checklist rules", { rulesDelayMs: 1200 });

  await expect(page.getByRole("cell", { name: "Customer master data complete" })).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "Customer master data complete" })).toBeVisible({
    timeout: 10_000,
  });
});

test("rules: seeding posts to the committing seed route", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Checklist rules", { rules: [], captured });

  await page.getByRole("button", { name: "Seed the default rules" }).click();

  await expect.poll(() => (captured["checklist-rules"] ?? []).length).toBe(1);
  expect(captured["checklist-rules"][0].method).toBe("POST");
  expect(captured["checklist-rules"][0].url).toContain("/transport/checklist-rules/seed");
});

test("rules: switching a rule off posts {active:false} for that key", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Checklist rules", { captured });

  await page.getByRole("row", { name: /Customer master data complete/ }).getByRole("button").click();

  await expect.poll(() => (captured["checklist-rules"] ?? []).length).toBe(1);
  expect(captured["checklist-rules"][0].url).toContain("/checklist-rules/customer_data/active");
  expect(captured["checklist-rules"][0].body).toEqual({ active: false });
});

test("rules: checklist_rule_not_found renders its sentence, not the slug", async ({ page }) => {
  await openTab(page, "Checklist rules", {
    refuse: {
      "checklist-rules": {
        status: 404,
        code: "checklist_rule_not_found",
        detail: "No checklist rule 'customer_data'",
      },
    },
  });

  await page.getByRole("row", { name: /Customer master data complete/ }).getByRole("button").click();

  await expect(page.getByText("That checklist rule doesn’t exist in this workspace")).toBeVisible();
  await expect(page.getByText("checklist_rule_not_found")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Receipt control (G3.5) — advisory
// ---------------------------------------------------------------------------

test("controls: the grid renders each slot with its finding and count", async ({ page }) => {
  await openTab(page, "Receipt control");

  await expect(page.getByRole("cell", { name: "Q8" })).toBeVisible();
  await expect(page.getByText("Chase the supplier", { exact: true })).toBeVisible();
  await expect(page.getByText("Invoice stored", { exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "12", exact: true })).toBeVisible();
});

test("controls: the copy says the chase list blocks nothing", async ({ page }) => {
  await openTab(page, "Receipt control");

  // §4.19 made visible: a `missing` slot is a worklist row, and this page must
  // not imply it gates a claim or a close.
  await expect(page.getByText("This board is a chase list.")).toBeVisible();
  await expect(
    page.getByText("blocks no claim, halts no close and changes no figure"),
  ).toBeVisible();
  await expect(
    page.getByText("blocks no claim, halts no close and changes no figure").first(),
  ).toBeVisible();
});

test("controls: an override posts the mute and the note for that control id", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Receipt control", { captured });

  await page.getByRole("row", { name: /Q8/ }).getByRole("button", { name: "Mute or annotate" }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("textbox", { name: "Note" }).fill("Supplier confirmed nothing to invoice");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect.poll(() => (captured["receipt-controls"] ?? []).length).toBe(1);
  expect(captured["receipt-controls"][0].url).toContain("/receipt-controls/ctl-1/override");
  expect(captured["receipt-controls"][0].body).toEqual({
    waived: true,
    note: "Supplier confirmed nothing to invoice",
  });
});

test("controls: receipt_control_not_found renders its human message", async ({ page }) => {
  await openTab(page, "Receipt control", {
    refuse: {
      "receipt-controls": {
        status: 404,
        code: "receipt_control_not_found",
        detail: "No receipt control 'ctl-1'",
      },
    },
  });

  await page.getByRole("row", { name: /Q8/ }).getByRole("button", { name: "Mute or annotate" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(page.getByText("That receipt-control row isn’t available")).toBeVisible();
  await expect(page.getByText("receipt_control_not_found")).toHaveCount(0);
});

test("controls: a malformed period says so instead of spinning", async ({ page }) => {
  await openTab(page, "Receipt control");

  // No request is issued for a period that isn't YYYY-MM, so the panel must say
  // what shape it wants rather than leave a skeleton running forever. A
  // well-formed but wrong period is still the service's own `invalid_period`.
  await page.getByRole("textbox", { name: "Period" }).fill("2026-1");
  await expect(page.getByText("Type a period as YYYY-MM")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Q8" })).toHaveCount(0);
});

test("controls: an empty period shows the empty copy, never an alert", async ({ page }) => {
  await openTab(page, "Receipt control", { controls: [] });

  await expect(page.getByText("Nothing recorded for this period")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Cadences (G3.5)
// ---------------------------------------------------------------------------

test("cadences: assigning one puts the chosen value on the supplier's route", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Cadences", { captured });

  await expect(page.getByRole("cell", { name: "semi-monthly" })).toBeVisible();
  await page.getByRole("textbox", { name: "Supplier" }).fill("DKV");
  await page.getByRole("combobox", { name: "Cadence" }).selectOption("monthly-per-country");
  await page.getByRole("button", { name: "Assign cadence" }).click();

  await expect.poll(() => (captured.cadences ?? []).length).toBe(1);
  expect(captured.cadences[0].method).toBe("PUT");
  expect(captured.cadences[0].url).toContain("/transport/cadences/DKV");
  expect(captured.cadences[0].body).toEqual({ cadence: "monthly-per-country" });
});

test("cadences: removing one calls DELETE on that supplier", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Cadences", { captured });

  await page.getByRole("button", { name: "Back to the default" }).click();

  await expect.poll(() => (captured.cadences ?? []).length).toBe(1);
  expect(captured.cadences[0].method).toBe("DELETE");
  expect(captured.cadences[0].url).toContain("/transport/cadences/Q8");
});

test("cadences: invalid_cadence renders its sentence, not the slug", async ({ page }) => {
  await openTab(page, "Cadences", {
    refuse: {
      cadences: {
        status: 422,
        code: "invalid_cadence",
        detail: "'weekly' is not a valid cadence — use one of: semi-monthly, monthly",
      },
    },
  });

  await page.getByRole("textbox", { name: "Supplier" }).fill("DKV");
  await page.getByRole("button", { name: "Assign cadence" }).click();

  await expect(page.getByText("That isn’t an invoicing cadence this service recognises")).toBeVisible();
  await expect(page.getByText("invalid_cadence")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Note→invoice-ref overrides (R16)
// ---------------------------------------------------------------------------

test("overrides: the list renders and offers NO delete control", async ({ page }) => {
  await openTab(page, "Invoice references");

  await expect(page.getByRole("cell", { name: "REF-77" })).toBeVisible();
  // WO-77 decision 6: the service has no removal function, so the UI invents
  // no button for one. Asserted by absence.
  await expect(page.getByRole("button", { name: /Remove|Delete/ })).toHaveCount(0);
  await expect(page.getByText("there is no remove action here")).toBeVisible();
});

test("overrides: saving posts the whole natural key plus the target", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Invoice references", { captured });

  await page.getByRole("combobox", { name: "Legal entity" }).selectOption("ent-1");
  await page.getByRole("textbox", { name: "Supplier" }).fill("Q8");
  await page.getByRole("textbox", { name: "Refund country" }).fill("lv");
  await page.getByRole("textbox", { name: "Statement reference" }).fill("REF-88");
  await page.getByRole("combobox", { name: "Supplier invoice it means" }).selectOption("inv-1");
  await page.getByRole("button", { name: "Save mapping" }).click();

  await expect.poll(() => (captured["note-overrides"] ?? []).length).toBe(1);
  expect(captured["note-overrides"][0].method).toBe("PUT");
  expect(captured["note-overrides"][0].body).toEqual({
    entity_id: "ent-1",
    supplier: "Q8",
    refund_country: "LV",
    invoice_ref: "REF-88",
    target_invoice_id: "inv-1",
  });
});

test("overrides: override_target_not_registered renders its sentence", async ({ page }) => {
  await openTab(page, "Invoice references", {
    refuse: {
      "note-overrides": {
        status: 422,
        code: "override_target_not_registered",
        detail: "Invoice 'inv-1' is not registered for Q8 in LV",
      },
    },
  });

  await page.getByRole("combobox", { name: "Legal entity" }).selectOption("ent-1");
  await page.getByRole("textbox", { name: "Supplier" }).fill("Q8");
  await page.getByRole("textbox", { name: "Refund country" }).fill("LV");
  await page.getByRole("textbox", { name: "Statement reference" }).fill("REF-88");
  await page.getByRole("combobox", { name: "Supplier invoice it means" }).selectOption("inv-1");
  await page.getByRole("button", { name: "Save mapping" }).click();

  await expect(
    page.getByText("That invoice isn’t registered for this supplier and country"),
  ).toBeVisible();
  await expect(page.getByText("override_target_not_registered")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Tie-out expectations (R25 regime 2) — a real gate
// ---------------------------------------------------------------------------

test("tieout: figures render exactly from the wire string, with no float round-trip", async ({
  page,
}) => {
  await openTab(page, "Tie-out expectations");

  // Number("99999999999999.99") === 100000000000000 — a float path would print
  // €100,000,000,000,000.00 here.
  await expect(page.getByText("€99,999,999,999,999.99")).toBeVisible();
  await expect(page.getByText("€100,000,000,000,000.00")).toHaveCount(0);
  // Litres are not money and carry no symbol; the exact digits survive.
  await expect(page.getByRole("cell", { name: "1234.567" })).toBeVisible();
});

test("tieout: the copy states that a typed expectation halts the close", async ({ page }) => {
  await openTab(page, "Tie-out expectations");

  await expect(page.getByText("These stop the monthly close.")).toBeVisible();
  await expect(page.getByText("absence never halts anything")).toBeVisible();
});

test("tieout: an upsert posts the typed decimal strings verbatim", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Tie-out expectations", { captured });

  await page.getByRole("combobox", { name: "Legal entity" }).selectOption("ent-1");
  await page.getByRole("textbox", { name: "Supplier" }).fill("Q8");
  await page.getByRole("textbox", { name: "Lines on the invoice" }).fill("42");
  await page.getByRole("textbox", { name: "Gross, as invoiced" }).fill("99999999999999.99");
  await page.getByRole("textbox", { name: "Net, in EUR" }).fill("4761.90");
  await page.getByRole("button", { name: "Save expectation" }).click();

  await expect.poll(() => (captured["tie-out-expectations"] ?? []).length).toBe(1);
  const body = captured["tie-out-expectations"][0].body as Record<string, unknown>;
  expect(captured["tie-out-expectations"][0].method).toBe("PUT");
  // The exact digits the operator typed — not a parsed, re-serialized number.
  expect(body.expected_gross_local).toBe("99999999999999.99");
  expect(body.expected_net_eur).toBe("4761.90");
  expect(body.gross_local_tolerance).toBe("0.02");
  // Untyped optional figures are null (genuinely not typed), never zero.
  expect(body.expected_gross_eur).toBeNull();
  expect(body.expected_diesel_litres).toBeNull();
  // The one integer field: a line COUNT, not money.
  expect(body.expected_lines).toBe(42);
  expect(body.supplier).toBe("Q8");
  // The period the panel is showing is the period the expectation is typed for —
  // the natural key must be complete or the upsert would land on the wrong row.
  expect(body.period).toMatch(/^\d{4}-\d{2}$/);
  expect(body.entity_id).toBe("ent-1");
  expect(body.currency).toBe("EUR");
});

test("tieout: invalid_gross_tolerance renders the band, not the slug", async ({ page }) => {
  await openTab(page, "Tie-out expectations", {
    refuse: {
      "tie-out-expectations": {
        status: 422,
        code: "invalid_gross_tolerance",
        detail: "Tolerance 0.06 is outside [0.02, 0.05]",
      },
    },
  });

  await page.getByRole("combobox", { name: "Legal entity" }).selectOption("ent-1");
  await page.getByRole("textbox", { name: "Supplier" }).fill("Q8");
  await page.getByRole("textbox", { name: "Lines on the invoice" }).fill("42");
  await page.getByRole("button", { name: "Save expectation" }).click();

  await expect(page.getByText("That tolerance is outside the band this service allows")).toBeVisible();
  await expect(page.getByText("Use a value between 0.02 and 0.05")).toBeVisible();
  await expect(page.getByText("invalid_gross_tolerance")).toHaveCount(0);
});

test("tieout: removing one sends the natural key as query params", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openTab(page, "Tie-out expectations", { captured });

  await page.getByRole("button", { name: "Stop checking" }).click();

  await expect.poll(() => (captured["tie-out-expectations"] ?? []).length).toBe(1);
  const call = captured["tie-out-expectations"][0];
  expect(call.method).toBe("DELETE");
  expect(call.url).toContain("entity_id=ent-1");
  expect(call.url).toContain("supplier=Q8");
  expect(call.url).toContain("period=2026-05");
  expect(call.url).toContain("currency=EUR");
});

test("tieout: an empty period shows the empty copy, never an alert", async ({ page }) => {
  await openTab(page, "Tie-out expectations", { expectations: [] });

  await expect(page.getByText("Nothing typed for this period")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Status-code vocabulary (R17) — reference only
// ---------------------------------------------------------------------------

test("codes: the vocabulary renders the system and manual codes separately", async ({ page }) => {
  await openTab(page, "Status codes");

  await expect(page.getByRole("heading", { name: "Set by the service" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Set by you, after filing" })).toBeVisible();
  await expect(page.getByText("1A", { exact: true })).toBeVisible();
  await expect(page.getByText("3D", { exact: true })).toBeVisible();
  // The VAT_SUBMIT action itself is not duplicated here — it is linked.
  await expect(page.getByRole("link", { name: "Open a claim to set its code" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Set status code/ })).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Tab isolation, permissions, module and nav gating
// ---------------------------------------------------------------------------

test("tabs: a panel reads only its own route", async ({ page }) => {
  const seen: string[] = [];
  await openTab(page, "Cadences", { seen });

  await expect(page.getByRole("cell", { name: "semi-monthly" })).toBeVisible();
  expect(seen).toContain("cadences");
  expect(seen).not.toContain("receipt-controls");
  expect(seen).not.toContain("tie-out-expectations");
  expect(seen).not.toContain("note-overrides");
});

test("permissions: a read-only role sees every read and no mutation", async ({ page }) => {
  await openTab(page, "Checklist rules", { role: "auditor" });

  await expect(page.getByRole("cell", { name: "Customer master data complete" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Seed the default rules" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Switch (on|off)/ })).toHaveCount(0);

  await page.getByRole("tab", { name: "Cadences" }).click();
  await expect(page.getByRole("cell", { name: "semi-monthly" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Assign cadence" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Back to the default" })).toHaveCount(0);

  await page.getByRole("tab", { name: "Tie-out expectations" }).click();
  await expect(page.getByRole("button", { name: "Save expectation" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop checking" })).toHaveCount(0);

  await page.getByRole("tab", { name: "Receipt control" }).click();
  await expect(page.getByRole("button", { name: "Mute or annotate" })).toHaveCount(0);

  await page.getByRole("tab", { name: "Invoice references" }).click();
  await expect(page.getByRole("button", { name: "Save mapping" })).toHaveCount(0);
});

test("permissions: an accountant (VAT_WRITE, not VAT_SUBMIT) sees every config mutation", async ({
  page,
}) => {
  await openTab(page, "Checklist rules", { role: "accountant" });

  await expect(page.getByRole("button", { name: "Seed the default rules" })).toBeVisible();
  await page.getByRole("tab", { name: "Cadences" }).click();
  await expect(page.getByRole("button", { name: "Assign cadence" })).toBeVisible();
  await page.getByRole("tab", { name: "Tie-out expectations" }).click();
  await expect(page.getByRole("button", { name: "Save expectation" })).toBeVisible();
});

test("module: with transport off both pages show the notice and no table", async ({ page }) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/vat-admin");
  await expect(page.getByText("isn't active", { exact: false })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Cadences" })).toHaveCount(0);

  await page.goto("/vat-customers");
  await expect(page.getByText("isn't active", { exact: false })).toBeVisible();
});

test("nav: the configuration entries are permission- and module-gated", async ({ page }) => {
  await mockApi(page, { role: "auditor" });
  await page.goto("/vat-admin");
  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link", { name: "VAT configuration" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Customer activation" })).toBeVisible();

  await mockApi(page, { role: "user" });
  await page.goto("/vat-admin");
  await expect(page.getByRole("navigation").getByRole("link", { name: "VAT configuration" })).toHaveCount(0);

  await mockApi(page, { moduleEnabled: false });
  await page.goto("/vat-admin");
  await expect(page.getByRole("navigation").getByRole("link", { name: "VAT configuration" })).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Customer lifecycle + per-country activation (R44)
// ---------------------------------------------------------------------------

async function openCustomers(page: Page, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/vat-customers");
  await page.getByRole("combobox", { name: "Legal entity" }).selectOption("ent-1");
}

test("lifecycle: an entity is chosen before anything is read", async ({ page }) => {
  await mockApi(page);
  await page.goto("/vat-customers");

  await expect(page.getByText("Pick an entity to see where it stands")).toBeVisible();
});

test("lifecycle: a never-onboarded entity reads as such, not as an error", async ({ page }) => {
  await openCustomers(page, { lifecycle: NEVER_ONBOARDED });

  await expect(page.getByText("Never onboarded")).toBeVisible();
  await expect(
    page.getByText("The claim gate treats that exactly like not-active"),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Record as a prospect" })).toBeVisible();
});

test("lifecycle: a prospect offers promotion and NOT activation", async ({ page }) => {
  await openCustomers(page, { lifecycle: LIFECYCLE_PROSPECT });

  // The state badge AND the ladder both name it — an operator can't misread
  // where this customer stands.
  await expect(page.getByText("Prospect", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("Customer lifecycle").getByText("Prospect")).toBeVisible();
  await expect(page.getByRole("button", { name: "Promote to onboarding" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate for refund claims" })).toHaveCount(0);
});

test("lifecycle: each transition posts its own route", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openCustomers(page, { lifecycle: LIFECYCLE_PROSPECT, captured });

  await page.getByRole("button", { name: "Promote to onboarding" }).click();

  await expect.poll(() => (captured.customers ?? []).length).toBe(1);
  expect(captured.customers[0].url).toContain("/transport/customers/ent-1/promote");
});

test("lifecycle: an active customer can be returned to onboarding or set inactive", async ({
  page,
}) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openCustomers(page, { captured });

  await expect(page.getByText("Open to refund claims", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Return to onboarding" }).click();

  await expect.poll(() => (captured.customers ?? []).length).toBe(1);
  expect(captured.customers[0].url).toContain("/transport/customers/ent-1/activation");
  expect(captured.customers[0].body).toEqual({ active: false });
  await expect(page.getByRole("button", { name: "Set inactive" })).toBeVisible();
});

test("lifecycle: not_a_prospect renders its sentence, not the slug", async ({ page }) => {
  await openCustomers(page, {
    lifecycle: LIFECYCLE_PROSPECT,
    refuse: {
      customers: {
        status: 409,
        code: "not_a_prospect",
        detail: "Customer is 'pending', not 'prospect'",
      },
    },
  });

  await page.getByRole("button", { name: "Promote to onboarding" }).click();

  await expect(page.getByText("This customer isn’t a prospect any more")).toBeVisible();
  await expect(page.getByText("not_a_prospect")).toHaveCount(0);
});

test("countries: each country shows its state and the step legal from it", async ({ page }) => {
  await openCustomers(page);

  await expect(page.getByText("LV", { exact: true })).toBeVisible();
  await expect(page.getByText("Claims for this country pass the activation gate.")).toBeVisible();
  await expect(page.getByText("Registration is being gathered", { exact: false })).toBeVisible();
  // The active one can be deactivated; the requested one activated.
  await expect(page.getByRole("button", { name: "Deactivate" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate", exact: true })).toBeVisible();
});

test("countries: requesting one posts the country's own route", async ({ page }) => {
  const captured: Record<string, { method: string; url: string; body: unknown }[]> = {};
  await openCustomers(page, { captured });

  await page.getByRole("textbox", { name: "Add a refunding country" }).fill("ee");
  await page.getByRole("button", { name: "Request", exact: true }).click();

  await expect.poll(() => (captured.customers ?? []).length).toBe(1);
  expect(captured.customers[0].url).toContain("/transport/customers/ent-1/countries/EE/request");
});

test("countries: country_not_requested renders its sentence, not the slug", async ({ page }) => {
  await openCustomers(page, {
    refuse: {
      customers: {
        status: 409,
        code: "country_not_requested",
        detail: "Country 'EE' was never requested",
      },
    },
  });

  await page.getByRole("button", { name: "Activate", exact: true }).click();

  await expect(page.getByText("That country was never requested for this customer")).toBeVisible();
  await expect(page.getByText("country_not_requested")).toHaveCount(0);
});

test("lifecycle: a read-only role sees the state and no transition", async ({ page }) => {
  await openCustomers(page, { role: "auditor" });

  await expect(page.getByText("Open to refund claims", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Set inactive" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Deactivate" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Add a refunding country" })).toHaveCount(0);
});

test("lifecycle: a 500 shows the error state, not a blank state", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/v1/transport/customers/**", (route: Route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "boom", code: "mocked_failure" }),
    }),
  );
  await page.goto("/vat-customers");
  await page.getByRole("combobox", { name: "Legal entity" }).selectOption("ent-1");

  await expect(
    page.getByRole("alert").filter({ hasText: "Couldn’t load this customer’s status" }),
  ).toBeVisible();
});
