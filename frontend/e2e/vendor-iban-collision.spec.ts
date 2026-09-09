import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * DB-014 (audit 2026-09-05, P2 batch 8) — the cross-vendor collision surface.
 *
 * Two suppliers paying into one bank account is the payment-redirection
 * signal the second-approver control exists to catch (a factoring company is
 * the legitimate case). The server now names the other holders on the vendor
 * row and on a pending IBAN change; this proves the page SHOWS them — on the
 * row, in the approval inbox and in the confirm dialog — and never hides a
 * request behind them. Live app shell, API mocked via page.route. Synthetic
 * fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };

const json = (body: unknown, code = 200) => ({
  status: code,
  contentType: "application/json",
  body: JSON.stringify(body),
});

type Handler = (p: string, route: Route) => Promise<void> | void | false;

async function signedIn(page: Page, handler: Handler) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const user = { id: "u1", email: "crew@haulage.example", name: "Site Crew", role: "owner", org_id: "org-1", is_platform_admin: false };
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me") return route.fulfill(json({ user, organization: ORG }));
    if (p === "/auth/organizations") return route.fulfill(json([{ org_id: "org-1", name: ORG.name, role: "owner", current: true }]));
    if (p === "/modules") return route.fulfill(json([]));
    if (p === "/dashboard/onboarding") return route.fulfill(json({ steps: [], done_count: 0, complete: true, dismissed: true, can_dismiss: true }));
    const handled = await handler(p, route);
    if (handled === false) return route.fulfill(json([]));
  });
}

const STEEL = { id: "v-steel", name: "Steel GmbH" };
const VENDORS = [
  { id: "v-steel", name: "Steel GmbH", tax_id: null, country: "DE", category: null, iban: "DE89370400440532013000", bic: null, status: "active", version: 1, pending_changes: [], iban_shared_with: [{ id: "v-trading", name: "Steel Trading GmbH" }] },
  { id: "v-trading", name: "Steel Trading GmbH", tax_id: null, country: "DE", category: null, iban: "DE89370400440532013000", bic: null, status: "provisional", version: 1, pending_changes: [], iban_shared_with: [STEEL] },
  { id: "v-cargo", name: "Cargo BV", tax_id: null, country: "NL", category: null, iban: "NL91ABNA0417164300", bic: null, status: "active", version: 2, pending_changes: [], iban_shared_with: [] },
];
const CHANGE = {
  id: "cr-1",
  vendor_id: "v-cargo",
  vendor_name: "Cargo BV",
  field: "iban",
  old_value: "NL91ABNA0417164300",
  new_value: "DE89370400440532013000",
  status: "pending",
  requested_by: "u2",
  requested_by_email: "capture@haulage.example",
  requested_at: "2026-09-09T06:00:00Z",
  decided_by: null,
  decided_by_email: null,
  decided_at: null,
  decision_note: null,
  source_document_id: null,
  shared_with: [STEEL, { id: "v-trading", name: "Steel Trading GmbH" }],
};

test("DB-014: a vendor row names the other suppliers on its account, a lone account says nothing", async ({ page }) => {
  await signedIn(page, (p, route) => {
    if (p === "/vendors") return route.fulfill(json(VENDORS));
    if (p === "/vendors/changes") return route.fulfill(json([]));
    return false;
  });
  await page.goto("/vendors");

  await expect(page.getByText("Same account as Steel Trading GmbH")).toBeVisible();
  await expect(page.getByText("Same account as Steel GmbH")).toBeVisible();
  await expect(page.getByText("Same account as Cargo BV")).toHaveCount(0);
});

test("DB-014: a pending IBAN change names the holders in the inbox and again in the confirm dialog", async ({ page }) => {
  await signedIn(page, (p, route) => {
    if (p === "/vendors") return route.fulfill(json(VENDORS));
    if (p === "/vendors/changes") return route.fulfill(json([CHANGE]));
    return false;
  });
  await page.goto("/vendors");

  await expect(page.getByText("Already on file for Steel GmbH, Steel Trading GmbH")).toBeVisible();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText("This account is already on file for Steel GmbH, Steel Trading GmbH."),
  ).toBeVisible();
  // The decision is still the approver's: the button is there, not hidden.
  await expect(page.getByRole("button", { name: "Approve change" })).toBeVisible();
});
