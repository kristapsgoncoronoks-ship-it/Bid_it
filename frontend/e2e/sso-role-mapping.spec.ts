import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-AE — the SSO panel's role vocabulary comes from the SERVER, and the
 * group → role mapping is editable on the page.
 *
 * What earns its place here, in order of how silently each could go wrong:
 *  - the default-role select renders exactly the list the server served — the
 *    four business roles in, `owner` and the never-was-a-role "processor" out;
 *  - the options are the SERVER's list, not a page constant: serve a shorter
 *    list and the missing role is absent;
 *  - a saved mapping renders as rows, and adding a row PUTs the whole mapping
 *    as {group: role};
 *  - removing a row PUTs the mapping without it (the mapping is replaced, not
 *    merged — a removed group must stop mapping).
 *
 * Live app shell, API mocked via page.route (the suite's standard pattern).
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

// What the backend serves: `roles.IDP_ASSIGNABLE_ROLES` — ASSIGNABLE_ROLES
// minus owner, in declaration order.
const SERVED = ["user_free", "user", "admin", "finance_manager", "accountant", "approver", "auditor"];

interface MockOpts {
  served?: string[];
  mappings?: Record<string, string>;
  onPut?: (body: Record<string, unknown>) => void;
}

async function openSettings(page: Page, opts: MockOpts = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  const served = opts.served ?? SERVED;
  let connection: Record<string, unknown> = {
    id: "sso-1",
    slug: "site-crew",
    protocol: "oidc",
    enabled: true,
    issuer: "https://idp.example",
    client_id: "client",
    allowed_domain: null,
    jit_enabled: true,
    default_role: "user",
    groups_claim: "groups",
    role_mappings: opts.mappings ?? { Finance: "finance_manager" },
    role_sync: false,
    assignable_roles: served,
    saml_metadata_url: null,
    has_client_secret: true,
    scim_enabled: false,
    login_url: "https://api.example/api/v1/auth/sso/site-crew/authorize",
    scim_base_url: "https://api.example/api/v1/scim/v2",
  };

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (p === "/auth/me")
      return route.fulfill(
        json({
          user: {
            id: "user-1",
            email: "dispatcher@fleet.example",
            name: "Dispatcher",
            role: "owner",
            org_id: "org-1",
            is_platform_admin: false,
          },
          organization: ORG,
        }),
      );
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));

    if (p === "/sso/connection") {
      if (method === "PUT") {
        const body = route.request().postDataJSON();
        opts.onPut?.(body);
        connection = { ...connection, ...body };
      }
      return route.fulfill(json(connection));
    }
    if (p === "/sso/assignable-roles") return route.fulfill(json(served));

    // The rest of the settings page: array-shaped feeds must be arrays or the
    // sibling cards crash the page before the SSO panel renders.
    if (p === "/settings/schedule")
      return route.fulfill(json({ assignment_remind_hours: null, client_notice_hours: null }));
    if (p === "/settings/lifecycle")
      return route.fulfill(json({ offer_prefix: null, final_invoice_requires_acceptance: false }));
    if (p === "/jobs" || p === "/webhooks") return route.fulfill(json([]));
    if (p === "/settings/validation")
      return route.fulfill(json({ ai_validation_enabled: false, human_validation_enabled: false }));
    if (p === "/retention") return route.fulfill(json({ categories: [], holds: [] }));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Single sign-on (OIDC)" })).toBeVisible();
}

const optionValues = (page: Page, selector: string) =>
  page.locator(`${selector} option`).evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value));

test("the default-role select offers exactly what the server serves", async ({ page }) => {
  await openSettings(page);
  const values = await optionValues(page, "#default-role-for-new-users");
  expect(values).toEqual(SERVED);
  // The two values the old page got wrong, stated by name.
  expect(values).toContain("auditor");
  expect(values).not.toContain("owner");
  expect(values).not.toContain("processor");
  // Labels are the human names, values are the stored keys.
  await expect(page.locator("#default-role-for-new-users option[value=finance_manager]")).toHaveText(
    "Finance Manager",
  );
});

test("the options are the server's list, not a page constant", async ({ page }) => {
  // Serve a vocabulary WITHOUT accountant: if the page carried its own list the
  // option would still be there.
  await openSettings(page, { served: ["user", "admin", "auditor"] });
  const values = await optionValues(page, "#default-role-for-new-users");
  expect(values).toEqual(["user", "admin", "auditor"]);
  expect(values).not.toContain("accountant");
});

test("a saved mapping renders as rows and adding one saves the whole mapping", async ({ page }) => {
  const puts: Record<string, unknown>[] = [];
  await openSettings(page, { onPut: (b) => puts.push(b) });

  // The saved mapping is on the page, not only in the API.
  await expect(page.getByLabel("Identity-provider group")).toHaveValue("Finance");
  await expect(page.locator("#sso-map-role-0")).toHaveValue("finance_manager");

  await page.getByRole("button", { name: "Add mapping" }).click();
  await page.locator("#sso-map-group-1").fill("Auditors");
  await page.locator("#sso-map-role-1").selectOption("auditor");
  await page.getByRole("button", { name: "Save connection" }).click();

  await expect(page.getByText("SSO connection saved.")).toBeVisible();
  expect(puts).toHaveLength(1);
  expect(puts[0].role_mappings).toEqual({ Finance: "finance_manager", Auditors: "auditor" });
});

test("removing a mapping row saves the mapping without it", async ({ page }) => {
  const puts: Record<string, unknown>[] = [];
  await openSettings(page, {
    mappings: { Finance: "finance_manager", Approvers: "approver" },
    onPut: (b) => puts.push(b),
  });
  await expect(page.locator("#sso-map-group-1")).toHaveValue("Approvers");

  await page.getByRole("button", { name: "Remove" }).nth(1).click();
  await expect(page.locator("#sso-map-group-1")).toHaveCount(0);
  await page.getByRole("button", { name: "Save connection" }).click();

  await expect(page.getByText("SSO connection saved.")).toBeVisible();
  expect(puts).toHaveLength(1);
  expect(puts[0].role_mappings).toEqual({ Finance: "finance_manager" });
});

test("a mapping saved without touching the rows is not re-sent", async ({ page }) => {
  // Untouched rows are not the admin's edit; the PUT carries only what changed
  // (exclude_none on the server means a missing key leaves the mapping alone).
  const puts: Record<string, unknown>[] = [];
  await openSettings(page, { onPut: (b) => puts.push(b) });
  await page.getByLabel("Default role for new users").selectOption("auditor");
  await page.getByRole("button", { name: "Save connection" }).click();
  await expect(page.getByText("SSO connection saved.")).toBeVisible();
  expect(puts).toHaveLength(1);
  expect(puts[0]).toEqual({ default_role: "auditor" });
});
