import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Acceptance & the adjustable final invoice (WO-D) over the LIVE app shell,
 * API mocked via `page.route`.
 *
 * What earns its place here:
 *  - recording acceptance posts {note, document_id} and the card flips to the
 *    accepted state rendered FROM THE WIRE;
 *  - "Prepare final invoice" posts the labelled adjustments, and the composed
 *    lines land IN THE NORMAL ISSUING FORM (one issuing path) with the
 *    project preselected;
 *  - the org gate's 409 surfaces as the error text, not a silent nothing;
 *  - industry-neutral copy.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "someone@test.io",
  name: "Test User",
  role: "owner",
  org_id: "org-1",
  is_platform_admin: false,
};

const PNL = {
  project_id: "proj-1",
  code: "JOB-7",
  name: "Won contract",
  status: "active",
  revenue: "3000.00",
  credited: "0.00",
  costs: "0.00",
  invoice_costs: "0.00",
  expense_costs: "0.00",
  manual_costs: "0.00",
  profit: "3000.00",
  margin_pct: "100.0",
  basis: "net_eur_live",
  adjustments: {},
  pnl_frozen_at: null,
  estimated_revenue: "10000.00",
  accepted_at: null,
  accepted_by: null,
  acceptance_document_id: null,
  acceptance_note: null,
};

interface MockOpts {
  accepted?: boolean;
  gate?: boolean;
  onAccept?: (body: Record<string, unknown>) => void;
  onDraft?: (body: Record<string, unknown>) => void;
}

async function open(page: Page, opts: MockOpts = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  let accepted = opts.accepted ?? false;

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  const pnl = () => ({
    ...PNL,
    accepted_at: accepted ? "2026-08-23T10:00:00+00:00" : null,
    accepted_by: accepted ? "someone@test.io" : null,
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(
        json([{ key: "issuing", enabled: true, label: "Issuing", available: true, core: false, name: "Issuing", ready: true, requires_issuer: true }]),
      );

    if (path === "/masters/projects/proj-1/pnl") return route.fulfill(json(pnl()));
    if (path === "/masters/projects/proj-1/acceptance") {
      if (method === "POST") {
        opts.onAccept?.(route.request().postDataJSON());
        accepted = true;
        return route.fulfill(json(pnl()));
      }
      accepted = false;
      return route.fulfill(json(pnl()));
    }
    if (path === "/masters/projects/proj-1/final-invoice-draft") {
      opts.onDraft?.(route.request().postDataJSON());
      if (opts.gate && !accepted)
        return route.fulfill(
          json({ detail: "this workspace requires a recorded acceptance before the final invoice" }, 409),
        );
      return route.fulfill(
        json({
          project_id: "proj-1",
          contracted_total: "10000.00",
          issued_total: "3000.00",
          remainder: "7000.00",
          lines: [
            { description: "Final invoice — contracted remainder (JOB-7)", quantity: "1", unit_price: "7000.00" },
            { description: "Adjustment — extra work agreed on site", quantity: "1", unit_price: "500.00" },
          ],
          total: "7500.00",
          gate_required: !!opts.gate,
          accepted_at: accepted ? "2026-08-23T10:00:00+00:00" : null,
        }),
      );
    }
    if (path === "/masters/projects/proj-1/cost-entries") return route.fulfill(json([]));
    if (path === "/masters/projects/proj-1/documents")
      return route.fulfill(
        json([{ id: "doc-9", kind: "acceptance", filename: "acceptance-JOB-7.pdf", content_type: "application/pdf", size: 1000, uploaded_by: USER.email, created_at: "2026-08-23T09:00:00Z" }]),
      );
    if (path === "/masters/projects/proj-1/offers") return route.fulfill(json([]));
    if (path === "/masters/projects/proj-1/invoicing-plan")
      return route.fulfill(
        json({ project_id: "proj-1", rows: [], contracted_total: "10000.00", issued_total: "3000.00", remaining: "7000.00" }),
      );
    if (path === "/masters/projects")
      return route.fulfill(
        json([{ id: "proj-1", code: "JOB-7", name: "Won contract", status: "active", version: 1 }]),
      );
    if (path.startsWith("/issued")) return route.fulfill(json({ items: [], total: 0 }));
    if (path.startsWith("/issuer/registry")) return route.fulfill(json([]));
    if (path.startsWith("/issuer")) return route.fulfill(json({ legal_name: "Acme OU", is_complete: true }));
    if (path.startsWith("/partners") || path.startsWith("/recurring")) return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });
}

test("recording acceptance posts note + document and the card flips", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, { onAccept: (b) => (posted = b) });
  await page.goto("/projects/proj-1");

  await expect(page.getByText("Acceptance & final invoice")).toBeVisible();
  await page.locator("select", { hasText: "No document linked" }).selectOption("doc-9");
  await page.getByPlaceholder(/signed at handover/).fill("Signed on site");
  await page.getByRole("button", { name: "Record acceptance" }).click();

  await expect.poll(() => posted).toEqual({ note: "Signed on site", document_id: "doc-9" });
  await expect(page.getByText("accepted", { exact: true })).toBeVisible();
  await expect(page.getByText(/someone@test\.io/).first()).toBeVisible();
});

test("prepare final invoice posts adjustments and lands in the issuing form", async ({ page }) => {
  let drafted: Record<string, unknown> | null = null;
  await open(page, { accepted: true, onDraft: (b) => (drafted = b) });
  await page.goto("/projects/proj-1");

  await page.getByRole("button", { name: "+ Adjustment" }).click();
  await page.getByPlaceholder(/extra work agreed on site/).fill("extra work agreed on site");
  await page.getByPlaceholder("±0.00").fill("500.00");
  await page.getByRole("button", { name: "Prepare final invoice" }).click();

  await expect
    .poll(() => drafted)
    .toEqual({ adjustments: [{ label: "extra work agreed on site", amount: "500.00" }] });
  // The composer's lines arrive in the NORMAL issuing form.
  await expect(page).toHaveURL(/\/issue$/);
  await expect(
    page.locator('input[value="Final invoice — contracted remainder (JOB-7)"]'),
  ).toBeVisible();
  await expect(page.locator('input[value="Adjustment — extra work agreed on site"]')).toBeVisible();
});

test("the acceptance gate's refusal is shown, not swallowed", async ({ page }) => {
  await open(page, { gate: true });
  await page.goto("/projects/proj-1");

  await page.getByRole("button", { name: "Prepare final invoice" }).click();
  await expect(page.getByText(/requires a recorded acceptance/)).toBeVisible();
});

test("the card copy is industry-neutral", async ({ page }) => {
  await open(page);
  await page.goto("/projects/proj-1");
  await expect(page.getByText("Acceptance & final invoice")).toBeVisible();

  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
