import { test, expect, type Page } from "@playwright/test";

const ORG = { id: "r012-org", name: "Synthetic Cash Workspace", status: "active" };
const USER = { id: "r012-user", org_id: ORG.id, name: "Owner", role: "owner", email: "owner@example.com" };
const PROFILE = { id: "issuer-1", legal_name: "Synthetic Seller", is_complete: true, missing_fields: [], default_penalty_rate: null };
const INVOICE = {
  id: "r012-invoice", number: "INV-012", issuer_id: PROFILE.id, buyer_name: "Synthetic Buyer", buyer_email: null,
  issue_date: "2026-10-13", due_date: null, currency: "EUR", total: "121.00", subtotal: "100.00", tax_total: "21.00",
  lifecycle: "issued", doc_type: "invoice", status: "partial", amount_paid: "20.00", credited_total: "24.20",
  outstanding: "76.80", sent_at: null, reminder_count: 0, last_reminder_at: null, vat_scheme: "standard", lines: [], voided_at: null,
};
const ENTRY = { id: "payment-12", amount: "20.00", paid_on: "2026-10-12", method: "bank_transfer", reference: null, note: null, receipt_id: null, reversal_of_id: null };

async function setup(page: Page, mode: "normal" | "readonly" | "lost" | "lost-committed" | "lost-reversal" | "stale" | "paid" = "normal") {
  let invoice = mode === "paid" || mode === "lost-reversal" ? { ...INVOICE, status: "paid", amount_paid: "96.80", outstanding: "0.00" } : { ...INVOICE };
  const posts: { path: string; payload: Record<string, string>; key: string | undefined }[] = [];
  let result: unknown = null;
  let history = [ENTRY];
  const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: { ...USER, role: mode === "readonly" ? "auditor" : "owner" }, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: ORG.id, name: ORG.name, current: true }]));
    if (path === "/modules") return route.fulfill(json([{ key: "issuing", enabled: true, ready: true, requires_issuer: true }]));
    if (path === "/issuer") return route.fulfill(json(PROFILE));
    if (path === "/issuer/entities") return route.fulfill(json([PROFILE]));
    if (path === "/issued") return route.fulfill(json({ items: [invoice], total: 1, page: 1, page_size: 50 }));
    if (path === `/issued/${invoice.id}/payments` && req.method() === "GET") return route.fulfill(json(history));
    if (path.includes("/payment-commands/")) return route.fulfill(json(result ?? { detail: "No committed command" }, result ? 200 : 404));
    if ((path.endsWith("/payments") || path.endsWith("/reverse")) && req.method() === "POST") {
      posts.push({ path, payload: req.postDataJSON(), key: req.headers()["idempotency-key"] });
      expect(req.headers()["x-expected-org-id"]).toBe(ORG.id);
      if ((mode === "lost" || mode === "lost-reversal") && posts.length === 1) return route.abort("failed");
      if (mode === "stale" && posts.length === 1) return route.fulfill(json({ detail: "The outstanding balance changed. Refresh and review before recording cash.", code: "payment_balance_changed" }, 409));
      if (path.endsWith("/reverse")) {
        const reversal = { ...ENTRY, id: "reversal-12", amount: "-20.00", reversal_of_id: ENTRY.id, note: req.postDataJSON().reason };
        history = [...history, reversal];
        invoice = { ...invoice, amount_paid: "76.80", outstanding: "20.00", status: "partial" };
        result = { invoice, payment: reversal };
      } else {
        invoice = { ...invoice, amount_paid: "96.80", outstanding: "0.00", status: "paid" };
        result = { invoice, payment: { ...ENTRY, amount: req.postDataJSON().amount } };
      }
      if (mode === "lost-committed" && posts.length === 1) return route.abort("failed");
      return route.fulfill(json(result));
    }
    if (path === "/recurring" || path === "/partners" || path === "/costing/projects" || path === "/tax-codes") return route.fulfill(json([]));
    return route.fulfill(json({ items: [], total: 0 }));
  });
  await page.goto("/issue");
  return posts;
}

test("Mark paid records only the remaining credited and partially paid balance", async ({ page }) => {
  const posts = await setup(page);
  await page.getByRole("button", { name: "Mark paid", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Payment recorded");
  expect(posts).toHaveLength(1);
  expect(posts[0].payload.amount).toBe("76.80");
  expect(posts[0].payload.expected_outstanding).toBe("76.80");
  expect(posts[0].key).toBeTruthy();
});

test("payment write controls are disabled for an auditor", async ({ page }) => {
  const posts = await setup(page, "readonly");
  await expect(page.getByRole("button", { name: "Mark paid", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Record…", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Payment history", exact: true }).click();
  await expect(page.getByRole("button", { name: "Reverse payment", exact: true })).toBeDisabled();
  expect(posts).toHaveLength(0);
});

test("a lost committed payment response resolves through lookup without another write", async ({ page }) => {
  const posts = await setup(page, "lost-committed");
  await page.getByRole("button", { name: "Mark paid", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Payment recorded");
  expect(posts).toHaveLength(1);
  await expect(page.getByRole("button", { name: "Retry previous payment", exact: true })).toHaveCount(0);
});

test("lost payment response survives refresh and retries the original keyed inputs", async ({ page }) => {
  const posts = await setup(page, "lost");
  await page.getByRole("button", { name: "Record…", exact: true }).click();
  await page.getByLabel("Additional payment amount").fill("76.80");
  await page.getByRole("button", { name: "Record payment", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Request outcome is unknown");
  await page.reload();
  await expect(page.getByRole("button", { name: "Mark paid", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Retry previous payment", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Payment recorded");
  expect(posts).toHaveLength(2);
  expect(posts[1].key).toBe(posts[0].key);
  expect(posts[1].payload).toEqual(posts[0].payload);
});

test("a definite stale balance refusal permits a corrected request", async ({ page }) => {
  const posts = await setup(page, "stale");
  await page.getByRole("button", { name: "Mark paid", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("outstanding balance changed");
  await expect(page.getByRole("button", { name: "Mark paid", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Mark paid", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Payment recorded");
  expect(posts[1].key).not.toBe(posts[0].key);
});

test("paid invoice history remains available and reversal requires a reason", async ({ page }) => {
  const posts = await setup(page, "paid");
  await page.getByRole("button", { name: "Payment history", exact: true }).click();
  await page.getByRole("button", { name: "Reverse payment", exact: true }).click();
  await expect(page.getByRole("button", { name: "Confirm reversal", exact: true })).toBeDisabled();
  await page.getByLabel("Correction reason").fill("Wrong receipt recorded");
  await page.getByRole("button", { name: "Confirm reversal", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Explained reversal recorded");
  await expect(page.getByText("Reversal of payment payment-12.")).toBeVisible();
  expect(posts[0].path).toContain("/payment-12/reverse");
  expect(posts[0].payload.reason).toBe("Wrong receipt recorded");
});

test("a lost reversal survives refresh with its original reason and command key", async ({ page }) => {
  const posts = await setup(page, "lost-reversal");
  await page.getByRole("button", { name: "Payment history", exact: true }).click();
  await page.getByRole("button", { name: "Reverse payment", exact: true }).click();
  await page.getByLabel("Correction reason").fill("Wrong receipt recorded");
  await page.getByRole("button", { name: "Confirm reversal", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Request outcome is unknown");
  await page.reload();
  await page.getByRole("button", { name: "Retry previous payment", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Explained reversal recorded");
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
});
