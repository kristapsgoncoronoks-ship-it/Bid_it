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

// Public read recovery; all tokens and responses are synthetic.
const READ_PATH = "/api/v1/portal/tok-live";
const wire = (data: unknown, status = 200, headers: Record<string, string> = {}) => ({
  status, headers, contentType: "application/json", body: JSON.stringify(data),
});

for (const mode of ["503", "500", "network"] as const) {
  test(`a ${mode} read failure explains recovery and retries only one GET`, async ({ page }) => {
    let reads = 0;
    let pending: Route | undefined;
    const methods: string[] = [];
    await page.route("**/api/v1/**", async route => {
      methods.push(route.request().method());
      expect(new URL(route.request().url()).pathname).toBe(READ_PATH);
      reads++;
      if (reads === 1) {
        if (mode === "network") return route.abort("failed");
        return route.fulfill(wire({ detail: "Upstream failure for /portal/tok-live" }, Number(mode)));
      }
      pending = route;
    });
    await page.goto("/portal/tok-live?source=read-recovery");
    await expect(page.getByRole("alert").filter({ hasText: "Could not load the portal" })).toBeVisible();
    await expect(page.getByText("This link is no longer valid", { exact: false })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("tok-live");
    expect(reads).toBe(1);
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    await expect.poll(() => pending !== undefined).toBe(true);
    const retry = page.getByRole("button", { name: "Retrying…", exact: true });
    await expect(retry).toBeDisabled();
    await retry.evaluate(el => { el.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(reads).toBe(2);
    await pending!.fulfill(wire(SUMMARY));
    await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
    await expect(page.getByText("INV-2026-001")).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again", exact: true })).toHaveCount(0);
    await expect(page).toHaveURL(/\/portal\/tok-live\?source=read-recovery$/);
    expect(methods).toEqual(["GET", "GET"]);
    expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBeNull();
  });
}

for (const status of [404, 410, 401, 403]) {
  test(`a ${status} link refusal gives contact guidance without a futile retry`, async ({ page }) => {
    let requests = 0;
    await page.route("**/api/v1/**", route => {
      requests++;
      return route.fulfill(wire({ detail: "Refused /portal/tok-live" }, status));
    });
    await page.goto("/portal/tok-live");
    if (status === 404 || status === 410) {
      await expect(page.getByText("This link is no longer valid", { exact: false })).toBeVisible();
    } else {
      await expect(page.getByRole("alert")).toContainText("Access to this portal was refused");
      await expect(page.getByText("This link is no longer valid", { exact: false })).toHaveCount(0);
    }
    await expect(page.getByRole("button", { name: "Try again", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Accept offer" })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("tok-live");
    await expect(page).toHaveURL(/\/portal\/tok-live$/);
    expect(requests).toBe(1);
  });
}

for (const format of ["seconds", "date"] as const) {
  test(`429 honors Retry-After ${format} before allowing a read retry`, async ({ page }) => {
    await page.clock.install();
    let reads = 0;
    await page.route("**/api/v1/**", route => {
      expect(route.request().method()).toBe("GET");
      reads++;
      if (reads === 1) return route.fulfill(wire({ detail: "Rate limited tok-live" }, 429, {
        "Retry-After": format === "seconds" ? "2" : new Date(Date.now() + 2_000).toUTCString(),
      }));
      return route.fulfill(wire(SUMMARY));
    });
    await page.goto("/portal/tok-live");
    await expect(page.getByRole("alert")).toContainText("Too many portal requests");
    await expect(page.getByRole("button", { name: "Please wait", exact: true })).toBeDisabled();
    expect(reads).toBe(1);
    await page.clock.fastForward(2_100);
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
    expect(reads).toBe(2);
  });
}

for (const header of [undefined, "not-a-date", "9999999999999999999999999999999"]) {
  test(`429 with ${header === undefined ? "no" : header} Retry-After remains recoverable`, async ({ page }) => {
    let reads = 0;
    await page.route("**/api/v1/**", route => {
      reads++;
      return route.fulfill(reads === 1
        ? wire({ detail: "Rate limited" }, 429, header === undefined ? {} : { "Retry-After": header })
        : wire(SUMMARY));
    });
    await page.goto("/portal/tok-live");
    await expect(page.getByRole("alert")).toContainText("Too many portal requests");
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
    expect(reads).toBe(2);
  });
}

test("a stalled portal read ends with a timeout and can recover", async ({ page }) => {
  let reads = 0;
  let stalled: Route | undefined;
  await page.route("**/api/v1/**", route => {
    reads++;
    if (reads === 1) { stalled = route; return; }
    return route.fulfill(wire(SUMMARY));
  });
  await page.goto("/portal/tok-live");
  await expect(page.getByRole("status")).toContainText("Loading portal");
  await expect(page.getByRole("alert").filter({ hasText: "The portal took too long to respond" })).toBeVisible({ timeout: 18_000 });
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
  expect(reads).toBe(2);
  await stalled?.abort().catch(() => {});
});

test("recovering an accepted offer's failed refresh never resubmits the decision", async ({ page }) => {
  let reads = 0;
  const decisions: unknown[] = [];
  await page.route("**/api/v1/**", route => {
    if (route.request().method() === "POST") {
      decisions.push(route.request().postDataJSON());
      return route.fulfill(wire({ offer_id: "off-1", status: "accepted" }));
    }
    reads++;
    if (reads === 1) return route.fulfill(wire(SUMMARY));
    if (reads === 2) return route.fulfill(wire({ detail: "Temporary read outage" }, 503));
    return route.fulfill(wire({ ...SUMMARY, offers: [{ ...SUMMARY.offers[0], status: "accepted", decidable: false }] }));
  });
  await page.goto("/portal/tok-live");
  await page.getByRole("button", { name: "Accept offer" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Could not load the portal" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept offer" })).toHaveCount(0);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByText("Job POR-1 · accepted", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept offer" })).toHaveCount(0);
  expect(reads).toBe(3);
  expect(decisions).toEqual([{ decision: "accepted" }]);
});

test("a backwards clock adjustment still displays the current rate-limit cooldown", async ({ page }) => {
  await page.clock.install();
  let reads = 0;
  await page.route("**/api/v1/**", route => {
    reads++;
    if (reads === 1) return route.fulfill(wire({ detail: "Temporary outage" }, 503));
    if (reads === 2) return route.fulfill(wire({ detail: "Rate limited" }, 429, { "Retry-After": "2" }));
    return route.fulfill(wire(SUMMARY));
  });
  await page.goto("/portal/tok-live");
  await expect(page.getByRole("alert")).toContainText("Could not load the portal");
  await page.clock.setSystemTime(new Date(Date.now() - 60_000));
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("button", { name: "Please wait", exact: true })).toBeDisabled();
  expect(reads).toBe(2);
  await page.clock.fastForward(2_100);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
  expect(reads).toBe(3);
});

test("manual recovery remains finite while the browser is already offline", async ({ page, context }) => {
  let reads = 0;
  let online = false;
  await page.route("**/api/v1/**", route => {
    reads++;
    if (reads === 1) return route.fulfill(wire({ detail: "Temporary outage" }, 503));
    if (!online) return route.abort("internetdisconnected");
    return route.fulfill(wire(SUMMARY));
  });
  await page.goto("/portal/tok-live");
  await expect(page.getByRole("alert")).toContainText("Could not load the portal");
  await context.setOffline(true);
  expect(await page.evaluate(() => navigator.onLine)).toBe(false);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("button", { name: "Try again", exact: true })).toBeEnabled();
  await expect(page.getByRole("alert")).toContainText("Could not load the portal");
  expect(reads).toBe(2);
  online = true;
  await context.setOffline(false);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Hello, Riverbank Office" })).toBeVisible();
  expect(reads).toBe(3);
});
