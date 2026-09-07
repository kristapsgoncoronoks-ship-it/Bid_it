import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * P2 batch 6 — the frontend group (audit 2026-09-05: FE-011, FE-013, FE-018).
 *
 *  - FE-011  91 controls had no programmatic label (a placeholder is not a
 *            label). Every control now has one and `scripts/check-controls.mjs`
 *            keeps it so; these tests prove the association survives rendering
 *            the way an assistive technology (and getByLabel) resolves it.
 *  - FE-013  31 buttons fired a mutation without being disabled while it was
 *            pending — a second click on a slow network sent a second request.
 *            This holds a request open and proves the button is disabled.
 *  - FE-018  twelve call sites formatted with the browser's own locale. Every
 *            date and number now comes from src/lib/format.ts's one LOCALE;
 *            this proves a rendered timestamp is in that locale's shape.
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };

const ISSUER = {
  id: "issuer-1",
  name: "Fictional Freight OÜ",
  is_default: true,
  legal_name: "Fictional Freight OÜ",
  trade_name: null,
  vat_number: null,
  registration_number: "12345678",
  address_line1: "Testivälja 1",
  address_line2: null,
  city: "Tallinn",
  postal_code: "10111",
  country: "EE",
  email: "billing@fictional-freight.test",
  phone: null,
  iban: null,
  bic: null,
  default_currency: "EUR",
  invoice_prefix: "INV",
  credit_note_prefix: "CN",
  next_number: 42,
  payment_terms_days: 14,
  default_penalty_rate: null,
  payment_instructions: null,
  notes: null,
  is_complete: true,
  missing_fields: [],
  has_logo: false,
};

const MODULES = [
  { key: "issuing", name: "Invoice issuing", description: "Issue customer invoices", core: false, enabled: true },
];

const json = (body: unknown, code = 200) => ({
  status: code,
  contentType: "application/json",
  body: JSON.stringify(body),
});

type Handler = (p: string, route: Route) => Promise<void> | void | false;

async function signedIn(page: Page, role: string, handler: Handler) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const user = { id: "u1", email: "crew@haulage.example", name: "Site Crew", role, org_id: "org-1", is_platform_admin: false };
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me") return route.fulfill(json({ user, organization: ORG }));
    if (p === "/auth/organizations") return route.fulfill(json([{ org_id: "org-1", name: ORG.name, role, current: true }]));
    if (p === "/modules") return route.fulfill(json(MODULES));
    if (p === "/dashboard/onboarding") return route.fulfill(json({ steps: [], done_count: 0, complete: true, dismissed: true, can_dismiss: true }));
    const handled = await handler(p, route);
    if (handled === false) return route.fulfill(json([]));
  });
}

// ---------------------------------------------------------------- FE-011

test("FE-011: the invoice line cells are reachable through their labels", async ({ page }) => {
  await signedIn(page, "owner", async (p, route) => {
    if (p === "/issuer" && route.request().method() === "GET") return route.fulfill(json(ISSUER));
    if (p === "/issuer/registry") return route.fulfill(json([ISSUER]));
    if (p === "/issued" && route.request().method() === "GET") return route.fulfill(json({ items: [], total: 0 }));
    return false;
  });
  await page.goto("/issue");

  // A table of unlabeled inputs read as "edit text, edit text, edit text" to a
  // screen reader. Filling THROUGH the label proves the association is real.
  await page.getByLabel("Line 1 description").fill("Fence line, 40 m");
  await expect(page.getByLabel("Line 1 description")).toHaveValue("Fence line, 40 m");
  await page.getByLabel("Line 1 quantity").fill("40");
  await expect(page.getByLabel("Line 1 quantity")).toHaveValue("40");
  await expect(page.getByLabel("Line 1 unit price")).toBeVisible();
  await expect(page.getByLabel("Line 1 VAT rate")).toBeVisible();
});

test("FE-011: a read-only value field is labelled too", async ({ page }) => {
  await signedIn(page, "owner", async (p, route) => {
    if (p === "/team") return route.fulfill(json([]));
    if (p === "/team/invites" && route.request().method() === "GET") {
      return route.fulfill(json([{ id: "inv-1", email: "driver@haulage.example", role: "user", token: "tok-abc", expires_at: "2026-12-01T00:00:00Z", accepted_at: null }]));
    }
    return false;
  });
  await page.goto("/team");
  const link = page.getByLabel("Invitation link");
  await expect(link).toBeVisible();
  await expect(link).toHaveValue(/tok-abc/);
});

// ---------------------------------------------------------------- FE-013

test("FE-013: a mutation button is disabled while its request is in flight", async ({ page }) => {
  let release: (() => void) | null = null;
  const held = new Promise<void>((r) => (release = r));
  let posts = 0;
  const invites: unknown[] = [];
  await signedIn(page, "owner", async (p, route) => {
    if (p === "/team") return route.fulfill(json([]));
    if (p === "/team/invites" && route.request().method() === "GET") return route.fulfill(json(invites));
    if (p === "/team/invites" && route.request().method() === "POST") {
      posts++;
      await held; // the slow network
      const created = { id: "inv-2", email: "driver@haulage.example", role: "user", token: "tok-new", expires_at: "2026-12-01T00:00:00Z", accepted_at: null };
      invites.push(created);
      return route.fulfill(json(created, 201));
    }
    return false;
  });
  await page.goto("/team");
  await page.getByLabel("Email").fill("driver@haulage.example");
  const invite = page.getByRole("button", { name: /invite/i });
  await invite.click();

  // While the POST is held open the button cannot be clicked again.
  await expect(invite).toBeDisabled();
  await expect(invite).toHaveAttribute("aria-busy", "true");
  await invite.click({ force: true, trial: false }).catch(() => undefined);
  expect(posts).toBe(1);

  release!();
  await expect(page.getByLabel("Invitation link")).toHaveValue(/tok-new/);
  // Settled: no longer busy. (The form resets its email on success, so the
  // button's own "nothing to send" disabled state is the one that remains.)
  await expect(invite).not.toHaveAttribute("aria-busy", "true");
  await page.getByLabel("Email").fill("second@haulage.example");
  await expect(invite).toBeEnabled();
});

// ---------------------------------------------------------------- FE-018

test("FE-018: a rendered timestamp follows the product's one locale, not the browser's", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "one engine is enough for a formatting shape");
  await signedIn(page, "owner", async (p, route) => {
    if (p === "/auth/sessions") {
      return route.fulfill(json([{ id: "s-1", created_at: "2026-03-05T14:07:00Z", last_seen_at: "2026-03-05T14:07:00Z", user_agent: "Firefox on Linux", ip: "10.0.0.7", current: true }]));
    }
    return false;
  });
  await page.goto("/sessions");
  const cell = page.getByRole("cell", { name: /05 Mar 2026/ });
  await expect(cell).toBeVisible();
  // en-IE, 24-hour clock, "05 Mar 2026, 14:07" — never "3/5/2026, 2:07 PM".
  await expect(cell).toHaveText(/05 Mar 2026,? \d{2}:\d{2}/);
  await expect(page.getByText(/\d+\/\d+\/\d{4}/)).toHaveCount(0);
});
