import { test, expect, type Page, type Route } from "@playwright/test";

test("a failed or expired SSO transaction explains how to restart", async ({ page }) => {
  await page.goto("/login?sso_error=1");
  await expect(page.getByRole("alert")).toContainText("Start a new sign-in attempt or use your password.");
  await expect(page.getByLabel("Single sign-on", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeEnabled();
});

const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });
const identity = (account: string) => ({ user: { id: account, name: `Owner ${account}`, email: `${account}@example.com`, org_id: `org-${account}`, role: "owner" }, organization: { id: `org-${account}`, name: `Workspace ${account}`, status: "active" } });
const dashboard = (account: string) => ({
  as_of: "2026-10-15", approvals: { invoices: [{ invoice_id: account, invoice_number: `PRIVATE-${account}`, vendor_name: `Supplier ${account}`, total: "100", currency: "EUR" }], invoice_count: 1, expense_reports: 0, payment_runs: 0, vendor_changes: 0, total: 1 },
  captures: null, payables: null, receivables: null, cash: null,
});

async function setup(page: Page, options: { delayedDashboard?: boolean; logoutFailure?: boolean; delayedLogout?: boolean; delayedMutation?: boolean; delayedRestore?: boolean } = {}) {
  const requests: string[] = [];
  let delayed: Route | undefined;
  let logout: Route | undefined;
  let mutation: Route | undefined;
  let restoration: Route | undefined;
  const onboarding = (account: string) => ({ steps: [{ key: "source", label: `PRIVATE-SETUP-${account}`, detail: "Synthetic setup", href: "/invoices", done: false }], done_count: 0, complete: false, can_dismiss: true });
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("identity-test-seeded")) {
      localStorage.setItem("invoiceiq_token", "token-A");
      sessionStorage.setItem("identity-test-seeded", "1");
    }
    // A sentinel proves logout/login stayed in the same document.
    (window as unknown as { identityTestDocument: string }).identityTestDocument = crypto.randomUUID();
  });
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const account = route.request().headers().authorization === "Bearer token-B" ? "B" : "A";
    if (path === "/auth/me") {
      if (options.delayedRestore && account === "A") { restoration = route; return; }
      return route.fulfill(json(identity(account)));
    }
    if (path === "/auth/login") return route.fulfill(json({ ...identity("B"), token: { access_token: "token-B" } }));
    if (path === "/auth/logout") {
      if (options.delayedLogout) { logout = route; return; }
      return route.fulfill(json(options.logoutFailure ? { detail: "Offline revocation" } : {}, options.logoutFailure ? 503 : 200));
    }
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: `org-${account}`, name: `Workspace ${account}`, current: true, role: "owner" }]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/dashboard/onboarding") return route.fulfill(json(options.delayedMutation ? onboarding(account) : { steps: [], complete: true }));
    if (path === "/dashboard/onboarding/dismiss") { mutation = route; return; }
    if (path === "/dashboard") {
      requests.push(account);
      if (options.delayedDashboard && account === "A") { delayed = route; return; }
      return route.fulfill(json(dashboard(account)));
    }
    if (path === "/invoices") return route.fulfill(json({ items: [{ id: account, invoice_number: `INVOICE-${account}`, vendor_name: `Vendor ${account}`, issue_date: "2026-10-15", currency: "EUR", subtotal: "100", tax_amount: "0", total: "100", total_eur: "100", workflow_state: "draft", status: "pending", payment_status: "open", outstanding: "100" }], total: 1, page: 1, page_size: 20 }));
    return route.fulfill(json([]));
  });
  await page.goto(options.delayedRestore ? "/login" : "/");
  if (options.delayedRestore) await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  else await expect(page.getByRole("button", { name: "Account menu" })).toBeVisible();
  return { requests, release: async (status = 200) => {
    expect(delayed).toBeDefined();
    const response = page.waitForResponse(r => r.url().endsWith("/dashboard") && r.request().headers().authorization === "Bearer token-A");
    await delayed!.fulfill(json(status === 200 ? dashboard("A") : { detail: "PRIVATE-A expired" }, status));
    await response;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  }, releaseLogout: async () => { expect(logout).toBeDefined(); await logout!.fulfill(json({})); },
    mutationStarted: () => !!mutation,
    releaseMutation: async (status: number) => {
      expect(mutation).toBeDefined();
      await mutation!.fulfill(json(status === 200 ? { ...onboarding("A"), dismissed: true } : { detail: "PRIVATE-A mutation failure" }, status));
    },
    restoreStarted: () => !!restoration,
    releaseRestoration: async (status: number) => {
      expect(restoration).toBeDefined();
      const response = page.waitForResponse(r => r.url().endsWith("/auth/me"));
      await restoration!.fulfill(json(status === 200 ? identity("A") : { detail: "Previous restoration failed" }, status));
      await response;
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    },
  };
}

for (const status of [200, 500]) {
  test(`late initial session restoration (${status}) cannot replace a newer sign-in`, async ({ page }) => {
    const state = await setup(page, { delayedRestore: true });
    await expect.poll(state.restoreStarted).toBe(true);
    await page.getByLabel("Email", { exact: true }).fill("B@example.com");
    await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
    await state.releaseRestoration(status);
    await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("token-B");
  });
}

for (const token of ["token-B", null]) {
  test(`another tab changing the credential to ${token ?? "signed out"} clears the current identity`, async ({ page, context }) => {
    await setup(page);
    await expect(page.getByText("PRIVATE-A", { exact: true })).toBeVisible();
    const sender = await context.newPage();
    await sender.route("**/identity-storage-sender", route => route.fulfill({ contentType: "text/html", body: "<html><body>Synthetic storage sender</body></html>" }));
    await sender.goto("http://localhost:5173/identity-storage-sender");
    await sender.evaluate(value => {
      if (value) localStorage.setItem("invoiceiq_token", value);
      else localStorage.removeItem("invoiceiq_token");
    }, token);
    if (token) await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
    else await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
    await expect(page.getByText("PRIVATE-A", { exact: true })).toHaveCount(0);
    await sender.close();
  });
}

async function signOutAndIn(page: Page) {
  const sentinel = await page.evaluate(() => (window as unknown as { identityTestDocument: string }).identityTestDocument);
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).fill("B@example.com");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Owner B", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { identityTestDocument: string }).identityTestDocument)).toBe(sentinel);
}

for (const logoutFailure of [false, true]) {
  test(`new identity never receives fresh cached dashboard or invoice rows (logout failure=${logoutFailure})`, async ({ page }) => {
    const state = await setup(page, { logoutFailure });
    await expect(page.getByText("PRIVATE-A", { exact: true })).toBeVisible();
    {
      const purchases = page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Purchases & expenses", exact: true });
      if (await purchases.getAttribute("aria-expanded") === "false") await purchases.click();
      await page.getByRole("link", { name: "Supplier invoices", exact: true }).first().click();
    }
    await expect(page.getByText("INVOICE-A", { exact: true })).toBeVisible();
    await signOutAndIn(page);
    await expect(page.getByText("INVOICE-B", { exact: true })).toBeVisible();
    await expect(page.getByText("INVOICE-A", { exact: true })).toHaveCount(0);
    await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
    await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
    await expect(page.getByText("PRIVATE-A", { exact: true })).toHaveCount(0);
    {
      const purchases = page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Purchases & expenses", exact: true });
      if (await purchases.getAttribute("aria-expanded") === "false") await purchases.click();
      await page.getByRole("link", { name: "Supplier invoices", exact: true }).first().click();
    }
    await expect(page.getByText("INVOICE-B", { exact: true })).toBeVisible();
    await expect(page.getByText("INVOICE-A", { exact: true })).toHaveCount(0);
    expect(state.requests).toContain("B");
    // Beyond the configured 30-second freshness window, revisiting the worklist
    // must still never render A's stale rows while B refetches.
    await page.clock.install();
    await page.clock.fastForward(31_000);
    await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
    {
      const purchases = page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Purchases & expenses", exact: true });
      if (await purchases.getAttribute("aria-expanded") === "false") await purchases.click();
      await page.getByRole("link", { name: "Supplier invoices", exact: true }).first().click();
    }
    await expect(page.getByText("INVOICE-B", { exact: true })).toBeVisible();
    await expect(page.getByText("INVOICE-A", { exact: true })).toHaveCount(0);
  });
}

test("local logout completes before a slow server revocation and its reply cannot clear B", async ({ page }) => {
  const state = await setup(page, { delayedLogout: true });
  await expect(page.getByText("PRIVATE-A", { exact: true })).toBeVisible();
  await signOutAndIn(page);
  await state.releaseLogout();
  await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("token-B");
});

for (const status of [200, 500]) {
  test(`late previous-account mutation (${status}) cannot update the new cache or disclose an error`, async ({ page }) => {
    const state = await setup(page, { delayedMutation: true });
    await expect(page.getByText("PRIVATE-SETUP-A", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Dismiss", exact: true }).click();
    await expect.poll(state.mutationStarted).toBe(true);
    await signOutAndIn(page);
    await expect(page.getByText("PRIVATE-SETUP-B", { exact: true })).toBeVisible();
    const response = page.waitForResponse(r => r.url().endsWith("/dashboard/onboarding/dismiss"));
    await state.releaseMutation(status);
    await response;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("PRIVATE-SETUP-B", { exact: true })).toBeVisible();
    await expect(page.getByText("PRIVATE-A mutation failure", { exact: true })).toHaveCount(0);
    await expect(page.getByText("PRIVATE-SETUP-A", { exact: true })).toHaveCount(0);
  });
}

for (const status of [200, 401]) {
  test(`a delayed previous-account response (${status}) cannot replace or log out the new account`, async ({ page }) => {
    const state = await setup(page, { delayedDashboard: true });
    await expect.poll(() => state.requests.includes("A")).toBe(true);
    await signOutAndIn(page);
    await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
    await state.release(status);
    await expect(page.getByText("PRIVATE-B", { exact: true })).toBeVisible();
    await expect(page.getByText("PRIVATE-A", { exact: true })).toHaveCount(0);
    await expect(page.getByText("PRIVATE-A expired", { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("token-B");
  });
}
