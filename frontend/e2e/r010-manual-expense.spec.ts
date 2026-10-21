import { test, expect, type Page, type Route } from "@playwright/test";

// Synthetic responses qualify client state only. Actual API/PostgreSQL
// completion has independent coverage in live/expenses.spec.ts.
const ORG = { id: "manual-org", name: "Synthetic manual workspace", status: "active" };
const USER = { id: "manual-user", org_id: ORG.id, name: "Synthetic owner", role: "owner", email: "manual@example.com" };
const SLOT = `invoiceiq:command:${USER.id}:${ORG.id}:/expenses/manual`;
const SELECTED_SLOT = `invoiceiq:command:${USER.id}:${ORG.id}:/expenses/from-selected`;
const REPORT = {
  id: "manual-report-1", employee_id: USER.id, employee_name: USER.name, title: "Synthetic manual trip",
  status: "draft", currency: "EUR", total: "0.30", vat_total: "0.00", total_eur: "0.30",
  submitted_at: null, created_at: "2026-10-20T00:00:00Z", version: 1, items: [],
};
type Receipt = Record<string, unknown>;
type Wire = {
  posts: { key: string | undefined; scope: { user: string | undefined; org: string | undefined }; payload: Record<string, unknown> }[];
  lookups: string[]; detailGets: number; listGets: number;
  lost: boolean; postStatus: number; lookupStatus: number; lookupOffline: boolean;
  receipt: ((key: string) => Receipt) | undefined; readFailure: boolean; committed: boolean; report: typeof REPORT; holdPost: boolean; heldPost?: Route; commitOnLost: boolean; detailReport?: typeof REPORT; holdLookup: boolean; heldLookup?: Route;
};
const wires = new WeakMap<Page, Wire>();
test.afterEach(async ({ page }, info) => {
  const wire = wires.get(page);
  if (wire) await info.attach("synthetic-manual-wire", { body: JSON.stringify(wire, null, 2), contentType: "application/json" });
});
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const receipt = (key: string, report = REPORT): Receipt => ({ id: "manual-command-1", command_key: key, status: "committed", operation: "report_created", target_id: report.id, result: report });
async function setup(page: Page, options: { saved?: string; selected?: string; permissions?: string[]; moduleEnabled?: boolean } = {}) {
  const state: Wire = { posts: [], lookups: [], detailGets: 0, listGets: 0, lost: false, postStatus: 201, lookupStatus: 200, lookupOffline: false, receipt: undefined, readFailure: false, committed: false, report: { ...REPORT }, holdPost: false, commitOnLost: true, holdLookup: false };
  wires.set(page, state);
  await page.addInitScript(({ slot, selectedSlot, saved, selected }) => {
    if (!sessionStorage.getItem("manual-test-seeded")) {
      localStorage.setItem("invoiceiq_token", "synthetic-manual-token");
      sessionStorage.setItem("manual-test-seeded", "1");
    }
    if (saved !== undefined) sessionStorage.setItem(slot, saved);
    if (selected !== undefined) sessionStorage.setItem(selectedSlot, selected);
  }, { slot: SLOT, selectedSlot: SELECTED_SLOT, saved: options.saved, selected: options.selected });
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace("/api/v1", "");
    const accountB = req.headers().authorization === "Bearer synthetic-manual-token-B";
    const currentUser = accountB ? { ...USER, id: "manual-user-B", org_id: "manual-org-B", name: "Synthetic second owner" } : USER;
    const currentOrg = accountB ? { ...ORG, id: "manual-org-B", name: "Synthetic second workspace" } : ORG;
    if (path === "/auth/login") return route.fulfill(json({ user: { ...USER, id: "manual-user-B", org_id: "manual-org-B", name: "Synthetic second owner" }, organization: { ...ORG, id: "manual-org-B", name: "Synthetic second workspace" }, token: { access_token: "synthetic-manual-token-B" } }));
    if (path === "/auth/logout") return route.fulfill(json({}));
    if (path === "/auth/me") return route.fulfill(json({ user: currentUser, organization: currentOrg, ...(options.permissions ? { permissions: options.permissions } : {}) }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (["/webhooks", "/jobs", "/sso/assignable-roles", "/workspace/export-requests"].includes(path)) return route.fulfill(json([]));
    if (path === "/retention") return route.fulfill(json({ categories: [], holds: [] }));
    if (path === "/modules") return route.fulfill(json([{ key: "expenses", name: "Employee expenses", enabled: options.moduleEnabled !== false, ready: true, requires_issuer: false }]));
    if (path === "/expenses" && req.method() === "POST") {
      const headers = req.headers();
      state.posts.push({ key: headers["idempotency-key"], scope: { user: headers["x-expected-user-id"], org: headers["x-expected-org-id"] }, payload: req.postDataJSON() });
      if (state.holdPost) { state.heldPost = route; return; }
      if (state.postStatus !== 201) return route.fulfill(json({ detail: "Synthetic save refused" }, state.postStatus));
      state.committed = !state.lost || state.commitOnLost;
      if (state.lost) return route.abort("failed");
      return route.fulfill(json(state.report, 201));
    }
    if (path.startsWith("/expenses/commands/")) {
      // Model the authoritative identity guard: B cannot read A's saved key,
      // even when a retired client attempts recovery after a late response.
      if (accountB) return route.fulfill(json({ detail: "Expected identity changed" }, 403));
      expect(req.method()).toBe("GET");
      expect(req.headers()["x-expected-user-id"]).toBe(USER.id);
      expect(req.headers()["x-expected-org-id"]).toBe(ORG.id);
      const key = path.slice("/expenses/commands/".length);
      expect(key).not.toContain("/");
      state.lookups.push(key);
      if (state.holdLookup) { state.heldLookup = route; return; }
      if (state.lookupOffline) return route.abort("internetdisconnected");
      if (state.lookupStatus !== 200) return route.fulfill(json({ detail: "Synthetic lookup unavailable" }, state.lookupStatus));
      return route.fulfill(json(state.receipt ? state.receipt(key) : receipt(key, state.report)));
    }
    if (path === `/expenses/${state.report.id}`) {
      state.detailGets += 1;
      return route.fulfill(json(state.readFailure ? { detail: "Synthetic current report unavailable" } : (state.detailReport ?? state.report), state.readFailure ? 503 : 200));
    }
    if (path === "/expenses/summary") return route.fulfill(json(state.readFailure && state.committed ? { detail: "Synthetic summary unavailable" } : { my_reimbursable: "0", reclaimable_vat: "0", my_submitted: 0, pending_approvals: 0 }, state.readFailure && state.committed ? 503 : 200));
    if (path === "/expenses/transactions") return route.fulfill(json([]));
    if (path === "/expenses" && req.method() === "GET") {
      state.listGets += 1;
      if (state.readFailure && state.committed) return route.fulfill(json({ detail: "Synthetic list unavailable" }, 503));
      const items = state.committed && !accountB && url.searchParams.get("mine") === "true" ? [state.report] : [];
      return route.fulfill(json({ items, total: items.length, page: 1, page_size: 50 }));
    }
    return route.fulfill(json({ items: [], total: 0 }));
  });
  await page.goto("/expenses");
  return state;
}
async function editor(page: Page, amount = "0.10", vat = "0") {
  await page.getByRole("button", { name: "+ New expense report (manual)", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill(REPORT.title);
  await page.getByLabel("Item 1 description", { exact: true }).fill("Synthetic train fare");
  await page.getByLabel("Item 1 amount", { exact: true }).fill(amount);
  await page.getByLabel("Item 1 VAT", { exact: true }).fill(vat);
}
async function save(page: Page) { await page.getByRole("button", { name: "Save draft", exact: true }).click(); }
const outcome = (page: Page) => page.getByRole("button", { name: "Check previous manual draft outcome", exact: true });
const savedLink = (page: Page) => page.getByRole("link", { name: "Open saved draft", exact: true });

async function openNavigationSection(page: Page, title: string) {
  const section = page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: title, exact: true });
  await expect(section).toBeVisible();
  if (await section.getAttribute("aria-expanded") === "false") await section.click();
  await expect(section).toHaveAttribute("aria-expanded", "true");
}
const metadata = (page: Page) => page.evaluate((slot) => sessionStorage.getItem(slot), SLOT);

test("manual exact cent preview sends one scoped explicit-key intention and awaits saved readback", async ({ page }) => {
  const renderingErrors: string[] = [];
  page.on("pageerror", (error) => renderingErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error" && message.text().includes("same key")) renderingErrors.push(message.text()); });
  const wire = await setup(page);
  await editor(page);
  await page.getByRole("button", { name: "+ Add expense", exact: true }).click();
  await page.getByLabel("Item 2 description", { exact: true }).fill("Synthetic bus fare");
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.20");
  await expect(page.getByText("€0.30", { exact: true })).toBeVisible();
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(1);
  expect(wire.posts[0].key).toMatch(/^[A-Za-z0-9:_-]{1,120}$/);
  expect(wire.posts[0].scope).toEqual({ user: USER.id, org: ORG.id });
  expect((wire.posts[0].payload.items as { amount: string }[]).map((item) => item.amount)).toEqual(["0.10", "0.20"]);
  await expect.poll(() => wire.detailGets).toBeGreaterThan(0);
  await expect.poll(() => metadata(page)).toBeNull();
  expect(renderingErrors).toEqual([]);
});

test("manual two overprecise amounts request correction without posting or claiming a rounded total", async ({ page }) => {
  const wire = await setup(page);
  await editor(page, "0.005");
  await page.getByRole("button", { name: "+ Add expense", exact: true }).click();
  await page.getByLabel("Item 2 description", { exact: true }).fill("Synthetic bus fare");
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.005");
  const button = page.getByRole("button", { name: "Save draft", exact: true });
  if (await button.isEnabled()) await button.click();
  await expect(page.getByRole("alert").filter({ hasText: /amount.*(two|2).*decimal|amount.*precision/i })).toBeVisible();
  expect(wire.posts).toHaveLength(0);
});

for (const field of ["amount", "VAT"] as const) {
  test(`manual invalid ${field} values cannot post a draft`, async ({ page }) => {
    const wire = await setup(page);
    await editor(page);
    for (const value of ["-0.01", "NaN", "Infinity", "1e2", "0.001", "1000000000000.00", ""]) {
      await page.getByLabel(`Item 1 ${field}`, { exact: true }).fill(value);
      const button = page.getByRole("button", { name: "Save draft", exact: true });
      if (await button.isEnabled()) await button.click();
      await expect(page.getByRole("alert").filter({ hasText: new RegExp(field, "i") })).toBeVisible();
      expect(wire.posts).toHaveLength(0);
    }
  });
}

test("manual VAT cannot exceed its item amount", async ({ page }) => {
  const wire = await setup(page);
  await editor(page, "0.10", "0.11");
  const button = page.getByRole("button", { name: "Save draft", exact: true });
  if (await button.isEnabled()) await button.click();
  await expect(page.getByRole("alert").filter({ hasText: /VAT.*(exceed|greater|amount)/i })).toBeVisible();
  expect(wire.posts).toHaveLength(0);
});

test("manual zero amounts remain a supported draft", async ({ page }) => {
  const wire = await setup(page);
  wire.report = { ...REPORT, total: "0.00", total_eur: "0.00" };
  await editor(page, "0", "0");
  await expect(page.getByRole("heading", { name: "New expense report", exact: true }).locator("..").getByText("€0.00", { exact: true })).toBeVisible();
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(1);
  expect((wire.posts[0].payload.items as { amount: string; vat_amount: string }[])[0]).toMatchObject({ amount: "0.00", vat_amount: "0.00" });
});

test("manual supported boundary total uses exact cents and rejects one extra cent", async ({ page }) => {
  const wire = await setup(page);
  await editor(page, "999999999999.98");
  await page.getByRole("button", { name: "+ Add expense", exact: true }).click();
  await page.getByLabel("Item 2 description", { exact: true }).fill("Synthetic last cent");
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.01");
  await expect(page.getByText("€999,999,999,999.99", { exact: true })).toBeVisible();
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.02");
  const button = page.getByRole("button", { name: "Save draft", exact: true });
  if (await button.isEnabled()) await button.click();
  await expect(page.getByRole("alert").filter({ hasText: /total|maximum|limit|999/i })).toBeVisible();
  expect(wire.posts).toHaveLength(0);
});

test("manual digest wait coalesces rapid activations and freezes the submitted envelope", async ({ page }) => {
  await page.addInitScript(() => {
    const original = crypto.subtle.digest.bind(crypto.subtle);
    const state = window as unknown as { manualDigestHeld: boolean; releaseManualDigest?: () => void };
    crypto.subtle.digest = async (...args: Parameters<SubtleCrypto["digest"]>) => {
      state.manualDigestHeld = true;
      await new Promise<void>((resolve) => { state.releaseManualDigest = resolve; });
      return original(...args);
    };
  });
  const wire = await setup(page);
  await editor(page);
  await page.evaluate(() => {
    const button = Array.from(document.querySelectorAll("button")).find((element) => element.textContent === "Save draft")!;
    button.click(); button.click();
  });
  await expect.poll(() => page.evaluate(() => (window as unknown as { manualDigestHeld?: boolean }).manualDigestHeld)).toBe(true);
  await expect(page.getByLabel("Title", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Item 1 amount", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "+ Add expense", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
  expect(wire.posts).toHaveLength(0);
  await page.evaluate(() => (window as unknown as { releaseManualDigest: () => void }).releaseManualDigest());
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(1);
  expect(wire.posts[0].scope).toEqual({ user: USER.id, org: ORG.id });
  expect((wire.posts[0].payload.items as { description: string; amount: string }[])[0]).toMatchObject({ description: "Synthetic train fare", amount: "0.10" });
});

test("manual lost save response recovers one reference and later checks only GET", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.lookupStatus = 503;
  await editor(page);
  await save(page);
  await expect(outcome(page)).toBeVisible();
  await expect.poll(() => metadata(page)).not.toBeNull();
  const stored = JSON.parse((await metadata(page))!);
  expect(Object.keys(stored).sort()).toEqual(["digest", "key"]);
  expect(stored.key).toBe(wire.posts[0].key);
  expect(stored.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(wire.posts).toHaveLength(1);
  wire.lookupStatus = 200;
  await outcome(page).click();
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  const lookups = wire.lookups.length;
  await outcome(page).click();
  await expect.poll(() => wire.lookups.length).toBe(lookups + 1);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(1);
  expect(new Set(wire.lookups)).toEqual(new Set([stored.key]));
  await expect.poll(() => metadata(page)).toBeNull();
});

for (const status of [403, 404, 429, 503]) {
  test(`manual saved reference survives lookup ${status} without a replacement POST`, async ({ page }) => {
    const key = `manual-saved-${status}`;
    const stored = JSON.stringify({ key, digest: "b".repeat(64) });
    const wire = await setup(page, { saved: stored });
    wire.lookupStatus = status;
    await outcome(page).click();
    await expect(page.getByRole("alert").filter({ hasText: /visible|unresolved|check|permission|available|found|unknown|recorded|access/i })).toBeVisible();
    expect(wire.posts).toHaveLength(0);
    expect(wire.lookups).toEqual([key]);
    expect(await metadata(page)).toBe(stored);
    wire.lookupStatus = 200;
    await outcome(page).click();
    await expect(savedLink(page)).toBeVisible();
    expect(wire.posts).toHaveLength(0);
  });
}

test("manual offline lookup retains the original reference and recovers when connected", async ({ page }) => {
  const stored = JSON.stringify({ key: "manual-offline", digest: "b".repeat(64) });
  const wire = await setup(page, { saved: stored });
  wire.lookupOffline = true;
  await outcome(page).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(await metadata(page)).toBe(stored);
  expect(wire.posts).toHaveLength(0);
  wire.lookupOffline = false;
  await outcome(page).click();
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(0);
});

for (const shape of ["wrong-operation", "wrong-key", "wrong-target", "uncommitted", "no-result", "invalid-total"] as const) {
  test(`manual ${shape} receipt cannot clear an unresolved reference`, async ({ page }) => {
    const stored = JSON.stringify({ key: "manual-malformed", digest: "b".repeat(64) });
    const wire = await setup(page, { saved: stored });
    wire.receipt = (key) => {
      const data = receipt(key);
      if (shape === "wrong-operation") data.operation = "statement_imported";
      if (shape === "wrong-key") data.command_key = "another-command";
      if (shape === "wrong-target") data.target_id = "another-report";
      if (shape === "uncommitted") data.status = "pending";
      if (shape === "no-result") data.result = null;
      if (shape === "invalid-total") data.result = { ...REPORT, total: "NaN" };
      return data;
    };
    await outcome(page).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(savedLink(page)).toHaveCount(0);
    expect(await metadata(page)).toBe(stored);
    expect(wire.posts).toHaveLength(0);
    expect(wire.detailGets).toBe(0);
  });
}

test("manual refresh loses form inputs but retains a GET-only uncertain save", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.lookupStatus = 404;
  await editor(page);
  await save(page);
  await expect(outcome(page)).toBeVisible();
  const stored = await metadata(page);
  await page.reload();
  await outcome(page).click();
  await expect(page.getByRole("alert").filter({ hasText: /visible|unresolved|found|recorded|unknown/i })).toBeVisible();
  expect(await metadata(page)).toBe(stored);
  expect(wire.posts).toHaveLength(1);
  wire.lookupStatus = 200;
  await outcome(page).click();
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(1);
});

test("manual corrupt metadata is explained without lookup or write", async ({ page }) => {
  const wire = await setup(page, { saved: "{corrupt" });
  await expect(page.getByRole("alert").filter({ hasText: /unreadable|corrupt|administrator/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeDisabled();
  expect(await metadata(page)).toBe("{corrupt");
  expect(wire.posts).toHaveLength(0);
  expect(wire.lookups).toHaveLength(0);
});

test("manual and selected-report saved references remain separate", async ({ page }) => {
  const manual = JSON.stringify({ key: "manual-independent", digest: "b".repeat(64) });
  const selected = JSON.stringify({ key: "selected-independent", digest: "c".repeat(64), path: "/expenses", payload: { title: "Selected synthetic report", transaction_ids: ["selected-1"] } });
  const wire = await setup(page, { saved: manual, selected });
  await outcome(page).click();
  await expect(savedLink(page)).toBeVisible();
  expect(wire.lookups).toEqual(["manual-independent"]);
  expect(await page.evaluate((slot) => sessionStorage.getItem(slot), SELECTED_SLOT)).toBe(selected);
  expect(wire.posts).toHaveLength(0);
});

test("manual committed ID survives failed readback and recovery never reposts", async ({ page }) => {
  const wire = await setup(page);
  wire.readFailure = true;
  await editor(page);
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  await expect(page.getByRole("alert").filter({ hasText: /saved|recorded|committed/i })).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry loading saved report", exact: true });
  await expect(retry).toBeVisible();
  wire.readFailure = false;
  await retry.click();
  await expect(retry).toHaveCount(0);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(1);
});

test("manual write authority is required before opening a new draft", async ({ page }) => {
  const wire = await setup(page, { permissions: ["expense.read"] });
  await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeDisabled();
  await expect(page.getByRole("status").filter({ hasText: /save or check a manual draft/i })).toBeVisible();
  expect(wire.posts).toHaveLength(0);
});

// A typed status lookup still participates in the established global auth
// boundary; session expiry must retain its saved reference through sign-in.
test("manual lookup 401 returns to sign-in while preserving the unresolved reference", async ({ page }) => {
  const stored = JSON.stringify({ key: "manual-expired", digest: "b".repeat(64) });
  const wire = await setup(page, { saved: stored });
  wire.lookupStatus = 401;
  await outcome(page).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(await metadata(page)).toBe(stored);
  expect(wire.posts).toHaveLength(0);
});

test("manual independent identical drafts receive different keys after confirmed completion", async ({ page }) => {
  const wire = await setup(page);
  await editor(page);
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeEnabled();
  wire.report = { ...REPORT, id: "manual-report-2" };
  await editor(page);
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", "/expenses/manual-report-2");
  expect(wire.posts).toHaveLength(2);
  expect(wire.posts[0].payload).toEqual(wire.posts[1].payload);
  expect(wire.posts[0].key).not.toBe(wire.posts[1].key);
});

test("manual unresolved save freezes changed-input and cancel cannot discard its identity", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.lookupStatus = 404;
  await editor(page);
  await save(page);
  await expect(outcome(page)).toBeVisible();
  const stored = await metadata(page);
  await expect(page.getByLabel("Title", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Item 1 amount", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Save draft", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(outcome(page)).toBeVisible();
  await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeDisabled();
  expect(await metadata(page)).toBe(stored);
  wire.lookupStatus = 200;
  await outcome(page).click();
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(1);
});

test("manual outcome remains checkable after navigating away and back", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.lookupStatus = 404;
  await editor(page);
  await save(page);
  await expect(outcome(page)).toBeVisible();
  const stored = await metadata(page);
  await openNavigationSection(page, "Settings & administration");
  await page.getByRole("link", { name: "Settings", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Admin panel", exact: true })).toBeVisible();
  await page.goBack();
  await expect(outcome(page)).toBeVisible();
  expect(await metadata(page)).toBe(stored);
  wire.lookupStatus = 200;
  await outcome(page).click();
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(1);
});

test("manual disabled module exposes no manual write control", async ({ page }) => {
  const wire = await setup(page, { moduleEnabled: false });
  await expect(page.getByText(/The employee expenses module isn.t active/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toHaveCount(0);
  expect(wire.posts).toHaveLength(0);
});

for (const status of [201, 401]) {
  test(`manual late previous-account save ${status} cannot disclose its outcome to another account`, async ({ page }) => {
    const wire = await setup(page);
    wire.holdPost = true;
    await editor(page);
    await save(page);
    await expect.poll(() => !!wire.heldPost).toBe(true);
    const stored = await metadata(page);
    expect(stored).not.toBeNull();
    const abandoned = page.waitForEvent("requestfailed", { predicate: (request) => request.url().endsWith("/expenses") && request.method() === "POST" });
    await page.getByRole("button", { name: "Account menu", exact: true }).click();
    await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
    await page.getByLabel("Email", { exact: true }).fill("second-owner@example.com");
    await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
    const returnUrl = new URL(new URL(page.url()).searchParams.get("next") ?? "/", page.url()).href;
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    // The new identity may mount its shell before Login completes its return
    // redirect. Expand only after that destination and account are ready.
    await expect(page).toHaveURL(returnUrl);
    await expect(page.getByRole("button", { name: "Account menu", exact: true })).toContainText("Synthetic second owner");
    await openNavigationSection(page, "Purchases & expenses");
    await page.getByRole("link", { name: "Expenses", exact: true }).first().click();
    await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeEnabled();
    // Unmount abandons the transport, while a server may still finish. Keep
    // the unresolved reference; neither a later success nor an expiry reply
    // from this old request may affect the second account.
    const canceled = await abandoned;
    expect(canceled.failure()?.errorText).toMatch(/ABORTED|FAILED/);
    await wire.heldPost!.fulfill(json(status === 201 ? REPORT : { detail: "PRIVATE previous-account refusal" }, status));
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(savedLink(page)).toHaveCount(0);
    await expect(page.getByText(/PRIVATE previous-account refusal|Manual draft saved:/)).toHaveCount(0);
    expect(await metadata(page)).toBe(stored);
    expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("synthetic-manual-token-B");
    expect(wire.posts).toHaveLength(1);
  });
}

test("manual uncertain uncommitted save retries only its original frozen inputs and key", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.commitOnLost = false;
  wire.lookupStatus = 404;
  await editor(page);
  await save(page);
  const retry = page.getByRole("button", { name: "Retry original manual save", exact: true });
  await expect(retry).toBeVisible();
  const stored = JSON.parse((await metadata(page))!);
  expect(wire.posts).toHaveLength(1);
  expect(wire.committed).toBe(false);
  const lookupCount = wire.lookups.length;
  wire.lost = false;
  await retry.click();
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(2);
  expect(wire.posts[0]).toEqual(wire.posts[1]);
  expect(wire.posts[1].key).toBe(stored.key);
  expect(wire.lookups.length).toBe(lookupCount + 1);
  await expect.poll(() => metadata(page)).toBeNull();
});

test("manual original-save retry finds a later committed receipt before posting again", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.lookupStatus = 503;
  await editor(page);
  await save(page);
  const retry = page.getByRole("button", { name: "Retry original manual save", exact: true });
  await expect(retry).toBeVisible();
  wire.lookupStatus = 200;
  await retry.click();
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(1);
});

test("manual refresh with lost inputs offers no original-save replay", async ({ page }) => {
  const wire = await setup(page);
  wire.lost = true;
  wire.lookupStatus = 404;
  await editor(page);
  await save(page);
  await expect(page.getByRole("button", { name: "Retry original manual save", exact: true })).toBeVisible();
  await page.reload();
  await expect(outcome(page)).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry original manual save", exact: true })).toHaveCount(0);
  await outcome(page).click();
  await expect(page.getByRole("alert")).toContainText(/unresolved/i);
  expect(wire.posts).toHaveLength(1);
});

for (const [label, stored] of [["null", "null"], ["unsafe key", JSON.stringify({ key: "../another-command", digest: "b".repeat(64) })], ["invalid digest", JSON.stringify({ key: "manual-invalid-digest", digest: "not-a-digest" })]]) {
  test(`manual malformed saved identity with ${label} refuses replacement and lookup`, async ({ page }) => {
    const wire = await setup(page, { saved: stored });
    await expect(page.getByRole("alert").filter({ hasText: /unreadable|administrator/i })).toBeVisible();
    await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeDisabled();
    expect(await metadata(page)).toBe(stored);
    expect(wire.posts).toHaveLength(0);
    expect(wire.lookups).toHaveLength(0);
  });
}

test("manual malformed write success keeps its identity until a verified receipt is available", async ({ page }) => {
  const wire = await setup(page);
  wire.report = { ...REPORT, total: "NaN" };
  wire.lookupStatus = 503;
  await editor(page);
  await save(page);
  await expect(outcome(page)).toBeVisible();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(savedLink(page)).toHaveCount(0);
  const stored = JSON.parse((await metadata(page))!);
  expect(wire.posts).toHaveLength(1);
  expect(stored.key).toBe(wire.posts[0].key);
  wire.report = { ...REPORT };
  wire.lookupStatus = 200;
  await outcome(page).click();
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(1);
});

test("manual failed later receipt lookup preserves its previously verified saved ID", async ({ page }) => {
  const wire = await setup(page);
  await editor(page);
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  wire.receipt = (key) => ({ ...receipt(key), target_id: "another-report" });
  await outcome(page).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  expect(wire.posts).toHaveLength(1);
});

test("manual current read cannot confirm a different report than its committed receipt", async ({ page }) => {
  const wire = await setup(page);
  wire.detailReport = { ...REPORT, id: "different-current-report" };
  await editor(page);
  await save(page);
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  await expect(page.getByRole("alert").filter({ hasText: /saved.*(could not|unavailable|load)/i })).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry loading saved report", exact: true });
  await expect(retry).toBeVisible();
  wire.detailReport = undefined;
  await retry.click();
  await expect(retry).toHaveCount(0);
  expect(wire.posts).toHaveLength(1);
});

// Navigation is allowed while hashing, but the abandoned editor must not
// dispatch a new financial command when its digest eventually completes.
test("manual unmount during hashing refuses the obsolete command after a later editor saves", async ({ page }) => {
  await page.addInitScript(() => {
    const original = crypto.subtle.digest.bind(crypto.subtle);
    let first = true;
    const state = window as unknown as { manualFirstDigestHeld: boolean; manualFirstDigestFinished: boolean; releaseFirstManualDigest?: () => void };
    crypto.subtle.digest = async (...args: Parameters<SubtleCrypto["digest"]>) => {
      const wasFirst = first;
      if (first) {
        first = false;
        state.manualFirstDigestHeld = true;
        await new Promise<void>((resolve) => { state.releaseFirstManualDigest = resolve; });
      }
      const result = await original(...args);
      if (wasFirst) state.manualFirstDigestFinished = true;
      return result;
    };
  });
  const wire = await setup(page);
  await editor(page);
  await save(page);
  await expect.poll(() => page.evaluate(() => (window as unknown as { manualFirstDigestHeld?: boolean }).manualFirstDigestHeld)).toBe(true);
  expect(await metadata(page)).toBeNull();
  await openNavigationSection(page, "Settings & administration");
  await page.getByRole("link", { name: "Settings", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Admin panel", exact: true })).toBeVisible();
  await openNavigationSection(page, "Purchases & expenses");
  await page.getByRole("link", { name: "Expenses", exact: true }).first().click();
  await editor(page);
  await page.getByLabel("Title", { exact: true }).fill("Second independent manual draft");
  await save(page);
  await expect(savedLink(page)).toBeVisible();
  expect(wire.posts).toHaveLength(1);
  expect(wire.posts[0].payload.title).toBe("Second independent manual draft");
  await page.evaluate(() => (window as unknown as { releaseFirstManualDigest: () => void }).releaseFirstManualDigest());
  await expect.poll(() => page.evaluate(() => (window as unknown as { manualFirstDigestFinished?: boolean }).manualFirstDigestFinished)).toBe(true);
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(wire.posts).toHaveLength(1);
  expect(wire.posts[0].payload.title).toBe("Second independent manual draft");
  await expect(savedLink(page)).toBeVisible();
});

test("manual abandoned status lookup preserves its reference until the current editor acknowledges it", async ({ page }) => {
  const key = "manual-abandoned-status";
  const stored = JSON.stringify({ key, digest: "b".repeat(64) });
  let oldLookupSettled = false;
  const isLookup = (url: string) => url.endsWith(`/expenses/commands/${key}`);
  page.on("requestfinished", (request) => { if (isLookup(request.url())) oldLookupSettled = true; });
  page.on("requestfailed", (request) => { if (isLookup(request.url())) oldLookupSettled = true; });
  const wire = await setup(page, { saved: stored });
  wire.holdLookup = true;
  await outcome(page).click();
  await expect.poll(() => !!wire.heldLookup).toBe(true);
  await openNavigationSection(page, "Settings & administration");
  await page.getByRole("link", { name: "Settings", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Admin panel", exact: true })).toBeVisible();
  await openNavigationSection(page, "Purchases & expenses");
  await page.getByRole("link", { name: "Expenses", exact: true }).first().click();
  await expect(outcome(page)).toBeVisible();
  expect(await metadata(page)).toBe(stored);
  await wire.heldLookup!.fulfill(json(receipt(key)));
  await expect.poll(() => oldLookupSettled).toBe(true);
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(await metadata(page)).toBe(stored);
  await expect(savedLink(page)).toHaveCount(0);
  wire.holdLookup = false;
  await outcome(page).click();
  await expect(savedLink(page)).toHaveAttribute("href", `/expenses/${REPORT.id}`);
  await expect.poll(() => metadata(page)).toBeNull();
  expect(wire.lookups).toEqual([key, key]);
  expect(wire.posts).toHaveLength(0);
});
