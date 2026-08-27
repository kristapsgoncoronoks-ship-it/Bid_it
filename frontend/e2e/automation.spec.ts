import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Automation rules (WO-J) over the LIVE app shell, API mocked via `page.route`.
 *
 * What earns its place here:
 *  - the page renders rules with status/version badges and the run log;
 *  - the builder composes the JSON-Logic condition and ordered actions and
 *    POSTs exactly the closed-vocabulary payload the backend validates;
 *  - publish posts to /publish; dry-run renders outcomes without mutating;
 *  - copy is industry-neutral (owner guard list).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

const RULES = [
  {
    id: "rule-1",
    name: "Chase quiet offers",
    trigger: "offer.sent_stale",
    condition: { ">": [{ var: "days_quiet" }, 7] },
    actions: [{ kind: "notify_owner_email", subject: "Quiet offer", body: "Chase it." }],
    status: "published",
    fire_policy: "once_per_record",
    cooldown_hours: null,
    published_version: 2,
    created_at: "2026-08-20T10:00:00+00:00",
  },
  {
    id: "rule-2",
    name: "Note dormant customers",
    trigger: "customer.dormant",
    condition: { ">": [{ var: "days_since_last_invoice" }, 180] },
    actions: [{ kind: "create_customer_note", body: "Gone quiet." }],
    status: "draft",
    fire_policy: "every_time",
    cooldown_hours: null,
    published_version: null,
    created_at: "2026-08-21T10:00:00+00:00",
  },
];

const RUNS = [
  {
    id: "run-1",
    rule_id: "rule-1",
    version: 2,
    ref_id: "offer-abcdef123456",
    status: "ok",
    detail: null,
    at: "2026-08-24T06:00:00+00:00",
  },
  {
    id: "run-2",
    rule_id: "rule-1",
    version: 2,
    ref_id: "offer-fedcba654321",
    status: "throttled",
    detail: { reason: "sweep cap" },
    at: "2026-08-24T06:00:01+00:00",
  },
];

async function open(
  page: Page,
  opts: {
    onCreate?: (b: Record<string, unknown>) => void;
    onPublish?: () => void;
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

    if (p === "/automation/meta")
      return route.fulfill(
        json({
          triggers: {
            "offer.sent_stale": "offer",
            "issued.overdue": "issued_invoice",
            "project.accepted": "project",
            "assignment.done_all": "project",
            "customer.dormant": "customer",
          },
          actions: [
            "notify_owner_email",
            "notify_customer_email",
            "create_customer_note",
            "emit_webhook",
          ],
        }),
      );
    if (p === "/automation/rules") {
      if (method === "POST") {
        opts.onCreate?.(route.request().postDataJSON());
        return route.fulfill(
          json({ ...RULES[1], id: "rule-new", name: "Overdue nudge" }, 201),
        );
      }
      return route.fulfill(json(RULES));
    }
    if (p === "/automation/rules/rule-2/publish" && method === "POST") {
      opts.onPublish?.();
      return route.fulfill(json({ ...RULES[1], status: "published", published_version: 1 }));
    }
    if (p === "/automation/rules/rule-1/dry-run" && method === "POST")
      return route.fulfill(
        json({
          outcomes: [
            { ref_id: "offer-abcdef123456", would_fire: true, ctx: { days_quiet: 12 } },
          ],
        }),
      );
    if (p === "/automation/runs") return route.fulfill(json(RUNS));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/automation");
}

test("rules render with status, version and the run log", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("heading", { name: "Automation" })).toBeVisible();

  await expect(page.getByRole("cell", { name: "Chase quiet offers" }).first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "Offer sent, gone quiet" })).toBeVisible();
  await expect(page.getByText("v2").first()).toBeVisible();
  await expect(page.getByText("published").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "Note dormant customers" })).toBeVisible();
  await expect(page.getByText("draft").first()).toBeVisible();

  // The run log: one ok, one throttled — the cap is visible, never silent.
  await expect(page.getByText("ok")).toBeVisible();
  await expect(page.getByText("throttled")).toBeVisible();
});

test("the builder composes the condition and actions payload", async ({ page }) => {
  const created: Record<string, unknown>[] = [];
  await open(page, { onCreate: (b) => created.push(b) });

  await page.getByRole("button", { name: "New rule" }).click();
  await page.getByPlaceholder("Chase quiet offers").fill("Overdue nudge");
  await page.getByLabel("When").selectOption("issued.overdue");

  await page.getByRole("button", { name: "Add condition" }).click();
  await page.getByLabel("Condition 1 field").selectOption("days_overdue");
  await page.getByLabel("Condition 1 operator").selectOption(">=");
  await page.getByLabel("Condition 1 value").fill("14");

  await page.getByLabel("Action 1 kind").selectOption("notify_customer_email");
  await page.getByLabel("Action 1 subject").fill("Invoice {{invoice_number}} is overdue");
  await page.getByLabel("Action 1 body").fill("Outstanding: {{outstanding}} EUR.");

  await page.getByRole("button", { name: "Create draft" }).click();
  await expect.poll(() => created.length).toBe(1);
  expect(created[0]).toEqual({
    name: "Overdue nudge",
    trigger: "issued.overdue",
    condition: { ">=": [{ var: "days_overdue" }, 14] },
    actions: [
      {
        kind: "notify_customer_email",
        subject: "Invoice {{invoice_number}} is overdue",
        body: "Outstanding: {{outstanding}} EUR.",
      },
    ],
    fire_policy: "once_per_record",
    cooldown_hours: null,
  });
});

test("publish posts and dry run shows outcomes without mutating", async ({ page }) => {
  let published = 0;
  await open(page, { onPublish: () => (published += 1) });

  await page.getByRole("button", { name: "Publish" }).click();
  await expect.poll(() => published).toBe(1);

  await page.getByRole("button", { name: "Dry run" }).first().click();
  await expect(page.getByRole("heading", { name: /Dry run — Chase quiet offers/ })).toBeVisible();
  await expect(page.getByText(/would_fire/)).toBeVisible();
});

test("the automation copy is industry-neutral", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("heading", { name: "Automation" })).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});

test("WO-W: the outward action is offered, and explains where it goes", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "New rule" }).click();

  const kind = page.getByLabel("Action 1 kind");
  await kind.selectOption("emit_webhook");

  // Labelled by what it does for the operator, not by the mechanism.
  await expect(kind).toHaveValue("emit_webhook");
  // The two things a person needs to know before pressing publish: WHERE it
  // goes, and what happens when nothing is listening.
  await expect(page.getByText("automation.fired")).toBeVisible();
  await expect(page.getByText("nothing is sent", { exact: false })).toBeVisible();
  // A webhook carries no subject line — the payload is built from the record.
  await expect(page.getByLabel("Action 1 subject")).toHaveCount(0);
});
