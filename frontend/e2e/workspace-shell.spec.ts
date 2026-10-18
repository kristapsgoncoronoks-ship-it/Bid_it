import { test, expect, type Page, type Route } from "@playwright/test";

// Exercise the real module request; API fixtures only establish a synthetic
// authenticated session. This is loading/recovery evidence, not server proof.
const SHELL = /\/src\/components\/Layout\.tsx(?:\?|$)/;
const json = (body: unknown) => ({ contentType: "application/json", body: JSON.stringify(body) });
async function signedIn(page: Page) {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("shell-fixture-seeded")) {
      localStorage.setItem("invoiceiq_token", "shell-fixture-token");
      sessionStorage.setItem("shell-fixture-seeded", "1");
    }
    (window as unknown as { shellDocument: string }).shellDocument = crypto.randomUUID();
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({
      user: { id: "shell-owner", name: "Owner", email: "owner@example.test", org_id: "shell-org", role: "owner" },
      organization: { id: "shell-org", name: "Shell fixture", status: "active" },
    }));
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: "shell-org", name: "Shell fixture", current: true, role: "owner" }]));
    if (path === "/dashboard/onboarding") return route.fulfill(json({ steps: [], complete: true, dismissed: true }));
    return route.fulfill(json([]));
  });
}

test("sign-in works without requesting the protected workspace module", async ({ page }) => {
  let shellRequests = 0;
  await page.route(SHELL, async route => { shellRequests++; await route.abort("failed"); });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByLabel("Email", { exact: true })).toBeEditable();
  expect(shellRequests).toBe(0);
});

test("a delayed workspace module explains loading and then opens the requested page", async ({ page }) => {
  await signedIn(page);
  let delayed: Route | undefined;
  await page.route(SHELL, route => { delayed = route; });
  await page.goto("/sessions?source=shell-fixture", { waitUntil: "domcontentloaded" });
  try {
    await expect(page.getByRole("status")).toHaveText("Loading workspace…");
    await expect.poll(() => delayed !== undefined).toBe(true);
    await delayed!.continue();
    delayed = undefined;
    await expect(page.getByRole("heading", { name: "Active sessions" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Account menu" })).toBeVisible();
    await expect(page).toHaveURL(/\/sessions\?source=shell-fixture$/);
  } finally { if (delayed) await delayed.abort(); }
});

test("a failed workspace module offers a reload that actually recovers", async ({ page }) => {
  await signedIn(page);
  let refuse = true;
  let shellRequests = 0;
  await page.route(SHELL, async route => {
    shellRequests++;
    if (refuse) return route.abort("failed");
    return route.continue();
  });
  await page.goto("/sessions?source=shell-fixture");
  await expect(page.getByRole("alert")).toContainText("The workspace could not load");
  const before = await page.evaluate(() => (window as unknown as { shellDocument: string }).shellDocument);
  refuse = false;
  await page.getByRole("button", { name: "Reload workspace", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Active sessions" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Account menu" })).toBeVisible();
  await expect(page).toHaveURL(/\/sessions\?source=shell-fixture$/);
  expect(shellRequests).toBe(2);
  expect(await page.evaluate(() => (window as unknown as { shellDocument: string }).shellDocument)).not.toBe(before);
  expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("shell-fixture-token");
});

test("a failed page download reloads instead of repeating a cached rejection", async ({ page }) => {
  await signedIn(page);
  let refuse = true;
  let requests = 0;
  await page.route(/\/src\/pages\/Sessions\.tsx(?:\?|$)/, async route => {
    requests++;
    if (refuse) return route.abort("failed");
    return route.continue();
  });
  await page.goto("/sessions?source=page-fixture");
  await expect(page.getByRole("alert")).toContainText("This page could not load");
  await expect(page.getByRole("button", { name: "Account menu" })).toBeVisible();
  const before = await page.evaluate(() => (window as unknown as { shellDocument: string }).shellDocument);
  refuse = false;
  await page.getByRole("button", { name: "Reload page", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Active sessions" })).toBeVisible();
  await expect(page).toHaveURL(/\/sessions\?source=page-fixture$/);
  expect(requests).toBe(2);
  expect(await page.evaluate(() => (window as unknown as { shellDocument: string }).shellDocument)).not.toBe(before);
  expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("shell-fixture-token");
});

test("a failed public portal download can reload without an account session", async ({ page }) => {
  let refuse = true;
  let moduleRequests = 0;
  let summaryReads = 0;
  await page.route(/\/src\/pages\/Portal\.tsx(?:\?|$)/, async route => {
    moduleRequests++;
    if (refuse) return route.abort("failed");
    return route.continue();
  });
  await page.route("**/api/v1/**", async route => {
    expect(route.request().method()).toBe("GET");
    expect(new URL(route.request().url()).pathname).toBe("/api/v1/portal/tok-shell-fixture");
    expect(route.request().headers().authorization).toBeUndefined();
    summaryReads++;
    return route.fulfill(json({ organization: "Portal workspace", customer: "Portal fixture", offers: [], invoices: [], documents: [] }));
  });
  await page.goto("/portal/tok-shell-fixture?source=public-page");
  await expect.poll(() => moduleRequests).toBe(1);
  await expect(page.getByRole("alert")).toContainText("This page could not load");
  expect(moduleRequests).toBe(1);
  expect(summaryReads).toBe(0);
  refuse = false;
  await page.getByRole("button", { name: "Reload page", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Hello, Portal fixture", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/portal\/tok-shell-fixture\?source=public-page$/);
  expect(moduleRequests).toBe(2);
  expect(summaryReads).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBeNull();
});
