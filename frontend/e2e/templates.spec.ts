import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Document templates (lifecycle phase 5 machinery) over the LIVE app shell,
 * API mocked via `page.route` (the suite's standard pattern).
 *
 * What earns its place here — the owner's trust model as the client sees it:
 *  - the masters render as read-only starting points and "Adjust" opens the
 *    editor PREFILLED with the master's text (the copy, not a live pointer);
 *  - saving posts `source_platform_id` so lineage is recorded, and an edit to
 *    an own version PATCHes only that version;
 *  - on a project, generating a document posts `{template_scope, template_id}`
 *    for the CHOSEN version — own or standard — against that project;
 *  - industry-neutral copy on the templates screen (owner requirement).
 *
 * Synthetic fixtures only.
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

const MASTER = {
  id: "pt-1",
  key: "demo-contract",
  kind: "contract",
  name: "Service contract (demo)",
  description: "A demo contract",
  body: "DEMO TEMPLATE — an example, not legal advice.\n\nBetween {{company.legal_name}} and {{customer.name}} for {{project.name}}.",
};

const OWN = {
  id: "ot-1",
  kind: "contract",
  name: "Our contract (strict)",
  body: "Our own text for {{project.code}}.",
  active: true,
  source_platform_id: "pt-1",
  created_at: "2026-08-16T10:00:00Z",
};

interface MockOpts {
  own?: Record<string, unknown>[];
  onSave?: (body: Record<string, unknown>) => void;
  onPatch?: (id: string, body: Record<string, unknown>) => void;
  onGenerate?: (body: Record<string, unknown>) => void;
}

async function open(page: Page, opts: MockOpts = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/templates") {
      if (method === "POST") {
        opts.onSave?.(route.request().postDataJSON());
        return route.fulfill(json({ ...OWN, id: "ot-new" }, 201));
      }
      return route.fulfill(json({ platform: [MASTER], own: opts.own ?? [] }));
    }
    if (path.startsWith("/templates/")) {
      if (method === "PATCH") {
        opts.onPatch?.(path.split("/").pop()!, route.request().postDataJSON());
        return route.fulfill(json(OWN));
      }
      if (method === "DELETE") return route.fulfill(json({ ok: true }));
    }

    if (path === "/masters/projects/proj-1/generate-document") {
      opts.onGenerate?.(route.request().postDataJSON());
      return route.fulfill(
        json(
          {
            id: "doc-1",
            kind: "contract",
            filename: "contract-JOB-7.pdf",
            content_type: "application/pdf",
            size: 1234,
            uploaded_by: USER.email,
            created_at: "2026-08-16T10:00:00Z",
          },
          201,
        ),
      );
    }
    if (path === "/masters/projects/proj-1/pnl")
      return route.fulfill(
        json({
          project_id: "proj-1",
          code: "JOB-7",
          name: "Won contract",
          status: "active",
          revenue: "0.00",
          credited: "0.00",
          costs: "0.00",
          invoice_costs: "0.00",
          expense_costs: "0.00",
          manual_costs: "0.00",
          profit: "0.00",
          margin_pct: null,
          basis: "net_eur_live",
          adjustments: {},
          pnl_frozen_at: null,
          estimated_revenue: null,
        }),
      );
    if (path === "/masters/projects/proj-1/cost-entries") return route.fulfill(json([]));
    if (path === "/masters/projects/proj-1/documents") return route.fulfill(json([]));
    if (path === "/masters/projects/proj-1/offers") return route.fulfill(json([]));
    if (path === "/masters/projects/proj-1/invoicing-plan")
      return route.fulfill(
        json({
          project_id: "proj-1",
          rows: [],
          contracted_total: "0.00",
          issued_total: "0.00",
          remaining: "0.00",
        }),
      );

    return route.fulfill(json({ items: [], total: 0 }));
  });
}

test("adjusting a master prefills the editor and saving records the lineage", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, { onSave: (b) => (posted = b) });
  await page.goto("/templates");

  await expect(page.getByRole("heading", { name: "Document templates" })).toBeVisible();
  await expect(page.getByText("Service contract (demo)")).toBeVisible();

  await page.getByRole("button", { name: "Adjust" }).click();
  // The editor holds the MASTER'S text — a copy the client now owns.
  const body = page.locator("textarea");
  await expect(body).toHaveValue(/not legal advice/);
  await expect(page.locator('input.input').first()).toHaveValue(
    "Service contract (demo) — our version",
  );

  await body.fill("Our adjusted text for {{project.code}}.");
  await page.getByRole("button", { name: "Save version" }).click();

  await expect.poll(() => posted).toEqual({
    name: "Service contract (demo) — our version",
    kind: "contract",
    body: "Our adjusted text for {{project.code}}.",
    source_platform_id: "pt-1",
  });
});

test("editing an own version patches only that version", async ({ page }) => {
  let patched: { id: string; body: Record<string, unknown> } | null = null;
  await open(page, { own: [OWN], onPatch: (id, body) => (patched = { id, body }) });
  await page.goto("/templates");

  await expect(page.getByText("Our contract (strict)")).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.locator("textarea").fill("Tightened wording.");
  await page.getByRole("button", { name: "Save version" }).click();

  await expect
    .poll(() => patched)
    .toEqual({
      id: "ot-1",
      body: { name: "Our contract (strict)", body: "Tightened wording." },
    });
});

test("generating from a project posts the chosen version's scope and id", async ({ page }) => {
  let generated: Record<string, unknown> | null = null;
  await open(page, { own: [OWN], onGenerate: (b) => (generated = b) });
  await page.goto("/projects/proj-1");

  await expect(page.getByRole("heading", { name: "JOB-7 · Won contract" })).toBeVisible();
  // The picker offers the client's own version AND the standard one.
  const select = page.locator("select", { hasText: "Generate from template…" });
  await expect(select.locator("option")).toHaveCount(3);
  await select.selectOption("own:ot-1");
  await page.getByRole("button", { name: "Generate", exact: true }).click();

  await expect.poll(() => generated).toEqual({
    template_scope: "own",
    template_id: "ot-1",
  });
});

test("the templates screen is industry-neutral", async ({ page }) => {
  await open(page, { own: [OWN] });
  await page.goto("/templates");
  await expect(page.getByRole("heading", { name: "Document templates" })).toBeVisible();

  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
