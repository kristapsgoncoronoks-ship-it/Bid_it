import { test, expect, type Page } from "@playwright/test";

/**
 * WO-P (R19) — the getting-started card over the LIVE shell, API mocked.
 * The card is a DERIVED projection: these tests pin the presentation contract
 * — done steps read as done, undone steps link to their screens, the dismiss
 * button exists only when the server grants it, and a complete or dismissed
 * card renders NOTHING (no empty husk).
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const STEPS = [
  {
    key: "issuer",
    label: "Set up your company profile",
    detail: "Name, VAT number and bank details.",
    href: "/issuer",
    done: true,
  },
  {
    key: "modules",
    label: "Choose your modules",
    detail: "Turn on the parts this workspace will use.",
    href: "/settings",
    done: true,
  },
  {
    key: "team",
    label: "Invite your team",
    detail: "Roles keep duties separated from day one.",
    href: "/settings",
    done: false,
  },
  {
    key: "customer",
    label: "Add your first customer",
    detail: "A counterparty to bill.",
    href: "/partners",
    done: false,
  },
  {
    key: "invoice",
    label: "Process your first invoice",
    detail: "Upload a supplier invoice or issue one.",
    href: "/upload",
    done: false,
  },
];

const PARTIAL = {
  steps: STEPS,
  done_count: 2,
  complete: false,
  dismissed: false,
  can_dismiss: true,
};

const EMPTY_DASHBOARD = {
  as_of: "2026-07-27",
  approvals: null,
  captures: null,
  payables: null,
  receivables: null,
  cash: null,
};

async function mockApi(
  page: Page,
  onboarding: unknown,
  opts: { onDismiss?: (body: unknown) => void } = {},
): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/dashboard") return route.fulfill(json(EMPTY_DASHBOARD));
    if (path === "/dashboard/onboarding/dismiss") {
      opts.onDismiss?.(null);
      return route.fulfill(
        json({ ...PARTIAL, dismissed: true }),
      );
    }
    if (path === "/dashboard/onboarding") return route.fulfill(json(onboarding));
    return route.fulfill(json([]));
  });
}

test("the card shows progress, strikes done steps and links undone ones", async ({ page }) => {
  await mockApi(page, PARTIAL);
  await page.goto("/");

  const card = page.getByRole("region", { name: "Getting started" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("2 of 5 done");

  // Done steps are struck-through text, not links.
  await expect(card.getByText("Set up your company profile")).toHaveClass(/line-through/);
  await expect(
    card.getByRole("link", { name: "Set up your company profile" }),
  ).toHaveCount(0);

  // Undone steps link to the screen that completes them.
  await expect(card.getByRole("link", { name: "Add your first customer" })).toHaveAttribute(
    "href",
    "/partners",
  );
  await expect(card.getByRole("link", { name: "Process your first invoice" })).toHaveAttribute(
    "href",
    "/upload",
  );
});

test("dismiss posts and the card vanishes for the workspace", async ({ page }) => {
  let dismissed = 0;
  await mockApi(page, PARTIAL, { onDismiss: () => dismissed++ });
  await page.goto("/");

  const card = page.getByRole("region", { name: "Getting started" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Dismiss" }).click();
  await expect(card).toHaveCount(0);
  expect(dismissed).toBe(1);
});

test("no dismiss button without the settings authority", async ({ page }) => {
  await mockApi(page, { ...PARTIAL, can_dismiss: false });
  await page.goto("/");

  const card = page.getByRole("region", { name: "Getting started" });
  await expect(card).toBeVisible();
  await expect(card.getByRole("button", { name: "Dismiss" })).toHaveCount(0);
});

test("a complete card renders nothing at all", async ({ page }) => {
  await mockApi(page, {
    ...PARTIAL,
    steps: STEPS.map((s) => ({ ...s, done: true })),
    done_count: 5,
    complete: true,
  });
  await page.goto("/");

  // Anchor first (check-e2e.mjs): the page rendered…
  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  // …and the card is genuinely absent, not an empty husk.
  await expect(page.getByRole("region", { name: "Getting started" })).toHaveCount(0);
});
