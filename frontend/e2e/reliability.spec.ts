import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-Q — the supplier-reliability panel on /recovery, over the LIVE shell with
 * the API mocked.
 *
 * The design's governing constraint is that this surface reads as EVIDENCE, not
 * as a verdict on a counterparty, so these specs assert the three presentation
 * rules that carry it — and one of them is an ABSENCE, which is the only kind
 * of assertion that survives a future edit:
 *
 * 1. the server's framing renders VERBATIM (the SPA owns none of those words);
 * 2. no band ever renders without the rule that produced it;
 * 3. a thin sample renders its month count and NO band at all.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};
const TRANSPORT_MODULE = { key: "transport", name: "Transport & VAT refunds", enabled: true };

const FRAMING =
  "Computed from this workspace's own captured statements and contract terms over the " +
  "stated window. It describes patterns in the data — it is not an assessment of the " +
  "counterparty.";

const OVERCHARGE_RULE =
  "'recurring' at 3 or more cases in the window, or 5.00 EUR or more detected per 1,000 EUR of net spend";
const FX_RULE =
  "'recurring' when the median markup of the supplier's own stated rate over the ECB rate reaches 50 basis points";
const UNGOVERNED_RULE =
  "'recurring' when 10.00% or more of the supplier's validated lines are governed by no agreed term, or breached one";

const BOARD = {
  window_from: "2025-09",
  window_to: "2026-08",
  framing: FRAMING,
  thresholds: { is_default: true },
  suppliers: [
    {
      supplier: "RECURCO",
      overall: "recurring",
      active_months: 12,
      net_spend_eur: "40000.00",
      criteria: [
        {
          key: "overcharges",
          band: "recurring",
          rule: OVERCHARGE_RULE,
          figures: {
            cases: 3,
            detected_eur: "30.00",
            detected_eur_per_1000_spend: "0.75",
            outcomes: { ignored: 1, detected: 2 },
          },
        },
        {
          key: "exchange_rate_treatment",
          band: "clean",
          rule: FX_RULE,
          figures: { foreign_currency_lines: 0, median_markup_bps: null, measured_lines: 0 },
        },
        {
          key: "lines_never_agreed",
          band: "clean",
          rule: UNGOVERNED_RULE,
          figures: { lines_total: 12, lines_without_agreed_terms: 0, finding_share_pct: "0.00" },
        },
      ],
    },
    {
      supplier: "THINCO",
      overall: "insufficient_history",
      active_months: 2,
      net_spend_eur: "300.00",
      criteria: [],
    },
  ],
};

const EMPTY_DASHBOARD = {
  year: "2026",
  currency: "EUR",
  recovered_eur: "0.00",
  in_flight_eur: "0.00",
  overcharges_eur: "0.00",
  buckets: [],
  excluded: [],
  median_days_to_refund: null,
  currency_mismatch_claims: 0,
};

async function mockApi(page: Page, board: unknown = BOARD): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([TRANSPORT_MODULE]));
    if (path === "/transport/reliability") return route.fulfill(json(board));
    if (path.startsWith("/transport/recovery-dashboard"))
      return route.fulfill(json(EMPTY_DASHBOARD));
    return route.fulfill(json([]));
  });
}

test("the framing renders verbatim, above the bands", async ({ page }) => {
  await mockApi(page);
  await page.goto("/recovery");

  const panel = page.getByText("Supplier reliability").locator("..");
  await expect(panel).toBeVisible();
  // VERBATIM: the SPA holds none of these words, so a server that rewords the
  // framing reworded the page.
  await expect(page.getByText(FRAMING)).toBeVisible();
  await expect(page.getByText("Window 2025-09 to 2026-08", { exact: false })).toBeVisible();
  // The reader is told WHOSE thresholds produced the bands.
  await expect(
    page.getByText("using the platform's default thresholds", { exact: false }),
  ).toBeVisible();
});

test("every band renders with the rule that produced it", async ({ page }) => {
  await mockApi(page);
  await page.goto("/recovery");

  await expect(page.getByText("RECURCO")).toBeVisible();
  // Anchor on the rules themselves: a band shown without its rule is exactly
  // the verdict-shaped presentation the design forbids.
  await expect(page.getByText(OVERCHARGE_RULE)).toBeVisible();
  await expect(page.getByText(FX_RULE)).toBeVisible();
  await expect(page.getByText(UNGOVERNED_RULE)).toBeVisible();
  // …and the figures behind the band, including the IGNORED outcome, which
  // counts as evidence rather than disappearing.
  await expect(page.getByText("ignored: 1", { exact: false })).toBeVisible();
});

test("a thin sample shows its month count and no band", async ({ page }) => {
  await mockApi(page);
  await page.goto("/recovery");

  // Anchor first: the panel rendered.
  await expect(page.getByText("THINCO")).toBeVisible();
  await expect(
    page.getByText("2 month(s) of activity", { exact: false }),
  ).toBeVisible();
  // The absence that matters: no criterion rule appears for the thin supplier,
  // because it earned no band in either direction.
  const thinRow = page.getByText("THINCO").locator("xpath=ancestor::li[1]");
  await expect(thinRow.getByText(OVERCHARGE_RULE)).toHaveCount(0);
});

test("an empty window says so instead of showing an empty verdict", async ({ page }) => {
  await mockApi(page, { ...BOARD, suppliers: [] });
  await page.goto("/recovery");

  await expect(page.getByText("Supplier reliability")).toBeVisible();
  await expect(
    page.getByText("No supplier activity in this window yet", { exact: false }),
  ).toBeVisible();
});
