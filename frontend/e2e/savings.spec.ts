import { test, expect, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/**
 * The NEGOTIATION-EVIDENCE workspace (WO-90) over the LIVE app shell, API mocked
 * via `page.route` — the deterministic pattern of `recovery.spec.ts`, no backend
 * involved.
 *
 * What this suite is actually proving:
 *  - the three panels render the WO-87 wire shapes field for field,
 *  - **R53's second framing survives contact with a UI**: the framing string is
 *    rendered VERBATIM off the wire on all three panels, the new modules use no
 *    word from the contract-breach vocabulary family (scanned at source, with a
 *    seeded-violation self-test so the scan itself cannot quietly stop working),
 *    the page offers no control that transmits anything, and there is no link or
 *    route from here into the contract-breach flow,
 *  - **R52 is legible**: each overpay panel renders the service's own `grain`
 *    label and the page states in words that the two totals are not expected to
 *    agree; the two euro totals never appear together,
 *  - a day with no rival is a NAMED COUNT with a reason, never a €0.00 finding,
 *  - money renders from the wire STRING with no float round-trip anywhere —
 *    asserted both on a value a double cannot hold and by a source grep,
 *  - permission-gated visibility (granted/denied), module gating, and the
 *    loading / empty / error / refusal states on every panel.
 *
 * Synthetic fixtures only (master context §10): fictional supplier codes,
 * stations and figures. No Fleet Fuel bytes, and no literal shaped like a real
 * VAT id, IBAN or registration number.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

type Role = "owner" | "finance_manager" | "accountant" | "auditor" | "user" | "user_free";

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

// More significant digits than an IEEE-754 double can hold:
// Number("99999999999999.99") is 100000000000000. If any float path existed
// between the wire and the screen, these digits would not survive.
const HUGE = "99999999999999.99";

// The two constants the service actually emits (`app/services/transport/
// savings.py`). They are FIXTURES here — the page must render whatever the wire
// says, which is the whole point of carrying them on the response.
const PRICE_BASIS = "NET EUR/L, final — VAT excluded, rebates applied";
const FRAMING = "Negotiation evidence, NOT a contractual claim-back";
const GRAIN_SAME_DAY = "same-day, same-country cheapest rival";
const GRAIN_COUNTRY_MONTH = "country × month, best-of-your-own-suppliers";

const SAME_DAY = {
  period: "2026-05",
  country: null,
  currency: "EUR",
  price_basis: PRICE_BASIS,
  legal_framing: FRAMING,
  grain: GRAIN_SAME_DAY,
  product_group: "Diesel",
  findings: [
    {
      country: "LV",
      txn_date: "2026-05-14",
      supplier: "CARDNET",
      litres: "12400.000",
      eur_l_eff: "1.4820",
      cheapest_rival_supplier: "NORDROUTE",
      cheapest_rival_eur_l_eff: "1.4400",
      delta_eur_l: "0.0420",
      avoidable_eur: HUGE,
      suppliers_that_day: 3,
      lines: 7,
    },
    {
      country: "PL",
      txn_date: "2026-05-21",
      supplier: "NORDROUTE",
      litres: "2100.000",
      eur_l_eff: "1.5100",
      cheapest_rival_supplier: "CARDNET",
      cheapest_rival_eur_l_eff: "1.4950",
      delta_eur_l: "0.0150",
      avoidable_eur: "31.50",
      suppliers_that_day: 2,
      lines: 2,
    },
  ],
  by_supplier: [
    { country: "LV", supplier: "CARDNET", litres: "12400.000", avoidable_eur: HUGE, days: 1 },
    { country: "PL", supplier: "NORDROUTE", litres: "2100.000", avoidable_eur: "31.50", days: 1 },
  ],
  avoidable_eur: HUGE,
  lines_compared: 9,
  days_compared: 2,
  days_without_a_rival: 4,
  lines_skipped_zero_qty: 1,
};

const SAME_DAY_NOTHING = {
  ...SAME_DAY,
  findings: [],
  by_supplier: [],
  avoidable_eur: "0.00",
  lines_compared: 3,
  days_compared: 0,
  days_without_a_rival: 3,
  lines_skipped_zero_qty: 0,
};

const BENCHMARK = {
  period: "2026-05",
  country: null,
  currency: "EUR",
  price_basis: PRICE_BASIS,
  legal_framing: FRAMING,
  grain: GRAIN_COUNTRY_MONTH,
  product_group: "Diesel",
  rows: [
    {
      country: "LV",
      supplier: "CARDNET",
      litres: "18200.000",
      eur_l_eff: "1.4750",
      best_supplier: "NORDROUTE",
      best_eur_l_eff: "1.4400",
      gap_eur_l: "0.0350",
      benchmark_gap_eur: "3180.00",
      lines: 12,
    },
    {
      country: "LV",
      supplier: "NORDROUTE",
      litres: "9100.000",
      eur_l_eff: "1.4400",
      best_supplier: "NORDROUTE",
      best_eur_l_eff: "1.4400",
      gap_eur_l: "0.0000",
      benchmark_gap_eur: "0.00",
      lines: 6,
    },
  ],
  benchmark_gap_eur: "3180.00",
  countries_compared: 1,
  suppliers_compared: 2,
  lines_compared: 18,
  lines_skipped_zero_qty: 0,
};

const BENCHMARK_EMPTY = {
  ...BENCHMARK,
  rows: [],
  benchmark_gap_eur: "0.00",
  countries_compared: 0,
  suppliers_compared: 0,
  lines_compared: 0,
};

const REBATE = {
  period: "2026-05",
  supplier: null,
  currency: "EUR",
  price_basis: PRICE_BASIS,
  legal_framing: FRAMING,
  tolerance_eur_l: "0.005",
  expectations: [
    { supplier: "CARDNET", country: "LV", typical_eur_l: "0.0350", learned_from_lines: 214 },
    { supplier: "NORDROUTE", country: "PL", typical_eur_l: "0.0180", learned_from_lines: 7 },
  ],
  findings: [
    {
      fuel_transaction_id: "ftx-77",
      entity_id: "ent-1",
      supplier: "CARDNET",
      country: "LV",
      station: "Riga North",
      txn_date: "2026-05-09",
      litres: "820.000",
      eur_l_doc: "1.5100",
      eur_l_eff: "1.5100",
      applied_eur_l: "0.0000",
      typical_eur_l: "0.0350",
      expected_rebate_eur: "28.70",
      learned_from_lines: 214,
    },
  ],
  expected_rebate_eur: "28.70",
  lines_examined: 41,
  lines_with_a_rebate: 38,
  lines_without_an_expectation: 2,
  lines_skipped_zero_qty: 0,
};

const REBATE_SILENT = {
  ...REBATE,
  expectations: [],
  findings: [],
  expected_rebate_eur: "0.00",
  lines_examined: 5,
  lines_with_a_rebate: 0,
  lines_without_an_expectation: 5,
};

interface MockOpts {
  role?: Role;
  moduleEnabled?: boolean;
  sameDay?: unknown;
  benchmark?: unknown;
  rebate?: unknown;
  /** Refusals keyed by a path fragment — applied to the GET itself, since every
   * route on this surface is a GET. */
  refuse?: Record<string, { status: number; code: string; detail: string }>;
  /** Fail with a non-refusal HTTP error (the QueryState error lane). */
  status?: number;
  delayMs?: number;
}

async function mockApi(page: Page, opts: MockOpts = {}): Promise<void> {
  const {
    role = "owner",
    moduleEnabled = true,
    sameDay = SAME_DAY,
    benchmark = BENCHMARK,
    rebate = REBATE,
    refuse = {},
    status,
    delayMs = 0,
  } = opts;

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: user(role), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(json([{ ...TRANSPORT_MODULE, enabled: moduleEnabled }]));

    for (const [fragment, r] of Object.entries(refuse)) {
      if (path.includes(fragment)) {
        return route.fulfill(json({ detail: r.detail, code: r.code }, r.status));
      }
    }

    if (path.startsWith("/transport/savings/")) {
      if (delayMs) await new Promise((res) => setTimeout(res, delayMs));
      if (status && status >= 400)
        return route.fulfill(json({ detail: "boom", code: "mocked_failure" }, status));
      if (path.includes("same-day")) return route.fulfill(json(sameDay));
      if (path.includes("internal-benchmark")) return route.fulfill(json(benchmark));
      if (path.includes("expected-rebate")) return route.fulfill(json(rebate));
    }

    return route.fulfill(json({}));
  });
}

async function open(page: Page, tab: string, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/savings");
  if (tab !== "Same-day overpay") await page.getByRole("tab", { name: tab }).click();
}

// ---------------------------------------------------------------------------
// 1. Same-day overpay — R52 grain (a)
// ---------------------------------------------------------------------------

test("same-day: each finding renders its litres, both prices, the delta and the euro", async ({
  page,
}) => {
  await open(page, "Same-day overpay");
  const row = page.getByRole("row", { name: /CARDNET/ }).first();
  await expect(row).toContainText("2026-05-14");
  await expect(row).toContainText("LV");
  await expect(row).toContainText("12400.000");
  await expect(row).toContainText("1.4820");
  await expect(row).toContainText("NORDROUTE");
  await expect(row).toContainText("1.4400");
  await expect(row).toContainText("0.0420");
  await expect(row).toContainText("3 suppliers that day");
});

test("same-day: the per-supplier attribution renders with its days count", async ({ page }) => {
  await open(page, "Same-day overpay");
  const card = page.locator("section,div").filter({ hasText: "By supplier and country" }).last();
  await expect(card).toContainText("NORDROUTE");
  await expect(card).toContainText("2100.000");
});

test("same-day: the total renders exactly as the wire string", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
});

test("same-day: a day with no rival is a named count, never a €0.00 finding", async ({ page }) => {
  await open(page, "Same-day overpay", { sameDay: SAME_DAY_NOTHING });
  await expect(page.getByText("Days with no rival")).toBeVisible();
  await expect(page.getByText("3 days this month had no rival to compare against")).toBeVisible();
  await expect(
    page.getByText(/no rival price to compare against and no finding was produced/),
  ).toBeVisible();
  // The zero-state says the comparison could not be made — it never prints a
  // euro figure for those days.
  await expect(page.getByText("No day in this month priced above a rival")).toBeVisible();
});

test("same-day: a month with nothing found renders the zero-state, not an error", async ({
  page,
}) => {
  await open(page, "Same-day overpay", { sameDay: SAME_DAY_NOTHING });
  await expect(page.getByText("No day in this month priced above a rival")).toBeVisible();
  await expect(page.getByText("Couldn’t run the same-day comparison")).toHaveCount(0);
  await expect(page.getByText("Nothing to attribute")).toBeVisible();
});

test("same-day: the response's own grain string is rendered", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText(GRAIN_SAME_DAY, { exact: false })).toBeVisible();
});

test("same-day: the page states why there is no supplier filter", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText(/deliberately no supplier filter/)).toBeVisible();
  await expect(page.getByLabel("Supplier")).toHaveCount(0);
});

test("same-day: a loading state renders before the API resolves", async ({ page }) => {
  await mockApi(page, { delayMs: 1500 });
  await page.goto("/savings");
  await expect(page.getByRole("heading", { name: "Negotiation evidence" })).toBeVisible();
  await expect(page.getByText(GRAIN_SAME_DAY, { exact: false })).toHaveCount(0);
});

test("same-day: a 500 renders the error state", async ({ page }) => {
  await open(page, "Same-day overpay", { status: 500 });
  await expect(page.getByText("Couldn’t run the same-day comparison")).toBeVisible();
});

test("same-day: fx_rate_unavailable renders its sentence, not the slug", async ({ page }) => {
  await open(page, "Same-day overpay", {
    refuse: {
      "savings/same-day": {
        status: 422,
        code: "fx_rate_unavailable",
        detail: "2 line(s) in scope have no established EUR basis — PLN (2 lines).",
      },
    },
  });
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("No ECB rate is available");
  await expect(alert).toContainText("PLN (2 lines)");
  await expect(page.getByText("fx_rate_unavailable")).toHaveCount(0);
});

test("same-day: invalid_country renders its sentence, not the slug", async ({ page }) => {
  await open(page, "Same-day overpay", {
    refuse: {
      "savings/same-day": {
        status: 422,
        code: "invalid_country",
        detail: "'ZZZ' is not an ISO 3166-1 alpha-2 country",
      },
    },
  });
  await expect(page.getByRole("alert")).toContainText("isn’t a country code this service accepts");
  await expect(page.getByText("invalid_country")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 2. Internal benchmark — R52 grain (b)
// ---------------------------------------------------------------------------

test("benchmark: each row renders its price, the best supplier and the gap", async ({ page }) => {
  await open(page, "Internal benchmark");
  const row = page.getByRole("row", { name: /CARDNET/ }).first();
  await expect(row).toContainText("18200.000");
  await expect(row).toContainText("1.4750");
  await expect(row).toContainText("NORDROUTE");
  await expect(row).toContainText("0.0350");
  await expect(row).toContainText("€3,180.00");
});

test("benchmark: the best supplier stays on the table with a zero gap", async ({ page }) => {
  await open(page, "Internal benchmark");
  const row = page.getByRole("row", { name: /NORDROUTE/ }).last();
  await expect(row).toContainText("0.0000");
  await expect(row).toContainText("€0.00");
});

test("benchmark: the total renders exactly as the wire string", async ({ page }) => {
  await open(page, "Internal benchmark");
  await expect(page.getByText("Gap to your own best supplier")).toBeVisible();
  await expect(page.getByText("€3,180.00").first()).toBeVisible();
});

test("benchmark: an empty month renders the zero-state", async ({ page }) => {
  await open(page, "Internal benchmark", { benchmark: BENCHMARK_EMPTY });
  await expect(page.getByText("No diesel volume to benchmark in this month")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 3. R52 — the two grains, and the fact that they do not reconcile
// ---------------------------------------------------------------------------

test("r52: both grains are labelled with the service's own grain string", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText(GRAIN_SAME_DAY, { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "Internal benchmark" }).click();
  await expect(page.getByText(GRAIN_COUNTRY_MONTH, { exact: false })).toBeVisible();
});

test("r52: the page states that the two grains do not reconcile, and why", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText(/not expected to agree/)).toBeVisible();
  await expect(page.getByText(/Different comparison, different denominator/)).toBeVisible();
  await expect(page.getByText(/neither number should be added to the other/)).toBeVisible();
});

test("r52: the two totals never appear as one figure", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
  await expect(page.getByText("€3,180.00")).toHaveCount(0);

  await page.getByRole("tab", { name: "Internal benchmark" }).click();
  await expect(page.getByText("€3,180.00").first()).toBeVisible();
  await expect(page.getByText("€99,999,999,999,999.99")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 4. Expected rebate
// ---------------------------------------------------------------------------

test("rebate: an expectation renders its typical €/L and the sample behind it", async ({
  page,
}) => {
  await open(page, "Expected rebate");
  const row = page.getByRole("row", { name: /CARDNET/ }).first();
  await expect(row).toContainText("0.0350");
  await expect(row).toContainText("214 lines");
});

test("rebate: a flagged line renders both prices, what was applied and the magnitude", async ({
  page,
}) => {
  await open(page, "Expected rebate");
  const row = page.getByRole("row", { name: /Riga North/ });
  await expect(row).toContainText("1.5100");
  await expect(row).toContainText("0.0000");
  await expect(row).toContainText("0.0350");
  await expect(row).toContainText("€28.70");
});

test("rebate: the tolerance the flag fires under is rendered", async ({ page }) => {
  await open(page, "Expected rebate");
  await expect(page.getByText(/below 0.005 €\/L/)).toBeVisible();
});

test("rebate: lines with no expectation are counted, not warned about", async ({ page }) => {
  await open(page, "Expected rebate");
  await expect(page.getByText("Lines with no expectation")).toBeVisible();
  await expect(page.getByText("No rebate history for that supplier and country yet")).toBeVisible();
});

test("rebate: with nothing learned yet the analysis is silent, not alarming", async ({ page }) => {
  await open(page, "Expected rebate", { rebate: REBATE_SILENT });
  await expect(page.getByText("Nothing learned yet")).toBeVisible();
  await expect(page.getByText("Every line in scope carries its usual rebate")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 5. R53 — the framing separation. The governing constraint of this order.
// ---------------------------------------------------------------------------

test("r53: the legal framing is rendered verbatim from the wire on all three panels", async ({
  page,
}) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText(FRAMING)).toBeVisible();
  await page.getByRole("tab", { name: "Internal benchmark" }).click();
  await expect(page.getByText(FRAMING)).toBeVisible();
  await page.getByRole("tab", { name: "Expected rebate" }).click();
  await expect(page.getByText(FRAMING)).toBeVisible();
});

test("r53: the price basis is rendered verbatim on all three panels", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(page.getByText(PRICE_BASIS)).toBeVisible();
  await page.getByRole("tab", { name: "Internal benchmark" }).click();
  await expect(page.getByText(PRICE_BASIS)).toBeVisible();
  await page.getByRole("tab", { name: "Expected rebate" }).click();
  await expect(page.getByText(PRICE_BASIS)).toBeVisible();
});

test("r53: the page states plainly that these figures oblige nobody", async ({ page }) => {
  await open(page, "Same-day overpay");
  await expect(
    page.getByText("Evidence for a negotiation — not an obligation on anyone"),
  ).toBeVisible();
  await expect(
    page.getByText(/nothing on this page is money anybody is contractually obliged to pay you/i),
  ).toBeVisible();
});

/**
 * The vocabulary the backend refuses to use for these figures — WO-87's own
 * list, matched at word boundaries so an innocent substring ("lowest" contains
 * "owes") is not a false positive while `recoverable_eur` is caught.
 */
const FORBIDDEN =
  /\brecover\w*|\bowed\b|\bowes\b|\bclaim\w*|\bdemand\w*|\bdue\b|\bdebt\w*|\bpayable\b/gi;

function forbiddenHits(source: string): string[] {
  return source.match(FORBIDDEN) ?? [];
}

function readSource(rel: string): string {
  const here = dirname(fileURLToPath(import.meta.url));
  return readFileSync(join(here, "..", rel), "utf8");
}

test("r53: the new modules carry no contract-breach vocabulary", () => {
  for (const rel of ["src/pages/Savings.tsx", "src/lib/transportSavings.ts"]) {
    expect(forbiddenHits(readSource(rel)), `${rel} must carry none of it`).toEqual([]);
  }
});

test("r53: the new wire interfaces carry no contract-breach vocabulary", () => {
  const types = readSource("src/lib/types.ts");
  const names = [
    "SameDayOverpayLine",
    "SupplierOverpayTotal",
    "SameDayOverpay",
    "InternalBenchmarkRow",
    "InternalBenchmark",
    "LearnedRebate",
    "MissingRebateLine",
    "ExpectedRebate",
  ];
  for (const name of names) {
    const m = new RegExp(`export interface ${name}\\s*\\{([^}]*)\\}`).exec(types);
    expect(m, `${name} must exist in lib/types.ts`).not.toBeNull();
    // Field names only — the surrounding prose explains WHY the vocabulary is
    // absent and necessarily names it.
    const fields = (m as RegExpExecArray)[1]
      .split("\n")
      .filter((line) => /^\s*\w+\??:/.test(line))
      .join("\n");
    expect(forbiddenHits(fields), `${name} field names`).toEqual([]);
  }
});

test("r53: the vocabulary scan detects a seeded violation", () => {
  // A scan that cannot fail proves nothing (WO-87 shipped its own seeded
  // violation for the same reason). Each of these is a plausible way the
  // separation would be undone by a well-meaning edit.
  expect(forbiddenHits("export const recoverable_eur = row.avoidable_eur;")).not.toEqual([]);
  expect(forbiddenHits("<th>Amount owed by the supplier</th>")).not.toEqual([]);
  expect(forbiddenHits('<Button>Open a claim-back</Button>')).not.toEqual([]);
  expect(forbiddenHits("// send the demand letter from here")).not.toEqual([]);
  // …and does not fire on an innocent substring.
  expect(forbiddenHits("the lowest price of the day was the cheapest rival")).toEqual([]);
});

test("r53: there is no route from this page into the contract-breach flow", async ({ page }) => {
  // Source: the page names no such path at all.
  expect(readSource("src/pages/Savings.tsx")).not.toContain("/overcharges");

  // DOM: nothing inside the page's own main region links there either (the app
  // shell's nav is a different surface and legitimately lists both).
  await open(page, "Same-day overpay");
  const hrefs = await page
    .locator("main a")
    .evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""));
  expect(hrefs.filter((h) => h.includes("overcharge"))).toEqual([]);
});

test("r53: the page exposes no mutating control", async ({ page }) => {
  await open(page, "Same-day overpay");
  // No form to post, and every button on the surface is a tab (the tablist).
  await expect(page.locator("main form")).toHaveCount(0);
  const buttons = page.locator("main button");
  const count = await buttons.count();
  for (let i = 0; i < count; i++) {
    await expect(buttons.nth(i)).toHaveAttribute("role", "tab");
  }
});

// ---------------------------------------------------------------------------
// 6. Permission, module and money
// ---------------------------------------------------------------------------

test("perm: a read-only role sees every figure on the surface", async ({ page }) => {
  await open(page, "Same-day overpay", { role: "user_free" });
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
  await expect(page.getByText(FRAMING)).toBeVisible();
  await expect(page.locator("main form")).toHaveCount(0);
});

test("perm: transport.read gates the nav entry (granted / denied)", async ({ page }) => {
  await mockApi(page, { role: "auditor" }); // AUDITOR holds TRANSPORT_READ
  await page.goto("/savings");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Negotiation evidence" }),
  ).toBeVisible();

  await mockApi(page, { role: "user" }); // EMPLOYEE holds neither VAT_READ nor TRANSPORT_READ
  await page.goto("/savings");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Negotiation evidence" }),
  ).toHaveCount(0);
});

test("perm: a server 403 renders through the refusal path", async ({ page }) => {
  await open(page, "Same-day overpay", {
    refuse: {
      "savings/same-day": {
        status: 403,
        code: "module_not_enabled",
        detail: "The Transport & VAT refunds module is not activated.",
      },
    },
  });
  await expect(page.getByRole("alert")).toContainText("module isn’t active for this workspace");
});

test("module: transport off renders the module notice and no nav entry", async ({ page }) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/savings");
  await expect(page.getByText(/Transport & VAT refunds/).first()).toBeVisible();
  await expect(page.getByRole("tab", { name: "Same-day overpay" })).toHaveCount(0);
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Negotiation evidence" }),
  ).toHaveCount(0);
});

test("money: the new modules perform no float arithmetic on an amount", () => {
  // `parseFloat`/`Number(`/`toFixed`/`Math.` are the four ways a decimal string
  // becomes an IEEE-754 double in this codebase. None may appear: every figure
  // is the server's own string, money rendered through `decimalMoney` (string
  // surgery, no arithmetic) and €/L and litres rendered as received.
  const banned = [/parseFloat/, /\bNumber\s*\(/, /toFixed/, /\bMath\./];
  for (const rel of ["src/pages/Savings.tsx", "src/lib/transportSavings.ts"]) {
    const src = readSource(rel);
    for (const pattern of banned) {
      expect(pattern.test(src), `${rel} must not contain ${pattern}`).toBe(false);
    }
  }
});
