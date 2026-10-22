import { test, expect, type Page, type Route } from "@playwright/test";

// Synthetic UI fixtures. These checks do not call a real backend or complete work.
const ORG = { id: "imp-dashboard-org", name: "Synthetic Workspace", status: "active" };
const USER = { id: "imp-dashboard-owner", name: "Owner", email: "owner@example.test", role: "owner", org_id: ORG.id };
const OFFER = {
  kind: "offer_followup", ref_id: "offer-1", title: "Follow up on offer AUD-7",
  detail: "Awaiting the customer's response.", link: "/projects/project-1",
  age_days: 5, due_date: null, dismissible: true,
};
const DEADLINE = {
  kind: "deadline", ref_id: "deadline-1", title: "Review the monthly report",
  detail: "Due this week.", link: "/", age_days: null, due_date: "2026-10-23", dismissible: false,
};
type Failure = "server" | "offline" | "timeout" | "forbidden" | null;

async function setup(page: Page, initial: Failure = null, empty = false) {
  let failure = initial;
  let reads = 0;
  let writes = 0;
  let dismissed = false;
  let failAfterDismiss: Failure = null;
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-dashboard-token"));
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    if (request.method() !== "GET") writes++;
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/dashboard/onboarding") return route.fulfill(json({ steps: [], complete: true, dismissed: true }));
    if (path === "/dashboard") return route.fulfill(json({
      as_of: "2026-10-22", approvals: null, captures: { pending: 2, low_confidence_fields: 1 },
      payables: null, receivables: null, cash: null,
    }));
    if (path === "/next-actions") {
      reads++;
      if (failure === "offline") return route.abort("connectionrefused");
      if (failure === "timeout") return route.abort("timedout");
      if (failure === "server") return route.fulfill(json({ detail: "Synthetic private server diagnostic" }, 500));
      if (failure === "forbidden") return route.fulfill(json({ detail: "Forbidden" }, 403));
      return route.fulfill(json(empty ? [] : dismissed ? [DEADLINE] : [OFFER, DEADLINE]));
    }
    if (path === "/next-actions/dismiss") {
      expect(request.method()).toBe("POST");
      expect(request.postDataJSON()).toEqual({ kind: OFFER.kind, ref_id: OFFER.ref_id });
      dismissed = true;
      failure = failAfterDismiss;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill(json([]));
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Today", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Captures to review/ })).toBeVisible();
  return { setFailure: (next: Failure) => { failure = next; }, afterDismiss: (next: Failure) => { failAfterDismiss = next; }, reads: () => reads, writes: () => writes };
}

for (const failure of ["server", "offline", "timeout"] as const) {
  test(`an unavailable next-actions ${failure} read is named and recovers with GET only`, async ({ page }) => {
    const state = await setup(page, failure);
    const error = page.getByRole("alert").filter({ hasText: "Couldn’t load next actions" });
    await expect(error).toBeVisible();
    await expect(page.getByRole("link", { name: /Captures to review/ })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("Synthetic private server diagnostic");
    const reads = state.reads();
    expect(state.writes()).toBe(0);
    state.setFailure(null);
    await error.getByRole("button", { name: "Try again", exact: true }).click();
    await expect(page.getByRole("link", { name: OFFER.title, exact: true })).toBeVisible();
    await expect(error).toHaveCount(0);
    expect(state.reads()).toBe(reads + 1);
    expect(state.writes()).toBe(0);
  });
}

for (const scenario of ["empty", "forbidden"] as const) {
  test(`a genuine ${scenario} next-actions result stays distinct from an unavailable read`, async ({ page }) => {
    const state = await setup(page, scenario === "forbidden" ? "forbidden" : null, scenario === "empty");
    await expect.poll(state.reads).toBeGreaterThan(0);
    await expect(page.getByRole("heading", { name: "Next actions", exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(state.writes()).toBe(0);
  });
}

test("a held recovery read keeps its status visible and removes the retry action", async ({ page }) => {
  const state = await setup(page, "server");
  const error = page.getByRole("alert").filter({ hasText: "Couldn’t load next actions" });
  await expect(error).toBeVisible();
  let held: Route | undefined;
  let retryReads = 0;
  await page.route("**/api/v1/next-actions", route => { retryReads++; held = route; });
  try {
    await error.getByRole("button", { name: "Try again", exact: true }).click();
    await expect.poll(() => held !== undefined).toBe(true);
    await expect(page.getByRole("region", { name: "Next actions", exact: true })).toBeVisible();
    await expect(page.getByRole("status")).toHaveText("Checking next actions…");
    await expect(page.getByRole("button", { name: "Try again", exact: true })).toHaveCount(0);
    expect(retryReads).toBe(1);
    expect(state.writes()).toBe(0);
    await held!.fulfill({ contentType: "application/json", body: JSON.stringify([OFFER, DEADLINE]) });
    held = undefined;
    await expect(page.getByRole("link", { name: OFFER.title, exact: true })).toBeVisible();
    await expect(page.getByRole("status")).toHaveCount(0);
  } finally {
    if (held) await held.abort();
  }
});

test("a failed refresh retains known tasks but pauses their writes until GET recovery", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("link", { name: OFFER.title, exact: true })).toBeVisible();
  state.afterDismiss("server");
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();
  const error = page.getByRole("alert").filter({ hasText: "Couldn’t refresh next actions" });
  await expect(error).toBeVisible();
  await expect(page.getByRole("link", { name: OFFER.title, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: DEADLINE.title, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Dismiss", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Done", exact: true })).toBeDisabled();
  expect(state.writes()).toBe(1);
  const reads = state.reads();
  state.setFailure(null);
  await error.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("link", { name: OFFER.title, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Done", exact: true })).toBeEnabled();
  expect(state.reads()).toBe(reads + 1);
  expect(state.writes()).toBe(1);
});

test("a permission refusal after refresh removes cached next actions", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("link", { name: OFFER.title, exact: true })).toBeVisible();
  state.afterDismiss("forbidden");
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Next actions", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: DEADLINE.title, exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(state.writes()).toBe(1);
});
