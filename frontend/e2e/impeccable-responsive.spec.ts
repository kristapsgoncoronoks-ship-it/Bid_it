import { test, expect, type Page } from "@playwright/test";

// Synthetic UI fixtures: these checks exercise layout, keyboard recovery and
// command dispatch, not live provider or financial side effects.
const ORG = { id: "responsive-org", name: "Synthetic Responsive Workspace", status: "active" };
const USER = { id: "responsive-user", org_id: ORG.id, name: "Operator", email: "operator@example.test", role: "owner" };
const DRAFT_RULE = {
  id: "responsive-rule", revision: "responsive-revision", name: "Review overdue customer invoices",
  trigger: "issued.overdue", condition: { ">": [{ var: "days_overdue" }, 7] },
  actions: [{ kind: "create_customer_note", body: "Review the recorded balance." }],
  status: "draft", fire_policy: "once_per_record", cooldown_hours: null,
  published_version: null, created_at: "2026-10-22T09:00:00Z",
};

async function fixture(page: Page) {
  let published = false;
  const writes: string[] = [];
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "responsive-synthetic-token"));
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() !== "GET") writes.push(`${request.method()} ${path}`);
    if (path === "/auth/me") return json({ user: USER, organization: ORG });
    if (path === "/auth/organizations") return json([ORG]);
    if (path === "/modules") return json([{ key: "transport", name: "Transport", enabled: true, core: false, ready: true, requires_issuer: false }]);
    if (path === "/transport/checklist-rules") return json([]);
    if (path === "/transport/status-codes") return json({ auto: [], manual: [] });
    if (path === "/automation/meta") return json({ triggers: { "issued.overdue": "issued_invoice" }, actions: ["create_customer_note"] });
    if (path === "/automation/rules/responsive-rule/publish" && request.method() === "POST") {
      expect(request.headers()["if-match"]).toBe("responsive-revision");
      published = true;
      return json({ ...DRAFT_RULE, status: "published", published_version: 1 });
    }
    if (path === "/automation/rules") return json([{ ...DRAFT_RULE, status: published ? "published" : "draft", published_version: published ? 1 : null }]);
    if (path === "/automation/runs") return json([{ id: "responsive-run", rule_id: DRAFT_RULE.id, version: 1, ref_id: "synthetic-invoice-reference", status: "ok", detail: null, at: "2026-10-22T09:00:00Z" }]);
    if (path === "/currencies") return json([]);
    if (path.startsWith("/masters/")) return json([]);
    return json([]);
  });
  return writes;
}

async function pageFits(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

for (const width of [320, 390]) {
  test(`long tabs stay contained and keyboard-reveal the last section at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const writes = await fixture(page);
    await page.goto("/vat-admin");
    const strip = page.getByRole("tablist", { name: "VAT refund configuration" });
    await expect(strip).toBeVisible();
    await pageFits(page);
    await expect(page.getByText("Scroll to see all sections.", { exact: true })).toBeVisible();
    const first = strip.getByRole("tab", { name: "Checklist rules", exact: true });
    const last = strip.getByRole("tab", { name: "Status codes", exact: true });
    await first.focus();
    await page.keyboard.press("End");
    await expect(last).toBeFocused();
    await expect(last).toHaveAttribute("aria-selected", "true");
    await expect(last).toBeInViewport({ ratio: 1 });
    expect(await strip.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
    await page.keyboard.press("Home");
    await expect(first).toBeFocused();
    await expect(first).toHaveAttribute("aria-selected", "true");
    await expect(first).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("ArrowLeft");
    await expect(last).toBeFocused();
    await expect(last).toBeInViewport({ ratio: 1 });
    await pageFits(page);
    expect(writes).toEqual([]);
  });

  test(`automation tables scroll locally and publish remains reachable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const writes = await fixture(page);
    await page.goto("/automation");
    await expect(page.getByRole("heading", { name: "Automation", exact: true })).toBeVisible();
    await pageFits(page);
    const region = page.getByRole("region", { name: "Automation rules", exact: true });
    await expect(region).toBeVisible();
    const publish = region.getByRole("button", { name: "Publish", exact: true });
    await publish.focus();
    await expect(publish).toBeInViewport({ ratio: 1 });
    expect(await region.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
    await page.keyboard.press("Enter");
    await expect(region.getByText("published", { exact: true })).toBeVisible();
    expect(writes).toEqual(["POST /automation/rules/responsive-rule/publish"]);
    await expect(page.getByRole("region", { name: "Automation run log", exact: true })).toBeVisible();
    await pageFits(page);
  });
}

test("enlarged-text modal keeps its close and form actions reachable, trapped and recoverable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 568 });
  const writes = await fixture(page);
  await page.goto("/currencies");
  await expect(page.getByRole("heading", { name: "Currencies", exact: true })).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.fontSize = "32px"; });
  const trigger = page.getByRole("button", { name: "New currency", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "New currency", exact: true });
  await expect(dialog).toBeVisible();
  await expect.poll(() => dialog.evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
  })).toBe(true);
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  const close = dialog.getByRole("button", { name: "Close dialog", exact: true });
  const submit = dialog.getByRole("button", { name: "Create currency", exact: true });
  await close.focus();
  await expect(close).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Shift+Tab");
  await expect(submit).toBeFocused();
  await expect(submit).toBeInViewport({ ratio: 1 });
  expect(await dialog.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await expect(close).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("");
  expect(writes).toEqual([]);
});

test("normal text short-height modal retains reachable actions and Escape recovery", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 320 });
  await fixture(page);
  await page.goto("/currencies");
  const trigger = page.getByRole("button", { name: "New currency", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "New currency", exact: true });
  await expect(dialog).toBeVisible();
  const submit = dialog.getByRole("button", { name: "Create currency", exact: true });
  await submit.focus();
  await expect(submit).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("resizing a selected desktop tab reveals it locally without moving the page", async ({ page }) => {
  await fixture(page);
  await page.goto("/vat-admin");
  const strip = page.getByRole("tablist", { name: "VAT refund configuration" });
  const last = strip.getByRole("tab", { name: "Status codes", exact: true });
  await last.click();
  await last.evaluate(el => (el as HTMLElement).blur());
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(last).toBeInViewport({ ratio: 1 });
    await expect(last).toHaveAttribute("aria-selected", "true");
    await pageFits(page);
    expect(await page.evaluate(() => scrollY)).toBe(0);
  }
});

test("below-the-fold tabs do not move the page on mount and skip disabled sections", async ({ page }) => {
  await fixture(page);
  await page.goto("/design/gallery");
  const strip = page.getByRole("tablist", { name: "Example tabs", exact: true });
  await expect(strip).toBeVisible();
  expect(await page.evaluate(() => scrollY)).toBe(0);
  await strip.getByRole("tab", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("End");
  await expect(strip.getByRole("tab", { name: "History", exact: true })).toBeFocused();
  await expect(page.getByRole("tabpanel", { name: "History", exact: true })).toContainText("Active panel: history");
  await expect(strip.getByRole("tab", { name: "Disabled", exact: true })).toBeDisabled();
  await page.keyboard.press("ArrowRight");
  await expect(strip.getByRole("tab", { name: "Overview", exact: true })).toBeFocused();
});

test("standard modal footer stays reachable with enlarged text and short height", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 320 });
  await fixture(page);
  await page.goto("/design/gallery");
  await page.evaluate(() => { document.documentElement.style.fontSize = "32px"; });
  const trigger = page.getByRole("button", { name: "Open modal", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Example modal", exact: true });
  const close = dialog.getByRole("button", { name: "Close dialog", exact: true });
  await close.focus();
  await expect(close).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeFocused();
  await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await expect(close).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("cost-object form actions wrap inside the enlarged-text modal", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 568 });
  const writes = await fixture(page);
  await page.goto("/cost-objects");
  await page.evaluate(() => { document.documentElement.style.fontSize = "32px"; });
  const trigger = page.getByRole("button", { name: "New department", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "New department", exact: true });
  const submit = dialog.getByRole("button", { name: "Create department", exact: true });
  await submit.focus();
  await expect(submit).toBeInViewport({ ratio: 1 });
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(writes).toEqual([]);
});
