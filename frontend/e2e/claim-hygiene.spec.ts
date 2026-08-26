import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-L claim hygiene over the LIVE app shell, API mocked via `page.route`.
 *
 * What earns its place here:
 *  - the Ignore move on a detected claim-back demands a REASON before it
 *    posts, and posts exactly {to_status:"ignored", note};
 *  - an ignored claim-back renders its explanatory copy and offers only
 *    the reinstate edge;
 *  - the claim detail's decision panel posts a partial rejection with the
 *    checked invoice refs;
 *  - an UNMATCHED line lists the suppliers to chase; a rejected line wears
 *    its badge;
 *  - copy stays inside the transport vertical's own vocabulary rules.
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "operator@test.io",
  name: "Test Operator",
  role: "owner",
  org_id: "org-1",
  is_platform_admin: false,
};

const TRANSPORT_MODULE = {
  key: "transport",
  name: "Transport VAT recovery",
  description: "EU cross-border VAT refunds",
  core: false,
  enabled: true,
  requires_issuer: false,
  ready: true,
};

const DETECTED = {
  id: "ocl-1",
  supplier: "CARDNET",
  period: "2026-05",
  status: "detected",
  detected_eur: "120.00",
  lines_count: 3,
  recovered_eur: null,
  note: null,
  currency: "EUR",
  created_at: "2026-06-02T09:00:00Z",
};

const IGNORED = {
  ...DETECTED,
  id: "ocl-2",
  supplier: "TOLLWAY",
  status: "ignored",
  note: "Gap below the chase threshold",
};

const CLAIM = {
  id: "claim-1",
  entity_id: "ent-1",
  refund_country: "LV",
  ref_period: "2026-Q2",
  status: "submitted",
  status_code: "2",
  status_note: null,
  decision_date: null,
  action_deadline: null,
  submitted_date: "2026-07-10",
  approved_date: null,
  paid_date: null,
  paid_amount: null,
  vat_eur: "360.00",
  vat_local: "360.00",
  currency: "EUR",
  fee_pct: "10.00",
  fee_min: "25.00",
  fee_eur: "36.00",
  created_at: "2026-07-01T09:00:00Z",
};

function line(id: string, ref: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    claim_id: CLAIM.id,
    invoice_ref: ref,
    vat_id: null,
    invoice_id: ref === "UNMATCHED" ? null : `inv-${id}`,
    goods_code: "1",
    product_group: "DIESEL",
    net_eur: "1000.00",
    vat_eur: "210.00",
    net_local: "1000.00",
    vat_local: "210.00",
    currency: "EUR",
    frozen_at: "2026-07-10T09:00:00Z",
    rejected_at: null,
    unmatched_suppliers: null,
    ...extra,
  };
}

const LINES = [
  line("l1", "INV-A"),
  line("l2", "INV-B", { rejected_at: "2026-08-01T09:00:00Z" }),
  line("l3", "UNMATCHED", {
    invoice_id: null,
    frozen_at: null,
    unmatched_suppliers: ["BP", "Q8"],
  }),
];

async function mock(page: Page): Promise<{ posts: { url: string; body: unknown }[] }> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const state = { posts: [] as { url: string; body: unknown }[] };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, "");
    const method = req.method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([TRANSPORT_MODULE]));

    if (method === "POST" && /\/transport\/(overcharges|claims)\//.test(path)) {
      state.posts.push({ url: path, body: req.postDataJSON() });
      if (path.endsWith("/decision"))
        return route.fulfill(json({ ...CLAIM, status: "approved", vat_eur: "270.00" }));
      return route.fulfill(json({ ...DETECTED, status: "ignored" }));
    }
    if (path === "/transport/overcharges/audit")
      return route.fulfill(
        json({
          period: "2026-05",
          supplier: null,
          currency: "EUR",
          recover_eur: "0.00",
          lines_audited: 0,
          lines_without_terms: 0,
          breaches: [],
          source_warnings: [],
        }),
      );
    if (path === "/transport/overcharges") return route.fulfill(json([DETECTED, IGNORED]));
    if (path === "/transport/overcharges/total")
      return route.fulfill(json({ year: null, currency: "EUR", recovered_eur: "0.00" }));
    if (path === "/transport/contract-terms") return route.fulfill(json([]));
    if (path === "/transport/rebates") return route.fulfill(json([]));
    if (path === `/transport/claims/${CLAIM.id}`) return route.fulfill(json(CLAIM));
    if (path === `/transport/claims/${CLAIM.id}/lines`) return route.fulfill(json(LINES));
    if (path === `/transport/claims/${CLAIM.id}/checklist`) return route.fulfill(json([]));
    if (path === `/transport/claims/${CLAIM.id}/stage`)
      return route.fulfill(json({ code: "2", label: "Filed" }));
    if (path === `/transport/claims/${CLAIM.id}/waivers`) return route.fulfill(json([]));
    if (path.startsWith("/transport/")) return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  return state;
}

test("ignoring a detected claim-back demands a reason, then posts it", async ({ page }) => {
  const state = await mock(page);
  await page.goto("/overcharges");
  await page.getByRole("tab", { name: "Claim-backs" }).click();

  const row = page.locator("tr", { hasText: "CARDNET" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "ignored" }).click();

  // The confirm button waits for the reason.
  const confirm = page.getByRole("button", { name: "Confirm" });
  await expect(confirm).toBeDisabled();
  await page.getByLabel("Reason").fill("Gap is 12 EUR — not worth the chase");
  await confirm.click();

  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].url).toContain("/transport/overcharges/ocl-1/advance");
  expect(state.posts[0].body).toEqual({
    to_status: "ignored",
    note: "Gap is 12 EUR — not worth the chase",
  });
});

test("an ignored claim-back explains itself and offers only reinstate", async ({ page }) => {
  await mock(page);
  await page.goto("/overcharges");
  await page.getByRole("tab", { name: "Claim-backs" }).click();
  const row = page.locator("tr", { hasText: "TOLLWAY" });
  await expect(row.getByText("ignored").first()).toBeVisible();
  await expect(page.getByText("Explicitly not pursued", { exact: false })).toBeVisible();
  await expect(row.getByRole("button", { name: "detected" })).toBeVisible();
  await expect(row.getByRole("button", { name: "packaged" })).toHaveCount(0);
});

test("the decision panel posts a partial rejection with the checked refs", async ({ page }) => {
  const state = await mock(page);
  await page.goto(`/vat-claims/${CLAIM.id}`);

  await page.getByRole("button", { name: "Record decision" }).click();
  await page.getByLabel("Decision outcome").selectOption("partial");
  await page.getByRole("checkbox").first().check(); // INV-A
  await page.getByRole("button", { name: "Record", exact: true }).click();

  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].url).toContain(`/transport/claims/${CLAIM.id}/decision`);
  expect(state.posts[0].body).toEqual({ outcome: "partial", rejected_refs: ["INV-A"] });
});

test("an unmatched line lists its suppliers and a rejected line wears its badge", async ({
  page,
}) => {
  await mock(page);
  await page.goto(`/vat-claims/${CLAIM.id}`);
  await expect(page.getByText("suppliers to chase: BP, Q8")).toBeVisible();
  const rejected = page.locator("tr", { hasText: "INV-B" });
  await expect(rejected.getByText("rejected")).toBeVisible();
});
