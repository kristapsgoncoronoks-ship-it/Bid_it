import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Deleting an invoice, and the consent gate
 * (docs/design/deletion-and-archive.md, step 3) — over the LIVE app shell with
 * the API mocked via `page.route`.
 *
 * The owner's decision is that a client MAY delete a consequential invoice, but
 * must be warned every time and the acceptance recorded. The server enforces
 * that; what these tests pin down is the half that only exists on the client:
 *
 *  - **The warning shown is the SERVER's, rendered verbatim.** The audit trail
 *    records those exact words as what was accepted, so a page that composed its
 *    own wording would make that record false. The mock returns deliberately
 *    distinctive text no client could have invented.
 *  - **The retry echoes the version the server sent**, not a constant. Consent
 *    is to a version; a hardcoded one silently starts lying the moment the
 *    warning is edited.
 *  - **A clean draft is deleted with NO dialog.** A warning shown for everything
 *    is a warning read for nothing.
 *  - **Cancelling deletes nothing.**
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Test User",
  role: "owner",
  org_id: "org-1",
  is_platform_admin: false,
};

// Deliberately NOT the real production wording: if the page ever hardcodes the
// warning, these assertions fail instead of passing by coincidence.
const SERVER_WARNING = {
  version: "test-version-7",
  text: "Fictional warning text that exists only in this test fixture.",
  consequences: [
    { code: "not_draft", message: "It is approved, not a draft." },
    { code: "payment_recorded", message: "A payment of 40.00 EUR is recorded against it." },
  ],
};

const INVOICE = {
  id: "inv-1",
  vendor_id: "v-1",
  vendor_name: "Fictional Fuels OU",
  invoice_number: "INV-2026-0041",
  issue_date: "2026-06-01",
  due_date: null,
  currency: "EUR",
  status: "pending",
  subtotal: "1000.00",
  tax_amount: "240.50",
  total: "1240.50",
  total_eur: "1240.50",
  fx_rate: null,
  fx_source: null,
  validation_status: "none",
  validation_findings: [],
  validated_by: null,
  validated_at: null,
  source_filename: null,
  notes: null,
  cost_center: null,
  department: null,
  project: null,
  vehicle: null,
  property_ref: null,
  line_items: [],
  workflow_state: "approved",
  amount_paid: "40.00",
  paid_date: null,
  outstanding: "1200.50",
  payment_status: "partial",
};

interface MockOpts {
  /** null = the server accepts the delete outright (a clean draft). */
  warning?: typeof SERVER_WARNING | null;
  /** Every DELETE the SPA issued, with the body it sent. */
  onDelete?: (body: unknown) => void;
}

async function open(page: Page, opts: MockOpts = {}) {
  const warning = opts.warning === undefined ? SERVER_WARNING : opts.warning;
  let acknowledged = false;

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/invoices/inv-1" && route.request().method() === "DELETE") {
      const body = route.request().postDataJSON();
      opts.onDelete?.(body ?? null);
      const version = (body as { acknowledged_warning_version?: string } | null)
        ?.acknowledged_warning_version;
      if (warning && version !== warning.version && !acknowledged) {
        // Exactly the server's contract: refuse, and hand back what to show.
        return route.fulfill(
          json(
            {
              detail: "This invoice is not a plain draft.",
              code: "deletion_consent_required",
              warning,
            },
            409,
          ),
        );
      }
      acknowledged = true;
      return route.fulfill({ status: 204, body: "" });
    }

    if (path === "/invoices/inv-1") return route.fulfill(json(INVOICE));
    if (path.startsWith("/invoices/inv-1/")) return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/invoices/inv-1");
}

test("a consequential delete shows the SERVER's warning, word for word", async ({ page }) => {
  await open(page);

  await page.getByRole("button", { name: "Delete", exact: true }).click();

  await expect(page.getByText(SERVER_WARNING.text)).toBeVisible();
  await expect(page.getByText("It is approved, not a draft.")).toBeVisible();
  await expect(page.getByText("A payment of 40.00 EUR is recorded against it.")).toBeVisible();
});

test("confirming re-submits the version the server sent, not a hardcoded one", async ({ page }) => {
  const bodies: unknown[] = [];
  await open(page, { onDelete: (b) => bodies.push(b) });

  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: /I understand/i }).click();

  await expect.poll(() => bodies.length).toBe(2);
  // First attempt carries no acknowledgement; the second echoes the server's.
  expect(bodies[0]).toBeNull();
  expect(bodies[1]).toEqual({ acknowledged_warning_version: SERVER_WARNING.version });
});

test("cancelling the warning deletes nothing further", async ({ page }) => {
  const bodies: unknown[] = [];
  await open(page, { onDelete: (b) => bodies.push(b) });

  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText(SERVER_WARNING.text)).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();

  await expect(page.getByText(SERVER_WARNING.text)).toBeHidden();
  // Only the probe the click made; no second, acknowledged request.
  expect(bodies).toHaveLength(1);
});

test("a clean draft is deleted with no dialog at all", async ({ page }) => {
  // A warning shown for everything is a warning read for nothing.
  await open(page, { warning: null });

  await page.getByRole("button", { name: "Delete", exact: true }).click();

  await expect(page).toHaveURL(/\/invoices$/);
  await expect(page.getByRole("button", { name: /I understand/i })).toHaveCount(0);
});
