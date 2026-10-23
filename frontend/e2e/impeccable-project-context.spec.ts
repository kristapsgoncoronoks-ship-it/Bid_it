import { expect, test, type Page } from "@playwright/test";

// Synthetic API fixtures exercise the real routed page and shared shell. No
// financial or project mutations are allowed in these wayfinding journeys.
const PROJECT = { id: "project-context", code: "CTX-23", name: "Synthetic project", status: "active", version: 1 };
const PNL = {
  project_id: PROJECT.id, code: PROJECT.code, name: PROJECT.name, status: "active",
  revenue: "100.00", credited: "0.00", costs: "40.00", invoice_costs: "20.00",
  expense_costs: "10.00", manual_costs: "10.00", profit: "60.00", margin_pct: "60.0",
  basis: "net_eur_live", basis_version: 2, reporting_currency: "EUR", complete: true,
  issues: [], fx_trace: [], legacy_figures: {}, adjustments_complete: true,
  adjustments: {}, pnl_frozen_at: null, estimated_revenue: null,
};
const errors = new WeakMap<Page, string[]>();
const writes = new WeakMap<Page, string[]>();
const expectedWrites = new WeakMap<Page, string[]>();
type WriteRequest = { method: string; path: string; body: Record<string, unknown> };

test.beforeEach(async ({ page }) => {
  errors.set(page, []);
  writes.set(page, []);
  expectedWrites.set(page, []);
  page.on("pageerror", (error) => errors.get(page)!.push(error.message));
});

test.afterEach(async ({ page }) => {
  expect(errors.get(page), "navigation must not conceal a page render failure").toEqual([]);
  expect(writes.get(page), "only the explicitly submitted original intention may be written").toEqual(expectedWrites.get(page));
});

async function fixture(page: Page, opts: {
  denied?: boolean; suspended?: boolean; name?: string; pnlStatus?: number; manage?: boolean;
  onWrite?: (request: WriteRequest) => Promise<{ status: number; body: unknown }>;
} = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-project-context-token"));
  const org = { id: "org-context", name: "Synthetic workspace", status: opts.suspended ? "suspended" : "active" };
  const user = { id: "user-context", email: "reader@example.test", name: "Reader", role: "user", org_id: org.id };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();
    if (method !== "GET") writes.get(page)!.push(`${method} ${path}`);
    if (method !== "GET" && opts.onWrite) {
      const reply = await opts.onWrite({ method, path, body: route.request().postDataJSON() });
      return route.fulfill({ status: reply.status, contentType: "application/json", body: JSON.stringify(reply.body) });
    }
    let body: unknown = [];
    let status = 200;
    if (path === "/auth/me") body = { user, organization: org, permissions: opts.denied ? ["report.read"] : opts.manage ? ["invoice.read", "settings.manage"] : ["invoice.read"] };
    else if (path === "/auth/organizations") body = [{ org_id: org.id, name: org.name, role: user.role, current: true }];
    else if (path === `/masters/projects/${PROJECT.id}/pnl`) {
      status = opts.denied ? 403 : opts.pnlStatus ?? 200;
      body = status === 200 ? { ...PNL, name: opts.name ?? PROJECT.name } : { detail: "Synthetic project unavailable" };
    } else if (path === "/masters/projects") body = [{ ...PROJECT, name: opts.name ?? PROJECT.name }];
    else if (path === "/masters/departments") body = [{ id: "department-context", code: "DEPT-1", name: "Synthetic department", status: "active", version: 1 }];
    else if (path === "/masters/projects-pnl-summary") body = [PNL];
    else if (path === `/masters/projects/${PROJECT.id}/invoicing-plan`) body = {
      project_id: PROJECT.id, rows: [], basis_version: 2, reporting_currency: "EUR", complete: true,
      issues: [], contracted_total: "0.00", issued_total: "0.00", remaining: "0.00",
    };
    else if (path === "/templates") body = { own: [], platform: [] };
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
}

async function expectProjectContext(page: Page) {
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(crumbs.getByRole("link", { name: "Cost objects", exact: true })).toHaveAttribute("href", "/cost-objects?kind=projects");
  await expect(crumbs.locator('[aria-current="page"]')).toHaveText("Project");
}

test("direct project entry retains its permitted breadcrumb, active parent and expanded section", async ({ page }) => {
  await fixture(page);
  await page.goto(`/projects/${PROJECT.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "CTX-23 · Synthetic project", exact: true })).toBeVisible();
  await expectProjectContext(page);
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("button", { name: "Settings & administration", exact: true })).toHaveAttribute("aria-expanded", "true");
  const parent = nav.getByRole("link", { name: "Cost objects", exact: true });
  await expect(parent).toBeVisible();
  await expect(parent).toHaveAttribute("aria-current", "page");
  await expect(nav.locator('a[aria-current="page"]')).toHaveCount(1);
});

test("All projects returns to the Projects tab instead of Departments", async ({ page }) => {
  await fixture(page);
  await page.goto(`/projects/${PROJECT.id}`);
  await page.getByRole("link", { name: "All projects", exact: true }).click();
  await expect(page).toHaveURL(/\/cost-objects\?kind=projects$/);
  await expect(page.getByRole("tab", { name: "Projects", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("link", { name: PROJECT.code, exact: true })).toBeVisible();
  await expect(page.getByText("Synthetic department", { exact: true })).toHaveCount(0);
});

test("list navigation and browser Back/Forward preserve the selected Projects tab", async ({ page }) => {
  await fixture(page);
  await page.goto("/cost-objects");
  await expect(page.getByRole("tab", { name: "Departments", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await expect(page).toHaveURL(/\/cost-objects\?kind=projects$/);
  await page.getByRole("link", { name: PROJECT.code, exact: true }).click();
  await expectProjectContext(page);
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Projects", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goForward();
  await expectProjectContext(page);
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Cost objects", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Projects", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("mobile project navigation exposes its current parent and closes after navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto(`/projects/${PROJECT.id}`);
  await expectProjectContext(page);
  await page.getByRole("button", { name: "Open navigation menu", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Menu", exact: true });
  const parent = drawer.getByRole("link", { name: "Cost objects", exact: true });
  await expect(parent).toBeVisible();
  await expect(parent).toHaveAttribute("aria-current", "page");
  await parent.click();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "Cost objects", exact: true })).toBeVisible();
});

for (const [width, fontSize] of [[320, 16], [390, 32]]) {
  test(`long project title keeps the return action reachable at ${width}px and ${fontSize}px text`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await fixture(page, { name: "SyntheticUnbrokenProjectName".repeat(8) });
    await page.goto(`/projects/${PROJECT.id}`);
    await page.evaluate((size) => { document.documentElement.style.fontSize = `${size}px`; }, fontSize);
    const title = page.getByRole("heading", { level: 1 });
    await expect(title).toContainText("SyntheticUnbrokenProjectName");
    const action = page.getByRole("link", { name: "All projects", exact: true });
    const bounds = await action.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await action.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(action).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("tab", { name: "Projects", exact: true })).toHaveAttribute("aria-selected", "true");
  });
}

test("denied navigation does not gain a linked parent from route metadata", async ({ page }) => {
  await fixture(page, { denied: true });
  await page.goto(`/projects/${PROJECT.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Project", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Cost objects", exact: true, includeHidden: true })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Cost objects", exact: true })).toHaveCount(0);
  await expect(page.getByText("Synthetic project", { exact: true })).toHaveCount(0);
});

test("suspension does not expose a hidden project parent or project content", async ({ page }) => {
  await fixture(page, { suspended: true });
  await page.goto(`/projects/${PROJECT.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "This workspace is suspended", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Cost objects", exact: true })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Cost objects", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "CTX-23 · Synthetic project", exact: true })).toHaveCount(0);
});

test("a failed project read keeps truthful generic wayfinding and a usable return", async ({ page }) => {
  await fixture(page, { pnlStatus: 404 });
  await page.goto(`/projects/${PROJECT.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Project", exact: true })).toBeVisible();
  await expectProjectContext(page);
  await expect(page.getByRole("alert").filter({ hasText: "Couldn’t load the project" })).toBeVisible();
  await page.getByRole("link", { name: "All projects", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Projects", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("URL-selected kinds follow history and reject unsupported values without changing other parameters", async ({ page }) => {
  await fixture(page);
  await page.goto("/cost-objects?kind=unsupported&source=bookmark");
  await expect(page.getByRole("tab", { name: "Departments", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("kind")).toBe("projects");
  expect(new URL(page.url()).searchParams.get("source")).toBe("bookmark");
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Departments", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("central navigation matching uses explicit detail routes and segment boundaries", async ({ page }) => {
  await page.goto("/login");
  const matches = await page.evaluate(async () => {
    const path = "/src/lib/nav.ts";
    const { matchNavItem } = await import(/* @vite-ignore */ path);
    return ["/projects/project-context", "/projects/project-context/extra", "/projects-extra/project-context", "/invoices/invoice-context", "/invoices-archive"]
      .map((pathname) => matchNavItem(pathname)?.to ?? null);
  });
  expect(matches).toEqual(["/cost-objects", null, null, "/invoices", null]);
});

test("browser Back cannot retarget an open project-creation draft as a department", async ({ page }) => {
  await fixture(page, { manage: true });
  await page.goto("/cost-objects");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "New project", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New project", exact: true });
  await dialog.getByRole("textbox", { name: "Code", exact: true }).fill("PROJECT-INTENTION");
  await dialog.getByRole("textbox", { name: "Name", exact: true }).fill("Typed as a project");
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Departments", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "New department", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("textbox", { name: "Code", exact: true })).toHaveValue("");
  await expect(page.getByRole("dialog").getByRole("textbox", { name: "Name", exact: true })).toHaveValue("");
});

test("browser Back closes a project rename instead of carrying it into another master list", async ({ page }) => {
  await fixture(page, { manage: true });
  await page.goto("/cost-objects");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("textbox").fill("Project rename intention");
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Departments", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

for (const status of [201, 422]) {
  test(`a held project create returning ${status} keeps its original request and cannot reset a newer department draft`, async ({ page }) => {
    const requests: WriteRequest[] = [];
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    expectedWrites.set(page, ["POST /masters/projects"]);
    await fixture(page, { manage: true, onWrite: async (request) => {
      requests.push(request);
      await held;
      return { status, body: status === 201 ? PROJECT : { detail: "Original project create was refused" } };
    } });
    await page.goto("/cost-objects");
    await page.getByRole("tab", { name: "Projects", exact: true }).click();
    await page.getByRole("button", { name: "New project", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox", { name: "Code", exact: true }).fill("ORIGINAL-PROJECT");
    await dialog.getByRole("textbox", { name: "Name", exact: true }).fill("Original project intention");
    await dialog.getByRole("button", { name: "Create project", exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests).toEqual([{ method: "POST", path: "/masters/projects", body: { code: "ORIGINAL-PROJECT", name: "Original project intention" } }]);
    await page.goBack();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "New department", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "New department", exact: true });
    await dialog.getByRole("textbox", { name: "Code", exact: true }).fill("NEW-DEPARTMENT");
    await dialog.getByRole("textbox", { name: "Name", exact: true }).fill("New department intention");
    const completed = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/masters/projects"));
    release();
    await completed;
    await expect(dialog.getByRole("button", { name: "Create department", exact: true })).toBeEnabled();
    await expect(dialog.getByRole("textbox", { name: "Code", exact: true })).toHaveValue("NEW-DEPARTMENT");
    await expect(dialog.getByRole("textbox", { name: "Name", exact: true })).toHaveValue("New department intention");
    await expect(page.getByText("Original project create was refused", { exact: true })).toHaveCount(0);
    expect(requests).toHaveLength(1);
  });
}

for (const status of [200, 409]) {
  test(`a held project rename returning ${status} cannot close or rewrite a newer department rename`, async ({ page }) => {
    const requests: WriteRequest[] = [];
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    expectedWrites.set(page, [`PATCH /masters/projects/${PROJECT.id}`]);
    await fixture(page, { manage: true, onWrite: async (request) => {
      requests.push(request);
      await held;
      return { status, body: status === 200 ? PROJECT : { detail: "Original project rename was refused" } };
    } });
    await page.goto("/cost-objects");
    await page.getByRole("tab", { name: "Projects", exact: true }).click();
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await page.getByRole("dialog").getByRole("textbox").fill("Original project rename");
    await page.getByRole("dialog").getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests).toEqual([{ method: "PATCH", path: `/masters/projects/${PROJECT.id}`, body: { name: "Original project rename", version: 1 } }]);
    await page.goBack();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Rename department", exact: true });
    await dialog.getByRole("textbox").fill("New department rename");
    const completed = page.waitForResponse((response) => response.request().method() === "PATCH" && new URL(response.url()).pathname.endsWith(`/masters/projects/${PROJECT.id}`));
    release();
    await completed;
    await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    await expect(dialog.getByRole("textbox")).toHaveValue("New department rename");
    await expect(page.getByText("Original project rename was refused", { exact: true })).toHaveCount(0);
    expect(requests).toHaveLength(1);
  });
}

test("a held project transition cannot surface its old refusal in a new department draft", async ({ page }) => {
  const requests: WriteRequest[] = [];
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  expectedWrites.set(page, [`PATCH /masters/projects/${PROJECT.id}`]);
  await fixture(page, { manage: true, onWrite: async (request) => {
    requests.push(request);
    await held;
    return { status: 409, body: { detail: "Original project transition was refused" } };
  } });
  await page.goto("/cost-objects");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests).toEqual([{ method: "PATCH", path: `/masters/projects/${PROJECT.id}`, body: { status: "closed", version: 1 } }]);
  await page.goBack();
  await page.getByRole("button", { name: "New department", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New department", exact: true });
  await dialog.getByRole("textbox", { name: "Code", exact: true }).fill("DEPARTMENT-AFTER-TRANSITION");
  const completed = page.waitForResponse((response) => response.request().method() === "PATCH" && new URL(response.url()).pathname.endsWith(`/masters/projects/${PROJECT.id}`));
  const refreshed = page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname.endsWith("/masters/departments"));
  release();
  await Promise.all([completed, refreshed]);
  await expect(dialog.getByRole("textbox", { name: "Code", exact: true })).toHaveValue("DEPARTMENT-AFTER-TRANSITION");
  await expect(page.getByText("Original project transition was refused", { exact: true })).toHaveCount(0);
  expect(requests).toHaveLength(1);
});
