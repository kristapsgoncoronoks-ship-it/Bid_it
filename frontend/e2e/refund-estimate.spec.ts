import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The refund-estimate funnel (WO-AC; G4.8, R43, R53).
 *
 * WHAT THESE SPECS ARE ABOUT
 * ----------------------------
 * Not the arithmetic — the backend suite owns that. These are about the two
 * things a screen can get wrong on its own, both of which would mislead
 * someone holding a prospect's data:
 *
 * 1. **The number must not appear without its framing.** §2.3 calls this a
 *    sales preview and R53 forbids flattening that into the language used for
 *    a contractual claim-back. If the caveat can be scrolled past, the screen
 *    is presenting an upper bound as a figure.
 * 2. **`below_minimum: null` must not render as "clears the threshold".** It
 *    means the Art. 17 comparison could not be made in the country's own
 *    currency. Showing it as a pass tells an operator a check ran that did
 *    not.
 */

const ORG = { id: "org-1", name: "Demo Transport", plan: "pro" };

const TRANSPORT_MODULE = {
  key: "transport",
  name: "Transport & VAT refunds",
  enabled: true,
  description: "EU cross-border VAT refunds",
};

function user(role: string) {
  return { id: "u-1", email: "owner@example.com", name: "Owner", role };
}

const ESTIMATE = {
  network: "Eurowag",
  period: "2026-Q2",
  lines: 4,
  recoverable_eur: "1730.00",
  unconverted_lines: 1,
  warnings: [
    "1 line(s) could not be converted to EUR (no exchange rate on file for that currency and date) and are NOT in the figures above — the real opportunity is larger than this estimate shows.",
  ],
  caveat:
    "Indicative — verify before relying. This is a sales preview, never a filed figure: it assumes every invoiced euro of VAT is recoverable, before supplier registration, receipt control, document checks, waivers, national minimums and fees are applied. Each of those can only reduce it.",
  countries: [
    {
      country: "BE",
      lines: 2,
      litres: "200.00",
      vat_eur: "1630.00",
      vat_local: "1630.00",
      currency: "EUR",
      below_minimum: false,
      threshold: "400.00",
      threshold_currency: "EUR",
      unconverted_lines: 0,
    },
    {
      country: "FR",
      lines: 1,
      litres: "100.00",
      vat_eur: "100.00",
      vat_local: "100.00",
      currency: "EUR",
      below_minimum: true,
      threshold: "400.00",
      threshold_currency: "EUR",
      unconverted_lines: 0,
    },
    {
      // The third state: mixed currencies, so Art. 17 could not be compared.
      country: "SE",
      lines: 1,
      litres: "50.00",
      vat_eur: "0.00",
      vat_local: null,
      currency: null,
      below_minimum: null,
      threshold: "4000",
      threshold_currency: "SEK",
      unconverted_lines: 1,
    },
  ],
};

interface MockOpts {
  role?: string;
  moduleEnabled?: boolean;
  estimate?: unknown;
  estimateStatus?: number;
  captured?: { url: string }[];
}

async function mockApi(page: Page, opts: MockOpts = {}): Promise<void> {
  const {
    role = "owner",
    moduleEnabled = true,
    estimate = ESTIMATE,
    estimateStatus,
    captured,
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

    if (path === "/auth/me") return route.fulfill(json({ user: user(role), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(json([{ ...TRANSPORT_MODULE, enabled: moduleEnabled }]));

    if (path === "/transport/estimate") {
      captured?.push({ url: url.pathname });
      if (estimateStatus && estimateStatus >= 400) {
        return route.fulfill(
          json({ detail: "'2026-06' is not a valid claim period", code: "invalid_period" }, estimateStatus),
        );
      }
      return route.fulfill(json(estimate));
    }

    return route.fulfill(json({}));
  });
}

async function runEstimate(page: Page, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/refund-estimate");
  await page.getByRole("textbox", { name: "Claim period" }).fill("2026-Q2");
  await page.setInputFiles('input[type="file"]', {
    name: "statement.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("EUROWAG STATEMENT\n"),
  });
  await page.getByRole("button", { name: "Estimate the refund" }).click();
}

test("estimate: the headline number never appears without its caveat", async ({ page }) => {
  await runEstimate(page);

  await expect(page.getByText("€1,730.00")).toBeVisible();
  // R53's framing, in the same view as the figure it qualifies.
  const note = page.getByRole("note");
  await expect(note).toContainText("never a filed figure");
  await expect(note).toContainText("verify before relying");
});

test("estimate: a country that could not be compared says so, not 'clears'", async ({ page }) => {
  await runEstimate(page);

  await expect(page.getByText("Not compared — mixed currencies")).toBeVisible();
  await expect(page.getByText("Clears €400.00")).toBeVisible();
  await expect(page.getByText("Below €400.00")).toBeVisible();
});

test("estimate: lines it could not convert are surfaced, not hidden", async ({ page }) => {
  await runEstimate(page);

  await expect(
    page.getByText("the real opportunity is larger than this estimate shows"),
  ).toBeVisible();
});

test("estimate: the page states that nothing is stored", async ({ page }) => {
  await mockApi(page);
  await page.goto("/refund-estimate");

  await expect(page.getByText("Nothing is stored", { exact: false })).toBeVisible();
});

test("estimate: a refusal renders its sentence, not the slug", async ({ page }) => {
  await runEstimate(page, { estimateStatus: 422 });

  await expect(page.getByRole("alert")).toContainText("is not a valid claim period");
  await expect(page.getByText("invalid_period")).toHaveCount(0);
});

test("estimate: a read-only role sees the page and cannot run one", async ({ page }) => {
  await mockApi(page, { role: "auditor" });
  await page.goto("/refund-estimate");

  await expect(page.getByRole("heading", { name: "Refund estimate" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Estimate the refund" })).toBeDisabled();
  await expect(page.getByText("can view estimates but not run one")).toBeVisible();
});

test("estimate: with transport off the page shows the notice and no form", async ({ page }) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/refund-estimate");

  await expect(page.getByText("isn't active", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Estimate the refund" })).toHaveCount(0);
});
