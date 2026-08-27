import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-S — the statement front door, over the LIVE shell with the API mocked.
 *
 * This slice's whole claim is that a statement can now enter the product
 * through the product, so these specs exercise the thing an operator actually
 * does: pick an entity, name a period, attach a file, press the button, and
 * read what came back. Three design rules are asserted, and two of them are
 * ABSENCES, which are the only assertions that survive a future edit:
 *
 * 1. the network is NEVER a control — it is shown as information and there is
 *    no input that lets an operator assert one;
 * 2. warnings render as findings to review, with the fact that they blocked
 *    nothing said out loud;
 * 3. a refusal renders the server's sentence, and nothing is reported as
 *    registered.
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
const ENTITY = { id: "entity-1", name: "Demo Haulage OU", legal_name: "Demo Haulage OU" };

const NETWORKS = {
  networks: [
    { network: "Eurowag" },
    { network: "E100" },
    { network: "Q8" },
    { network: "DKV" },
    { network: "TFC" },
    { network: "Moeve" },
    { network: "BP" },
  ],
};

const RESULT = {
  network: "Eurowag",
  period: "2026-06",
  filename: "eurowag-2026-06.csv",
  statement_sha256: "a".repeat(64),
  lines_registered: 3,
  entities_learned: [
    { country: "BE", vat_number: "BE9999999999", entity_name: "Eurowag Belgium NV" },
  ],
  warnings: [
    "capture review: line 2: station name is blank",
    "post-capture check (warning): supplier VAT id not yet confirmed with VIES",
  ],
  sample: [
    {
      line_seq: 1,
      txn_date: "2026-06-03",
      country: "BE",
      station: "Demo Fuel Hub",
      product: "DIESEL",
      qty: "120.500",
      currency: "EUR",
      net_local: "180.75",
      vat_local: "37.96",
      net_eur: "180.75",
      vat_eur: "37.96",
      fx_source: "eur",
    },
  ],
};

async function mockApi(
  page: Page,
  opts: { upload?: { status: number; body: unknown } } = {},
): Promise<void> {
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
    if (path === "/issuer/registry") return route.fulfill(json([ENTITY]));
    if (path === "/transport/statements/networks") return route.fulfill(json(NETWORKS));
    if (path === "/transport/statements") {
      const u = opts.upload ?? { status: 200, body: RESULT };
      return route.fulfill(json(u.body, u.status));
    }
    return route.fulfill(json([]));
  });
}

async function attachStatement(page: Page): Promise<void> {
  await page.setInputFiles('input[type="file"]', {
    name: "eurowag-2026-06.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("EUROWAG STATEMENT\ntxn_date,country,net\n"),
  });
}

test("a statement can be registered from the browser", async ({ page }) => {
  await mockApi(page);
  await page.goto("/statements");

  // Anchor: the page rendered its own heading before anything is asserted.
  await expect(page.getByRole("heading", { name: "Register a statement" })).toBeVisible();

  // Deterministic wait: the entity picker resolves to a real select with the
  // placeholder plus the one seeded entity.
  const entity = page.getByLabel("Claiming entity");
  await expect(entity.locator("option")).toHaveCount(2);
  await entity.selectOption(ENTITY.id);
  await page.getByLabel("Period").fill("2026-06");
  await attachStatement(page);

  await page.getByRole("button", { name: "Register statement" }).click();

  // `exact` matters: the advisory paragraph also contains the word.
  await expect(page.getByText("Registered", { exact: true })).toBeVisible();
  await expect(page.getByText("Eurowag · 2026-06 · 3 line(s)")).toBeVisible();
  // The learned seller entity is named, because a file that quietly edits the
  // registry is a file that edited the registry quietly.
  await expect(page.getByText("BE9999999999")).toBeVisible();
});

test("warnings render as findings, and say they blocked nothing", async ({ page }) => {
  await mockApi(page);
  await page.goto("/statements");

  const entity = page.getByLabel("Claiming entity");
  await expect(entity.locator("option")).toHaveCount(2);
  await entity.selectOption(ENTITY.id);
  await page.getByLabel("Period").fill("2026-06");
  await attachStatement(page);
  await page.getByRole("button", { name: "Register statement" }).click();

  await expect(page.getByText("2 finding(s) to review")).toBeVisible();
  await expect(page.getByText("station name is blank")).toBeVisible();
  // The distinction the whole advisory design rests on, stated in the copy.
  await expect(
    page.getByText("nothing here blocked it or changed a figure", { exact: false }),
  ).toBeVisible();
});

test("the network is information, never a control", async ({ page }) => {
  await mockApi(page);
  await page.goto("/statements");

  // Positive first: the supported networks really are listed.
  await expect(page.getByText("Networks this workspace reads")).toBeVisible();
  await expect(page.getByText("Eurowag", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText("Detected from the file, never chosen here", { exact: false }),
  ).toBeVisible();
  // The absence that matters: no form control offers a network, so an operator
  // cannot assert one and cannot be wrong about it.
  await expect(page.getByLabel("Network")).toHaveCount(0);
  await expect(page.getByLabel("Fuel card network")).toHaveCount(0);
});

test("a refused statement shows the server's reason and reports nothing registered", async ({
  page,
}) => {
  await mockApi(page, {
    upload: {
      status: 422,
      body: {
        detail:
          "Capture review blocked registration: batch tie-out failed: lines total 180.75 vs coversheet 999.00",
        code: "capture_review_blocked",
      },
    },
  });
  await page.goto("/statements");

  const entity = page.getByLabel("Claiming entity");
  await expect(entity.locator("option")).toHaveCount(2);
  await entity.selectOption(ENTITY.id);
  await page.getByLabel("Period").fill("2026-06");
  await attachStatement(page);
  await page.getByRole("button", { name: "Register statement" }).click();

  await expect(page.getByRole("alert")).toContainText("Capture review blocked registration");
  await expect(page.getByRole("alert")).toContainText("tie-out failed");
  // Anchored absence: the alert is on screen, and the success report is not.
  await expect(page.getByText("Registered", { exact: true })).toHaveCount(0);
});
