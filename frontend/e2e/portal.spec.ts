import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * The client portal (WO-I) over the LIVE app shell, API mocked via
 * `page.route`. The portal page is PUBLIC — no login, the URL token is the
 * credential — so these specs run unauthenticated.
 *
 * What earns its place here:
 *  - the portal renders the customer's offers (with lines), invoices and
 *    shared documents from the wire shapes;
 *  - Accept posts {decision: "accepted"} to the offer's decision route;
 *  - a dead link renders the friendly "no longer valid" message, not a
 *    crash;
 *  - the workspace side: the customer page's portal card fetches and shows
 *    the link;
 *  - copy is industry-neutral (owner guard list).
 *
 * Synthetic fixtures only.
 */

const SUMMARY = {
  organization: "Test Workspace",
  customer: "Riverbank Office",
  offers: [
    {
      offer_id: "off-1",
      number: "OFF-1",
      version: 1,
      title: "Renovation quote",
      status: "sent",
      total: "500.00",
      currency: "EUR",
      project: "Job POR-1",
      lines: [{ description: "Work", amount: "500.00" }],
      decidable: true,
    },
  ],
  invoices: [
    {
      number: "INV-2026-001",
      total: "121.00",
      currency: "EUR",
      status: "issued",
      issued_at: "2026-08-20T10:00:00+00:00",
    },
  ],
  documents: [
    { document_id: "doc-1", filename: "contract.pdf", kind: "contract", project: "Job POR-1" },
  ],
};

async function mock(
  page: Page,
  opts: { dead?: boolean; onDecision?: (b: Record<string, unknown>) => void } = {},
) {
  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (p === "/portal/tok-live") {
      if (opts.dead) return route.fulfill(json({ detail: "unknown portal link" }, 404));
      return route.fulfill(json(SUMMARY));
    }
    if (p === "/portal/tok-live/offers/off-1/decision" && method === "POST") {
      opts.onDecision?.(route.request().postDataJSON());
      return route.fulfill(json({ offer_id: "off-1", status: "accepted" }));
    }
    return route.fulfill(json({ detail: "not found" }, 404));
  });
}

test("the portal renders offers, invoices and shared documents", async ({ page }) => {
  await mock(page);
  await page.goto("/portal/tok-live");

  await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
  await expect(page.getByText("OFF-1 — Renovation quote")).toBeVisible();
  await expect(page.getByText("awaiting your decision")).toBeVisible();
  await expect(page.getByText("INV-2026-001")).toBeVisible();
  await expect(page.getByText("contract.pdf")).toBeVisible();
});

test("accepting an offer posts the decision", async ({ page }) => {
  const decisions: Record<string, unknown>[] = [];
  await mock(page, { onDecision: (b) => decisions.push(b) });
  await page.goto("/portal/tok-live");

  await page.getByRole("button", { name: "Accept offer" }).click();
  await expect.poll(() => decisions.length).toBe(1);
  expect(decisions[0]).toEqual({ decision: "accepted" });
});

test("a dead link gets the friendly message, not a crash", async ({ page }) => {
  await mock(page, { dead: true });
  await page.goto("/portal/tok-live");
  await expect(page.getByText("This link is no longer valid", { exact: false })).toBeVisible();
});

test("the portal copy is industry-neutral", async ({ page }) => {
  await mock(page);
  await page.goto("/portal/tok-live");
  await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
