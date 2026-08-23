import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Next actions (WO-C) on the dashboard, API mocked via `page.route`.
 *
 * What earns its place here:
 *  - the card renders derived items with their deep links;
 *  - Dismiss posts {kind, ref_id} and the card refetches;
 *  - a deadline's Done posts to its complete endpoint;
 *  - the card is ABSENT for an empty list (no noise) and for non-planners;
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

const ACTIONS = [
  {
    kind: "offer_followup",
    ref_id: "off-1",
    title: "Follow up on offer OFF-7",
    detail: "Sent 5 days ago with no answer — Quote v1.",
    link: "/projects/proj-1",
    age_days: 5,
    due_date: null,
    dismissible: true,
  },
  {
    kind: "deadline",
    ref_id: "dl-1",
    title: "Prepare the VAT report",
    detail: "Due 2026-08-28. Mark done when handled.",
    link: "/",
    age_days: null,
    due_date: "2026-08-28",
    dismissible: false,
  },
];

interface MockOpts {
  actions?: unknown;
  forbidden?: boolean;
  onDismiss?: (body: Record<string, unknown>) => void;
  onComplete?: (id: string) => void;
}

async function open(page: Page, opts: MockOpts = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  let dismissed = false;

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

    if (path === "/next-actions") {
      if (opts.forbidden) return route.fulfill(json({ detail: "forbidden" }, 403));
      const items = (opts.actions as typeof ACTIONS | undefined) ?? ACTIONS;
      return route.fulfill(json(dismissed ? items.slice(1) : items));
    }
    if (path === "/next-actions/dismiss") {
      dismissed = true;
      opts.onDismiss?.(route.request().postDataJSON());
      return route.fulfill({ status: 204, contentType: "application/json", body: "" });
    }
    if (path.startsWith("/next-actions/deadlines/") && path.endsWith("/complete")) {
      opts.onComplete?.(path.split("/")[3]);
      return route.fulfill(
        json({ id: "dl-1", name: "Prepare the VAT report", cadence: "monthly", due_day: 28, lead_days: 7, last_done_period: "2026-08" }),
      );
    }
    if (path === "/dashboard") return route.fulfill(json({}));

    return route.fulfill(json({ items: [], total: 0 }));
  });
}

test("derived items render with links, dismiss posts and refetches", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, { onDismiss: (b) => (posted = b) });
  await page.goto("/");

  await expect(page.getByText("Next actions")).toBeVisible();
  await expect(page.getByRole("link", { name: "Follow up on offer OFF-7" })).toHaveAttribute(
    "href",
    "/projects/proj-1",
  );
  await expect(page.getByText(/Sent 5 days ago/)).toBeVisible();

  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect.poll(() => posted).toEqual({ kind: "offer_followup", ref_id: "off-1" });
  // The dismissed item is gone; the deadline stays.
  await expect(page.getByText("Follow up on offer OFF-7")).toHaveCount(0);
  await expect(page.getByText("Prepare the VAT report")).toBeVisible();
});

test("a deadline's Done posts to complete", async ({ page }) => {
  let completed: string | null = null;
  await open(page, { onComplete: (id) => (completed = id) });
  await page.goto("/");

  await expect(page.getByText("Prepare the VAT report")).toBeVisible();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect.poll(() => completed).toBe("dl-1");
});

test("no items and no rights both mean NO card", async ({ page }) => {
  await open(page, { actions: [] });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(page.getByText("Next actions")).toHaveCount(0);

  await page.unrouteAll();
  await open(page, { forbidden: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(page.getByText("Next actions")).toHaveCount(0);
});

test("the card copy is industry-neutral", async ({ page }) => {
  await open(page);
  await page.goto("/");
  await expect(page.getByText("Next actions")).toBeVisible();

  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
