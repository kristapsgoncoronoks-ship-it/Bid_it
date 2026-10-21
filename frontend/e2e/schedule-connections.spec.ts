import { test, expect, type Page, type Route } from "@playwright/test";

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const ROW = {
  id: "asg-1", project_id: "project-1", project_code: "JOB-1", project_name: "Site visit",
  assignee_user_id: "user-1", assignee_email: "crew@example.test",
  starts_at: "2026-10-20T09:00:00Z", ends_at: "2026-10-20T17:00:00Z",
  all_day: false, status: "planned", revision: 1, time_zone: "Europe/Riga",
  note: null, notify_assignee: true, reminders_enabled: true, client_notice_enabled: null,
};
const linked = { id: "connection-1", status: "connected", calendar_name: "Bid_it work",
  last_synced_at: null as string | null, error: null, pending: 1, failed: 0 };
interface State {
  configured: boolean; connection: typeof linked | null; readFailed: boolean;
  posts: string[]; reads: number; deletes: number; retryFailed: boolean;
  smtp: boolean; emailStatus: string; emailRetryFailed: boolean; emailPosts: number;
  authorizationUrl: string; hold?: () => Promise<void>;
  emailHold?: () => Promise<void>; emailReads: number; assignmentRevision: number;
}
async function setup(page: Page) {
  const state: State = { configured: false, connection: null, readFailed: false,
    posts: [], reads: 0, deletes: 0, retryFailed: false, smtp: false,
    emailStatus: "blocked", emailRetryFailed: false, emailPosts: 0,
    emailReads: 0, assignmentRevision: 1,
    authorizationUrl: "https://accounts.google.com/o/oauth2/v2/auth?state=synthetic" };
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  await page.clock.setFixedTime(new Date("2026-10-21T09:00:00Z"));
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/v1/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();
    if (path === "/auth/me") return route.fulfill(json({ user: { id: "user-1", email: "crew@example.test", name: "Test User", role: "owner", org_id: ORG.id }, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/schedule/members") return route.fulfill(json([{ user_id: "user-1", email: "crew@example.test", name: "Test User" }]));
    if (path === "/masters/projects") return route.fulfill(json([{ id: "project-1", code: "JOB-1", name: "Site visit" }]));
    if (path === "/schedule/assignments") return route.fulfill(json([{ ...ROW, revision: state.assignmentRevision }]));
    if (path === "/dashboard") return route.fulfill(json({ as_of: "2026-10-21", approvals: null, captures: null, payables: null, receivables: null, cash: null }));
    if (path === "/schedule/calendar-connections") {
      state.reads++;
      if (state.readFailed) return route.fulfill(json({ detail: "Calendar status temporarily unavailable" }, 503));
      return route.fulfill(json({ providers: ["google", "outlook"].map((provider) => ({
        provider, configured: state.configured, reason: state.configured ? null : "An administrator must configure calendar access.",
        connection: provider === "google" ? state.connection : null,
      })) }));
    }
    if (path === "/schedule/calendar-connections/google" && method === "DELETE") {
      state.deletes++; state.connection = null; return route.fulfill({ status: 204 });
    }
    if (path.startsWith("/schedule/calendar-connections/") && method === "POST") {
      state.posts.push(path); await state.hold?.();
      if (path.endsWith("/connect")) return route.fulfill(json({ authorization_url: state.authorizationUrl }));
      return route.fulfill(state.retryFailed ? json({ detail: "Unable to confirm sync request" }, 503) : json({ status: "queued" }, 202));
    }
    if (path.endsWith("/notifications")) { state.emailReads++; return route.fulfill(json({ smtp_configured: state.smtp, items: [{
      id: "notice-1", kind: "assignment", status: state.emailStatus, error: null,
      sent_at: state.emailStatus === "sent" ? "2026-10-21T09:00:00Z" : null,
      created_at: "2026-10-21T08:59:00Z", can_retry: ["blocked", "failed"].includes(state.emailStatus),
    }] })); }
    if (path.endsWith("/notifications/notice-1/retry")) {
      state.emailPosts++;
      const requestedRevision = state.assignmentRevision;
      await state.emailHold?.();
      if (state.emailRetryFailed) return route.fulfill(json({ detail: "Delivery request outcome unavailable" }, 503));
      if (state.assignmentRevision === requestedRevision) state.emailStatus = "queued";
      return route.fulfill(json({ status: "queued" }, 202));
    }
    return route.fulfill(json({ items: [], total: 0 }));
  });
  return state;
}
async function showConnections(page: Page) {
  await page.goto("/schedule");
  await page.getByRole("button", { name: "Calendar connections", exact: true }).click();
  return page.getByRole("region", { name: "Calendar connections", exact: true });
}

test("unconfigured connections explain setup and cannot start authorization", async ({ page }) => {
  const state = await setup(page); const panel = await showConnections(page);
  await expect(panel.getByRole("button", { name: "Connect Google Calendar", exact: true })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Connect Outlook Calendar", exact: true })).toBeDisabled();
  await expect(panel.getByText("An administrator must configure calendar access.")).toHaveCount(2);
  expect(state.posts).toHaveLength(0);
});

test("calendar status failure is recoverable by GET without starting work", async ({ page }) => {
  const state = await setup(page); state.readFailed = true; const panel = await showConnections(page);
  await expect(panel.getByText("Couldn’t load calendar connections", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(panel.getByRole("button", { name: "Connect Google Calendar", exact: true })).toHaveCount(0);
  state.readFailed = false;
  await panel.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(panel.getByText("Not connected", { exact: true })).toHaveCount(2);
  expect(state.posts).toHaveLength(0);
});

test("provider acknowledgement follows a fresh read and disconnect explains retained events", async ({ page }) => {
  const state = await setup(page); state.configured = true; state.connection = { ...linked };
  const panel = await showConnections(page);
  await expect(panel.getByText("1 pending · 0 need attention", { exact: true })).toBeVisible();
  await expect(panel.getByText("No provider acknowledgement yet", { exact: true })).toBeVisible();
  state.connection = { ...linked, pending: 0, last_synced_at: "2026-10-21T09:00:00Z" };
  await panel.getByRole("button", { name: "Refresh calendar status", exact: true }).click();
  await expect(panel.getByText(/Last provider acknowledgement:/)).toBeVisible();
  expect(state.posts).toHaveLength(0);
  await panel.getByRole("button", { name: "Disconnect Google Calendar", exact: true }).click();
  await expect(panel.getByText(/Previously exported events remain in your calendar/)).toBeVisible();
  expect(state.deletes).toBe(1);
});

test("an uncertain sync retry blocks every calendar write until a successful GET recovery", async ({ page }) => {
  const state = await setup(page); state.configured = true; state.connection = { ...linked, pending: 0 }; state.retryFailed = true;
  const panel = await showConnections(page);
  const retry = panel.getByRole("button", { name: "Check & retry sync", exact: true });
  await retry.click();
  await expect(panel.getByText("Unable to confirm sync request", { exact: true })).toBeVisible();
  await expect(panel.getByText("Refresh calendar status before requesting another change.", { exact: true })).toBeVisible();
  await expect(retry).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Disconnect Google Calendar", exact: true })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Connect Outlook Calendar", exact: true })).toBeDisabled();
  expect(state.posts).toEqual(["/schedule/calendar-connections/google/retry"]); expect(state.deletes).toBe(0);
  state.readFailed = true;
  await panel.getByRole("button", { name: "Refresh calendar status", exact: true }).click();
  await expect(panel.getByText("Couldn’t refresh calendar status. Try again before requesting another change.", { exact: true })).toBeVisible();
  await expect(retry).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Disconnect Google Calendar", exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Connect Outlook Calendar", exact: true })).toHaveCount(0);
  await expect(panel.getByText("Refresh calendar status before requesting another change.", { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(1); expect(state.deletes).toBe(0);
  state.readFailed = false; const readsBeforeRecovery = state.reads;
  await panel.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(retry).toBeEnabled();
  await expect(panel.getByRole("button", { name: "Disconnect Google Calendar", exact: true })).toBeEnabled();
  await expect(panel.getByRole("button", { name: "Connect Outlook Calendar", exact: true })).toBeEnabled();
  expect(state.reads).toBeGreaterThan(readsBeforeRecovery); expect(state.posts).toHaveLength(1);
  state.retryFailed = false; await retry.click();
  await expect(retry).toBeEnabled();
  expect(state.posts).toEqual(["/schedule/calendar-connections/google/retry", "/schedule/calendar-connections/google/retry"]);
  expect(state.deletes).toBe(0);
});

test("connect stays pending and rejects an unexpected authorization host", async ({ page }) => {
  const state = await setup(page); state.configured = true; state.authorizationUrl = "https://example.test/steal";
  let release: () => void = () => {}; const gate = new Promise<void>((resolve) => { release = resolve; }); state.hold = () => gate;
  const panel = await showConnections(page); const connect = panel.getByRole("button", { name: "Connect Google Calendar", exact: true });
  await connect.click(); await expect.poll(() => state.posts.length).toBe(1);
  await expect(connect).toBeDisabled(); release();
  await expect(panel.getByText(/authorization address was invalid/)).toBeVisible();
  await expect(page).toHaveURL(/\/schedule$/); expect(state.posts).toHaveLength(1);
});

test("a delayed connect response cannot redirect after leaving planning in the same workspace", async ({ page }) => {
  const state = await setup(page); state.configured = true;
  let release: () => void = () => {}; const gate = new Promise<void>((resolve) => { release = resolve; }); state.hold = () => gate;
  let providerVisits = 0;
  await page.route("https://accounts.google.com/**", async (route) => { providerVisits++; await route.fulfill({ contentType: "text/html", body: "Unexpected late authorization navigation" }); });
  const panel = await showConnections(page);
  await panel.getByRole("button", { name: "Connect Google Calendar", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  const originalDocument = await page.evaluate(() => { const id = crypto.randomUUID(); (window as unknown as { planningDocument: string }).planningDocument = id; return id; });
  await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Today", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Calendar connections", exact: true })).toHaveCount(0);
  const response = page.waitForResponse((r) => r.url().endsWith("/schedule/calendar-connections/google/connect"));
  release(); await response;
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page).toHaveURL(/\/$/);
  expect(providerVisits).toBe(0);
  expect(await page.evaluate(() => (window as unknown as { planningDocument: string }).planningDocument)).toBe(originalDocument);
});

for (const outcome of ["accepted", "failed"] as const) {
  test(`a delayed email retry (${outcome}) cannot update the next assignment revision`, async ({ page }) => {
    const state = await setup(page); state.smtp = true; state.emailStatus = "failed"; state.emailRetryFailed = outcome === "failed";
    let release: () => void = () => {}; const gate = new Promise<void>((resolve) => { release = resolve; }); state.emailHold = () => gate;
    await page.goto("/schedule"); await page.getByRole("button", { name: "Email details", exact: true }).click();
    await page.getByRole("region", { name: "Work-order email details", exact: true }).getByRole("button", { name: "Retry email", exact: true }).click();
    await expect.poll(() => state.emailPosts).toBe(1);
    state.assignmentRevision = 2; state.emailStatus = "unknown";
    await page.getByRole("button", { name: "Refresh plan", exact: true }).click();
    await expect(page.getByRole("button", { name: "Email details", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Work-order email details", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Email details", exact: true }).click();
    const details = page.getByRole("region", { name: "Work-order email details", exact: true });
    await expect(details.getByText("Delivery outcome unknown", { exact: true })).toBeVisible();
    const currentRevisionReads = state.emailReads;
    const response = page.waitForResponse((r) => r.url().endsWith("/notifications/notice-1/retry"));
    release(); await response;
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(details.getByText("Delivery outcome unknown", { exact: true })).toBeVisible();
    await expect(details.getByText("Delivery request outcome unavailable", { exact: true })).toHaveCount(0);
    await expect(details.getByText("Check the current status before requesting another delivery attempt.", { exact: true })).toHaveCount(0);
    expect(state.emailReads).toBe(currentRevisionReads); expect(state.emailPosts).toBe(1);
  });
}

test("unknown email retry outcome requires readback and is never shown as sent", async ({ page }) => {
  const state = await setup(page); state.smtp = true; state.emailStatus = "failed"; state.emailRetryFailed = true;
  await page.goto("/schedule"); await page.getByRole("button", { name: "Email details", exact: true }).click();
  const details = page.getByRole("region", { name: "Work-order email details", exact: true });
  await details.getByRole("button", { name: "Retry email", exact: true }).click();
  await expect(details.getByText("Check the current status before requesting another delivery attempt.")).toBeVisible();
  await expect(details.getByRole("button", { name: "Retry email", exact: true })).toBeDisabled();
  expect(state.emailPosts).toBe(1); state.emailStatus = "sent";
  await details.getByRole("button", { name: "Check email status", exact: true }).click();
  await expect(details.getByText("Accepted by mail server", { exact: true })).toBeVisible();
  await expect(details.getByRole("button", { name: "Retry email", exact: true })).toHaveCount(0);
  expect(state.emailPosts).toBe(1);
});

test("email blocked and unknown outcomes expose no unsafe resend", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/schedule"); await page.getByRole("button", { name: "Email details", exact: true }).click();
  const details = page.getByRole("region", { name: "Work-order email details", exact: true });
  await expect(details.getByText("Delivery not configured", { exact: true })).toBeVisible();
  await expect(details.getByRole("button", { name: "Retry email", exact: true })).toBeDisabled();
  state.smtp = true; state.emailStatus = "unknown";
  await details.getByRole("button", { name: "Check email status", exact: true }).click();
  await expect(details.getByText("Delivery outcome unknown", { exact: true })).toBeVisible();
  await expect(details.getByRole("button", { name: "Retry email", exact: true })).toHaveCount(0);
  expect(state.emailPosts).toBe(0);
});
