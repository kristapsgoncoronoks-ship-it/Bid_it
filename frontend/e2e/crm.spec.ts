import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * CRM light (WO-H) over the LIVE app shell, API mocked via `page.route`.
 *
 * What earns its place here:
 *  - the customer page renders notes + the derived timeline, posting a note
 *    sends {body}, changing lifecycle PUTs the stage;
 *  - the pipeline kanban renders columns, and a quiet sent offer carries
 *    the red chase badge while a fresh one does not;
 *  - copy is industry-neutral (owner guard list).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

const CUSTOMER = {
  id: "cust-1",
  name: "Riverbank Office",
  email: "front@riverbank.example",
  lifecycle: "active",
  vat_number: null,
  city: null,
  country: "LV",
};

async function open(
  page: Page,
  path: string,
  opts: {
    onNote?: (b: Record<string, unknown>) => void;
    onLifecycle?: (b: Record<string, unknown>) => void;
  } = {},
) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (p === "/auth/me")
      return route.fulfill(
        json({
          user: {
            id: "user-1",
            email: "someone@test.io",
            name: "Test User",
            role: "owner",
            org_id: "org-1",
            is_platform_admin: false,
          },
          organization: ORG,
        }),
      );
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([{ key: "issuing", enabled: true }]));

    if (p === "/customers") return route.fulfill(json([CUSTOMER]));
    if (p === "/customers/cust-1/notes") {
      if (method === "POST") {
        opts.onNote?.(route.request().postDataJSON());
        return route.fulfill(
          json(
            {
              id: "note-new",
              body: "Prefers morning calls",
              created_by: "someone@test.io",
              created_at: "2026-08-25T10:00:00+00:00",
            },
            201,
          ),
        );
      }
      return route.fulfill(
        json([
          {
            id: "note-1",
            body: "Gate code 4711",
            created_by: "someone@test.io",
            created_at: "2026-08-20T10:00:00+00:00",
          },
        ]),
      );
    }
    if (p === "/customers/cust-1/lifecycle" && method === "PUT") {
      opts.onLifecycle?.(route.request().postDataJSON());
      return route.fulfill(json({ ...CUSTOMER, lifecycle: "dormant", contacts: [] }));
    }
    if (p === "/customers/cust-1/timeline")
      return route.fulfill(
        json({
          events: [
            {
              at: "2026-08-24T10:00:00+00:00",
              kind: "offer",
              title: "Offer OFF-1 v1 sent",
              ref: "/projects/proj-1",
            },
            {
              at: "2026-08-20T10:00:00+00:00",
              kind: "note",
              title: "Gate code 4711 — someone@test.io",
              ref: null,
            },
          ],
        }),
      );

    if (p === "/masters/offers-pipeline")
      return route.fulfill(
        json({
          stale_after_days: 14,
          columns: {
            sent: [
              {
                offer_id: "off-quiet",
                number: "OFF-1",
                version: 1,
                title: "Renovation quote",
                total: "900.00",
                currency: "EUR",
                project_id: "proj-1",
                project: "JOB-7 · Won contract",
                customer: "Riverbank Office",
                days_in_stage: 21,
                stale: true,
              },
              {
                offer_id: "off-fresh",
                number: "OFF-2",
                version: 1,
                title: null,
                total: "100.00",
                currency: "EUR",
                project_id: "proj-1",
                project: "JOB-7 · Won contract",
                customer: null,
                days_in_stage: 1,
                stale: false,
              },
            ],
            accepted: [],
          },
        }),
      );

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto(path);
}

test("the customer page shows notes + derived timeline, posts and moves stage", async ({
  page,
}) => {
  const notes: Record<string, unknown>[] = [];
  const stages: Record<string, unknown>[] = [];
  await open(page, "/customers/cust-1", {
    onNote: (b) => notes.push(b),
    onLifecycle: (b) => stages.push(b),
  });

  await expect(page.getByRole("heading", { name: "Riverbank Office" })).toBeVisible();
  await expect(page.getByText("Gate code 4711", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Offer OFF-1 v1 sent")).toBeVisible();

  await page.getByPlaceholder(/Prefers morning calls/).fill("Prefers morning calls");
  await page.getByRole("button", { name: "Add note" }).click();
  await expect.poll(() => notes.length).toBe(1);
  expect(notes[0]).toEqual({ body: "Prefers morning calls" });

  await page.locator("select").first().selectOption("dormant");
  await expect.poll(() => stages.length).toBe(1);
  expect(stages[0]).toEqual({ lifecycle: "dormant" });
});

test("the pipeline kanban flags the rotting offer and not the fresh one", async ({ page }) => {
  await open(page, "/pipeline");
  await expect(page.getByRole("heading", { name: "Offer pipeline" })).toBeVisible();
  await expect(page.getByText("21d — chase")).toBeVisible();
  const fresh = page.locator("a", { hasText: "OFF-2" });
  await expect(fresh.getByText("1d")).toBeVisible();
  await expect(fresh.getByText("chase")).toHaveCount(0);
});

test("the crm copy is industry-neutral", async ({ page }) => {
  await open(page, "/pipeline");
  await expect(page.getByRole("heading", { name: "Offer pipeline" })).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
