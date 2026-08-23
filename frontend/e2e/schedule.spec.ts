import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Work-planning schedule (WO-A) over the LIVE app shell, API mocked via
 * `page.route` (the suite's standard pattern).
 *
 * What earns its place here:
 *  - a planner sees the week grid with assignments and the planning form,
 *    and saving posts exactly {project_id, assignee_user_id, starts_at,
 *    ends_at, all_day, note};
 *  - the ADVISORY overlap contract: a save whose response names overlaps
 *    renders a warning notice — and the assignment still saved;
 *  - a non-planner (members endpoint 403) gets the personal view: no
 *    "+ Assign" button, no filters, own rows rendered;
 *  - industry-neutral copy (owner guard list).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

const ASSIGNMENT = {
  id: "asg-1",
  project_id: "proj-1",
  assignee_user_id: "user-1",
  assignee_email: "someone@test.io",
  starts_at: "2026-09-01T09:00:00+00:00",
  ends_at: "2026-09-01T17:00:00+00:00",
  all_day: false,
  status: "planned",
  note: "Bring the signed contract",
  created_by: "someone@test.io",
};

interface MockOpts {
  planner?: boolean;
  onCreate?: (body: Record<string, unknown>) => void;
  createOverlaps?: Record<string, unknown>[];
}

async function open(page: Page, opts: MockOpts = {}) {
  const planner = opts.planner ?? true;
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  await page.clock.setFixedTime(new Date("2026-09-02T10:00:00Z"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (path === "/auth/me")
      return route.fulfill(
        json({
          user: {
            id: "user-1",
            email: "someone@test.io",
            name: "Test User",
            role: planner ? "owner" : "user",
            org_id: "org-1",
            is_platform_admin: false,
          },
          organization: ORG,
        }),
      );
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/schedule/members") {
      if (!planner) return route.fulfill(json({ detail: "forbidden" }, 403));
      return route.fulfill(
        json([
          { user_id: "user-1", email: "someone@test.io", name: "Test User" },
          { user_id: "user-2", email: "crew@test.io", name: "Crew Person" },
        ]),
      );
    }
    if (path === "/schedule/assignments") {
      if (method === "POST") {
        opts.onCreate?.(route.request().postDataJSON());
        return route.fulfill(
          json(
            {
              assignment: { ...ASSIGNMENT, id: "asg-new" },
              overlaps: opts.createOverlaps ?? [],
            },
            201,
          ),
        );
      }
      return route.fulfill(json([ASSIGNMENT]));
    }
    if (path === "/masters/projects")
      return route.fulfill(
        json([{ id: "proj-1", code: "JOB-7", name: "Won contract", status: "active", version: 1 }]),
      );

    return route.fulfill(json({ items: [], total: 0 }));
  });
}

test("a planner sees the week, and saving posts what was typed", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, { onCreate: (b) => (posted = b) });
  await page.goto("/schedule");

  await expect(page.getByRole("heading", { name: "Schedule" })).toBeVisible();
  // The mocked assignment renders in its day cell with status + person.
  await expect(page.getByText("JOB-7", { exact: true })).toBeVisible();
  await expect(page.getByText("planned")).toBeVisible();
  await expect(page.getByText(/someone@test\.io/).first()).toBeVisible();

  await page.getByRole("button", { name: "+ Assign" }).click();
  await page.locator('label:text-is("Person") ~ select').selectOption("user-2");
  await page.locator('input[type="date"]').fill("2026-09-03");
  await page.getByPlaceholder("Bring the signed contract").fill("Gate code 4711");
  await page.getByRole("button", { name: "Save assignment" }).click();

  await expect.poll(() => posted).not.toBeNull();
  const body = posted as unknown as Record<string, unknown>;
  expect(body.project_id).toBe("proj-1");
  expect(body.assignee_user_id).toBe("user-2");
  expect(body.all_day).toBe(false);
  expect(body.note).toBe("Gate code 4711");
  expect(String(body.starts_at)).toContain("2026-09-03");
});

test("overlap warnings are advisory: saved, and said out loud", async ({ page }) => {
  await open(page, {
    createOverlaps: [
      {
        ...ASSIGNMENT,
        id: "asg-other",
        starts_at: "2026-09-03T08:00:00+00:00",
        ends_at: "2026-09-03T12:00:00+00:00",
      },
    ],
  });
  await page.goto("/schedule");

  await page.getByRole("button", { name: "+ Assign" }).click();
  await page.getByRole("button", { name: "Save assignment" }).click();

  await expect(page.getByText(/also booked/)).toBeVisible();
  await expect(page.getByText(/saved anyway/)).toBeVisible();
});

test("a non-planner gets the personal view: own rows, no planning surface", async ({ page }) => {
  await open(page, { planner: false });
  await page.goto("/schedule");

  await expect(page.getByRole("heading", { name: "Schedule" })).toBeVisible();
  await expect(page.getByText("Your assignments.", { exact: false })).toBeVisible();
  // The row still renders (the server already scoped it to the caller)…
  await expect(page.getByText("planned")).toBeVisible();
  // …but nothing lets them plan.
  await expect(page.getByRole("button", { name: "+ Assign" })).toHaveCount(0);
  await expect(page.getByText("Everyone")).toHaveCount(0);
});

test("the schedule copy is industry-neutral", async ({ page }) => {
  await open(page);
  await page.goto("/schedule");
  await expect(page.getByRole("heading", { name: "Schedule" })).toBeVisible();

  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
