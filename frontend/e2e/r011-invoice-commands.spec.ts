import { test, expect, type Page } from "@playwright/test";

// Browser recovery/state evidence with synthetic mocked responses. Real
// PostgreSQL commit/concurrency evidence is in test_r011_pg_concurrency.py.
const ORG = { id: "r011-org", name: "Synthetic Workspace", status: "active" };
const USER = { id: "r011-user", org_id: ORG.id, name: "Owner", role: "owner", email: "owner@example.com" };
const PROFILE = { id: "issuer-1", legal_name: "Synthetic Seller", is_complete: true, missing_fields: [], default_penalty_rate: null };
const INVOICE = {
  id: "r011-invoice", number: "INV-011", issuer_id: PROFILE.id, buyer_name: "Synthetic Buyer",
  buyer_email: "buyer@example.com", issue_date: "2026-10-13", due_date: null,
  currency: "EUR", total: "121.00", subtotal: "100.00", tax_total: "21.00",
  lifecycle: "issued", doc_type: "invoice", status: "open", amount_paid: "0.00",
  credited_total: "0.00", outstanding: "121.00", sent_at: null, reminder_count: 0,
  last_reminder_at: null, vat_scheme: "standard", lines: [],
};

async function setup(page: Page, mode: "lost-committed" | "lost-uncommitted" | "delivery") {
  let posts = 0;
  let listed = mode === "delivery";
  let committed = false;
  let key: string | undefined;
  let deliveryPolls = 0;
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([{ key: "issuing", enabled: true, ready: true, requires_issuer: true }]));
    if (path === "/issuer") return route.fulfill(json(PROFILE));
    if (path === "/issuer/entities") return route.fulfill(json([PROFILE]));
    if (path === "/issued" && req.method() === "POST") {
      posts += 1;
      const supplied = req.headers()["idempotency-key"];
      if (key) expect(supplied).toBe(key);
      key = supplied;
      expect(req.headers()["x-expected-org-id"]).toBe(ORG.id);
      expect(req.headers()["x-expected-user-id"]).toBe(USER.id);
      if (posts === 1) {
        committed = mode === "lost-committed";
        listed = committed;
        return route.abort("failed");
      }
      committed = true;
      listed = true;
      return route.fulfill(json(INVOICE, 201));
    }
    if (path.startsWith("/issued/commands/")) return route.fulfill(json(committed ? INVOICE : { detail: "Not found" }, committed ? 200 : 404));
    if (path === "/issued" && req.method() === "GET") return route.fulfill(json({ items: listed ? [INVOICE] : [], total: listed ? 1 : 0, page: 1, page_size: 50 }));
    if (path.endsWith("/send")) return route.fulfill(json({ delivered: false, message: { id: "mail-1", command_key: `invoice:${INVOICE.id}`, status: "queued", error: null } }));
    if (path.includes("/delivery-commands/")) {
      deliveryPolls += 1;
      return route.fulfill(json({ delivered: false, message: { id: "mail-1", command_key: `invoice:${INVOICE.id}`, status: deliveryPolls < 2 ? "queued" : "unknown", error: deliveryPolls < 2 ? null : "SMTP acceptance is uncertain. Check the relay log before requesting a resend." } }));
    }
    if (path === "/recurring" || path === "/partners" || path === "/costing/projects" || path === "/tax-codes") return route.fulfill(json([]));
    return route.fulfill(json({ items: [], total: 0 }));
  });
  await page.goto("/issue");
  return { posts: () => posts, polls: () => deliveryPolls, commitLater: () => { committed = true; listed = true; } };
}

async function fill(page: Page) {
  await page.getByLabel("Customer name", { exact: true }).fill("Synthetic Buyer");
  await page.getByLabel("Line 1 description", { exact: true }).fill("Synthetic Service");
  await page.getByLabel("Line 1 unit price", { exact: true }).fill("100");
}

test("lost create response resolves the committed invoice before any second POST", async ({ page }) => {
  const counters = await setup(page, "lost-committed");
  await fill(page);
  await page.getByRole("button", { name: "Issue invoice", exact: true }).click();
  await expect(page.getByText("INV-011", { exact: true })).toBeVisible();
  expect(counters.posts()).toBe(1);
  await expect(page.getByLabel("Customer name", { exact: true })).toHaveValue("");
});

test("an unknown outcome retries with the same key after a committed-status lookup", async ({ page }) => {
  const counters = await setup(page, "lost-uncommitted");
  await fill(page);
  await page.getByRole("button", { name: "Issue invoice", exact: true }).click();
  await expect(page.getByText(/Request outcome is unknown/)).toBeVisible();
  await page.getByRole("button", { name: "Issue invoice", exact: true }).click();
  await expect(page.getByText("INV-011", { exact: true })).toBeVisible();
  expect(counters.posts()).toBe(2);
});

test("delivery progresses from queued to unknown with a safe explicit recovery", async ({ page }) => {
  const counters = await setup(page, "delivery");
  await page.getByRole("button", { name: "Email", exact: true }).click();
  await expect(page.getByText("Delivery queued for the worker; not yet sent.")).toBeVisible();
  await expect(page.getByText(/SMTP acceptance is uncertain/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Request new delivery", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Request new delivery", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Another delivery can duplicate");
  expect(counters.polls()).toBeLessThanOrEqual(3);
});


test("refresh can check an uncertain command without the original form", async ({ page }) => {
  const counters = await setup(page, "lost-uncommitted");
  await fill(page);
  await page.getByRole("button", { name: "Issue invoice", exact: true }).click();
  await expect(page.getByText(/Request outcome is unknown/)).toBeVisible();
  counters.commitLater();
  await page.reload();
  await page.getByRole("button", { name: "Check previous request", exact: true }).click();
  await expect(page.getByText(/Saved request found: INV-011/)).toBeVisible();
  expect(counters.posts()).toBe(1);
});

test("changed inputs cannot start a replacement while the old command is unknown", async ({ page }) => {
  const counters = await setup(page, "lost-uncommitted");
  await fill(page);
  await page.getByRole("button", { name: "Issue invoice", exact: true }).click();
  await expect(page.getByText(/Request outcome is unknown/)).toBeVisible();
  await page.getByLabel("Customer name", { exact: true }).fill("Changed Buyer");
  await page.getByRole("button", { name: "Issue invoice", exact: true }).click();
  await expect(page.getByText(/A previous request has an unresolved outcome/)).toBeVisible();
  expect(counters.posts()).toBe(1);
});
