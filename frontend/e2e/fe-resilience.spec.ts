import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Frontend resilience (audit 2026-09-05: FE-001/002/003/008, FE-019).
 *
 * Four defects, each one verified in the tree before the fix and each one a
 * whole class rather than a page:
 *
 *  - FE-001  a mutation with no `onError` of its own failed SILENTLY (21 of
 *            them: revoke session, remove transaction, delete draft…). The
 *            `MutationCache` backstop now toasts the server's message.
 *  - FE-002  the 401 interceptor bounced EVERY 401 to /login — including the
 *            accept-invite / reset / verify screens, whose 401 IS the message —
 *            and forgot where the person was heading. Public paths are left
 *            alone; protected ones carry `?next=` and sign-in honours it,
 *            same-origin relative paths only.
 *  - FE-003  no ErrorBoundary: one render throw blanked the whole app. Now the
 *            page shows an error state and the shell + nav survive.
 *  - FE-008  thirteen deletes fired with no confirmation, and Customers said
 *            "archive" while calling DELETE (a soft deactivation). Every one
 *            now asks first through the design system's ConfirmDialog.
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };
const USER = { id: "u1", email: "owner@haulage.example", name: "Owner", role: "owner", org_id: "org-1", is_platform_admin: false };

const json = (body: unknown, code = 200) => ({
  status: code,
  contentType: "application/json",
  body: JSON.stringify(body),
});

type Handler = (p: string, route: Route) => Promise<void> | void | false;

/** Mount the shell as a signed-in owner; `handler` answers everything but the
 * identity/boot routes (return `false` to fall through to an empty 200). */
async function signedIn(page: Page, handler: Handler) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));
    if (p === "/dashboard/onboarding") return route.fulfill(json({ steps: [], done_count: 0, complete: true, dismissed: true, can_dismiss: true }));
    const handled = await handler(p, route);
    if (handled === false) return route.fulfill(json({}));
  });
}

// ---------------------------------------------------------------- FE-001

test("FE-001: a mutation with no onError of its own surfaces the server's message", async ({ page }) => {
  await signedIn(page, async (p, route) => {
    if (p === "/auth/sessions" && route.request().method() === "GET") {
      return route.fulfill(
        json([
          { id: "s-here", created_at: "2026-09-01T08:00:00Z", last_seen_at: null, user_agent: "This laptop", ip: "10.0.0.1", current: true },
          { id: "s-phone", created_at: "2026-09-02T08:00:00Z", last_seen_at: null, user_agent: "Driver phone", ip: "10.0.0.2", current: false },
        ]),
      );
    }
    if (p === "/auth/sessions/s-phone" && route.request().method() === "DELETE") {
      return route.fulfill(json({ detail: "That session was already revoked by an administrator", code: "session_gone" }, 409));
    }
    return false;
  });
  await page.goto("/sessions");
  await expect(page.getByText("Driver phone")).toBeVisible();
  await page.getByRole("button", { name: "Revoke" }).click();
  // Before the backstop this click did nothing visible at all.
  await expect(page.getByRole("alert").filter({ hasText: "already revoked by an administrator" })).toBeVisible();
});

// ---------------------------------------------------------------- FE-002

test("FE-002: a 401 on the accept-invite screen stays on the screen and shows the message", async ({ page }) => {
  let bounced = false;
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/invite/tok-1") return route.fulfill(json({ organization_name: "Haulage Co", email: "driver@haulage.example", role: "user" }));
    if (p === "/auth/accept-invite") {
      return route.fulfill(json({ detail: "This invitation was revoked by the workspace admin", code: "invite_revoked" }, 401));
    }
    return route.fulfill(json({}));
  });
  page.on("framenavigated", (f) => {
    if (f === page.mainFrame() && new URL(f.url()).pathname.startsWith("/login")) bounced = true;
  });
  await page.goto("/accept-invite?token=tok-1");
  await expect(page.getByRole("heading", { name: "Join Haulage Co" })).toBeVisible();
  await page.getByLabel("Your name").fill("Driver");
  await page.getByLabel("Set a password").fill("supersecret");
  await page.getByRole("button", { name: "Join workspace" }).click();
  await expect(page.getByText("This invitation was revoked by the workspace admin")).toBeVisible();
  await expect(page).toHaveURL(/\/accept-invite\?token=tok-1$/);
  expect(bounced).toBe(false);
});

test("FE-002: a dead session on a protected page returns there after sign-in", async ({ page }) => {
  // Seed the dead token ONCE: an init script re-runs on every document load,
  // and the interceptor's redirect is a full load — re-seeding would undo the
  // very clear() this test asserts.
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("seeded")) {
      localStorage.setItem("invoiceiq_token", "stale-token");
      sessionStorage.setItem("seeded", "1");
    }
  });
  let authed = false;
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me") {
      if (!authed) return route.fulfill(json({ detail: "Could not validate credentials" }, 401));
      return route.fulfill(json({ user: USER, organization: ORG }));
    }
    if (p === "/auth/login") {
      authed = true;
      return route.fulfill(json({ token: { access_token: "fresh-token", token_type: "bearer" }, user: USER, organization: ORG }));
    }
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));
    if (p === "/invoices") return route.fulfill(json({ items: [], total: 0, page: 2, page_size: 20 }));
    return route.fulfill(json({}));
  });
  await page.goto("/invoices?page=2");
  // The interceptor cleared the dead token and remembered the destination.
  await expect(page).toHaveURL(/\/login\?next=%2Finvoices%3Fpage%3D2$/);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBeNull();
  await page.getByLabel("Email").fill("owner@haulage.example");
  await page.getByLabel("Password").fill("supersecret");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/invoices\?page=2$/);
});

test("FE-002: `next` is never an open redirect", async ({ page }) => {
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/login") {
      return route.fulfill(json({ token: { access_token: "fresh-token", token_type: "bearer" }, user: USER, organization: ORG }));
    }
    if (p === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));
    return route.fulfill(json({}));
  });
  for (const evil of ["//evil.example/steal", "https://evil.example", "/login"]) {
    await page.goto(`/login?next=${encodeURIComponent(evil)}`);
    await page.getByLabel("Email").fill("owner@haulage.example");
    await page.getByLabel("Password").fill("supersecret");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/^http:\/\/localhost:5173\/$/);
    await page.evaluate(() => localStorage.removeItem("invoiceiq_token"));
  }
});

// ---------------------------------------------------------------- FE-003

test("FE-003: a render throw takes out the page, not the shell", async ({ page }) => {
  await signedIn(page, async (p, route) => {
    // The review queue reads `d.items.length`; an empty object is a render
    // throw — before the boundary this blanked the entire app.
    if (p === "/invoices") return route.fulfill(json({}));
    if (p === "/settings/validation") return route.fulfill(json({ ai_validation_enabled: true, human_validation_enabled: true }));
    return false;
  });
  await page.goto("/review");
  await expect(page.getByRole("alert").filter({ hasText: "This page hit an error" })).toBeVisible();
  // The shell survived: the primary nav is still there and still navigates.
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
  await nav.getByRole("link", { name: "Dashboard" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("alert").filter({ hasText: "This page hit an error" })).toHaveCount(0);
});

// ---------------------------------------------------------------- FE-008

test("FE-008: deactivating a customer asks first, says what happens, and only then calls DELETE", async ({ page }) => {
  const deletes: string[] = [];
  await signedIn(page, async (p, route) => {
    if (p === "/customers" && route.request().method() === "GET") {
      return route.fulfill(
        json([
          { id: "c-1", name: "Site Crew OU", vat_number: "EE100000001", city: "Tallinn", country: "EE", payment_terms_days: 14, default_currency: "EUR", email: null },
        ]),
      );
    }
    if (p.startsWith("/customers/") && route.request().method() === "DELETE") {
      deletes.push(p);
      return route.fulfill({ status: 204, body: "" });
    }
    return false;
  });
  await page.goto("/customers");
  await expect(page.getByText("Site Crew OU")).toBeVisible();
  // The verb is honest now: the server soft-deactivates, so the button says so.
  await expect(page.getByRole("button", { name: "archive" })).toHaveCount(0);
  await page.getByRole("button", { name: "deactivate" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Deactivate Site Crew OU?" })).toBeVisible();
  await expect(dialog.getByText("Invoices already issued keep their link")).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  expect(deletes).toEqual([]);

  await page.getByRole("button", { name: "deactivate" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Deactivate" }).click();
  await expect.poll(() => deletes).toEqual(["/customers/c-1"]);
  await expect(page.getByRole("alert").filter({ hasText: "Customer deactivated" })).toBeVisible();
});
