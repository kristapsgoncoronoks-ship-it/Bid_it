import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const csv = Buffer.from("Date,Description,Amount,Currency\n2026-10-01,Synthetic hotel,-12.34,USD\n");

function uuid(value: string): string {
  if (!/^[0-9a-f-]{36}$/i.test(value)) throw new Error("Expected a synthetic UUID");
  return value;
}

function sql(statement: string): string {
  return execFileSync("psql", ["-X", "-v", "ON_ERROR_STOP=1", "-At", "-c", statement],
    { encoding: "utf8", env: process.env }).trim();
}

async function owner(page: Page) {
  await expect.poll(async () => {
    try { return (await page.request.get("http://127.0.0.1:8000/health")).status(); }
    catch { return 0; }
  }, { timeout: 30_000 }).toBe(200);
  const email = "r010-live-" + randomUUID() + "@example.com";
  const password = "synthetic-test-password";
  const registration = await page.request.post("/api/v1/auth/register", {
    data: { organization_name: "Synthetic R010 browser", name: "Owner", email, password },
  });
  expect(registration.status()).toBe(201);
  const identity = await registration.json();
  const headers = { Authorization: "Bearer " + identity.token.access_token };
  const active = await page.request.put("/api/v1/modules/expenses", { headers, data: { enabled: true } });
  expect(active.status()).toBe(200);
  await page.goto("/login?next=/expenses");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toBeVisible();
  return { orgId: uuid(identity.organization.id), userId: uuid(identity.user.id), headers };
}

async function importFile(page: Page) {
  const response = page.waitForResponse((r) => r.url().endsWith("/api/v1/expenses/import/bank-statement") && r.request().method() === "POST");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import bank statement", exact: true }).click();
  await (await chooser).setFiles({ name: "synthetic.csv", mimeType: "text/csv", buffer: csv });
  const result = await response;
  expect(result.status()).toBe(200);
  return await result.json();
}

test("real import, lost-response report recovery, replay and committed PG readback", async ({ page }) => {
  const { orgId, headers } = await owner(page);
  await page.getByLabel("Statement account reference", { exact: true }).fill("operating-card");
  await page.getByLabel("Currency if missing from the statement", { exact: true }).fill("USD");
  const imported = await importFile(page);
  expect(imported.imported).toBe(1);
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
  await page.getByRole("checkbox", { name: "Select Synthetic hotel" }).check();
  await page.getByLabel("Report title", { exact: true }).fill("Synthetic recovered report");
  let committedReport = "";
  let originalKey = "";
  let originalPayload: unknown;
  await page.route("**/api/v1/expenses", async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    originalKey = route.request().headers()["idempotency-key"];
    originalPayload = route.request().postDataJSON();
    const actual = await route.fetch({ maxRetries: 0 });
    expect(actual.status()).toBe(201);
    committedReport = uuid((await actual.json()).id);
    await route.abort("failed"); // Actual server committed; only its response is lost.
  });
  await page.getByRole("button", { name: /Create report from 1/ }).click();
  await expect(page.getByText(/Report saved: Synthetic recovered report/)).toBeVisible();
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toBeVisible();
  expect(committedReport).not.toBe("");
  await page.unroute("**/api/v1/expenses");
  const replay = await page.request.post("/api/v1/expenses", {
    headers: { ...headers, "Idempotency-Key": originalKey }, data: originalPayload,
  });
  expect(replay.status()).toBe(201);
  expect((await replay.json()).id).toBe(committedReport);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await expect(page.getByText("Synthetic recovered report", { exact: true })).toBeVisible();
  await page.getByLabel("Statement account reference", { exact: true }).fill("operating-card");
  await page.getByLabel("Currency if missing from the statement", { exact: true }).fill("USD");
  const again = await importFile(page);
  expect(again.batch_id).toBe(imported.batch_id);
  expect(again.imported).toBe(0);
  expect(again.assigned).toBe(1);
  for (let i = 0; i < 10; i++) {
    const readback = await page.request.get("/api/v1/expenses/" + committedReport, { headers });
    expect(readback.status()).toBe(200);
    expect((await readback.json()).total).toBe("12.34");
  }
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("1");
  expect(sql("SELECT count(*) FROM expense_transactions WHERE org_id='" + orgId + "'")).toBe("1");
  expect(sql("SELECT currency || '|' || total::text FROM expense_reports WHERE id='" + committedReport + "' AND org_id='" + orgId + "'")).toBe("USD|12.34");
  expect(sql("SELECT currency || '|' || original_amount::text FROM expense_items WHERE report_id='" + committedReport + "' AND org_id='" + orgId + "'")).toBe("USD|12.34");
  expect(sql("SELECT count(*) FROM audit_events WHERE org_id='" + orgId + "' AND action='expense.report_created'")).toBe("1");
  console.log(JSON.stringify({ kind: "R010 unmocked completion plus response-loss injection", csv_bytes: csv.length, source_rows: 1, report_id: committedReport }));
});

test("real recoverable removal and nested-query failure remain distinct", async ({ page }) => {
  const { headers } = await owner(page);
  await page.getByLabel("Statement account reference", { exact: true }).fill("operating-card");
  await page.getByLabel("Currency if missing from the statement", { exact: true }).fill("USD");
  const imported = await importFile(page);
  const id = uuid(imported.transactions[0].id);
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
  await page.getByRole("button", { name: "remove", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Remove", exact: true }).click();
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toBeVisible();
  expect(sql("SELECT count(*) FROM expense_transactions WHERE id='" + id + "' AND deleted_at IS NOT NULL")).toBe("1");
  const restored = await page.request.post("/api/v1/invoices/trash/other/expense_transaction/" + id + "/restore", { headers });
  expect(restored.status()).toBe(200);
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
  const read = await page.request.get("/api/v1/expenses/transactions", { headers });
  expect((await read.json())[0].currency).toBe("USD");
  // Mocked transport failure below is rendering evidence only.
  await page.route("**/api/v1/expenses/transactions", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await expect(page.getByText("The available transactions could not be loaded. The inbox is not confirmed empty.")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toHaveCount(0);
  await page.unroute("**/api/v1/expenses/transactions");
  await page.getByRole("button", { name: "Retry loading inbox", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
});


async function seedImport(page: Page, headers: Record<string, string>, account: string, key = randomUUID()) {
  const response = await page.request.post("/api/v1/expenses/import/bank-statement", {
    headers: { ...headers, "Idempotency-Key": key },
    multipart: {
      file: { name: "synthetic.csv", mimeType: "text/csv", buffer: csv },
      account_reference: account, statement_currency: "USD",
    },
  });
  expect(response.status()).toBe(200);
  return { key, result: await response.json() };
}

async function savePending(page: Page, userId: string, orgId: string, slot: string,
  key: string, payload?: unknown) {
  await page.evaluate(async ({ userId, orgId, slot, key, payload }) => {
    const path = slot === "/expenses/from-selected" ? "/expenses" : slot;
    const envelope = payload === undefined ? {} : { path, payload };
    const bytes = await crypto.subtle.digest("SHA-256",
      new TextEncoder().encode(JSON.stringify(payload === undefined ? {} : envelope)));
    const digest = Array.from(new Uint8Array(bytes), (v) => v.toString(16).padStart(2, "0")).join("");
    sessionStorage.setItem("invoiceiq:command:" + userId + ":" + orgId + ":" + slot,
      JSON.stringify({ key, digest, ...envelope }));
  }, { userId, orgId, slot, key, payload });
}

test("the three outcome-check controls only read existing committed outcomes", async ({ page }) => {
  const { orgId, userId, headers } = await owner(page);
  const imported = await seedImport(page, headers, "read-card");
  const row = imported.result.transactions[0];
  const reportKey = randomUUID();
  const report = await page.request.post("/api/v1/expenses", {
    headers: { ...headers, "Idempotency-Key": reportKey },
    data: { title: "Read-only recovered report", currency: "USD",
      transaction_ids: [row.id], transaction_versions: { [row.id]: row.version } },
  });
  expect(report.status()).toBe(201);
  const other = await seedImport(page, headers, "remove-card");
  const removedRow = other.result.transactions[0];
  const removeKey = randomUUID();
  const removed = await page.request.delete("/api/v1/expenses/transactions/" + removedRow.id, {
    headers: { ...headers, "Idempotency-Key": removeKey }, params: { version: removedRow.version },
  });
  expect(removed.status()).toBe(204);
  await savePending(page, userId, orgId, "/expenses/import/bank-statement", imported.key);
  await savePending(page, userId, orgId, "/expenses/from-selected", reportKey);
  await savePending(page, userId, orgId, "/expenses/remove", removeKey);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/expenses") && ["POST", "DELETE", "PATCH"].includes(request.method())) writes.push(request.method());
  });
  await page.getByRole("button", { name: "Check previous import outcome", exact: true }).click();
  await expect(page.getByText(/Import committed:/)).toBeVisible();
  await page.getByRole("button", { name: "Check previous report outcome", exact: true }).click();
  await expect(page.getByText(/Report saved: Read-only recovered report/)).toBeVisible();
  await page.getByRole("button", { name: "Check previous removal outcome", exact: true }).click();
  await expect(page.getByText("The previous removal committed. The inbox below shows its current state.")).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("1");
  expect(sql("SELECT count(*) FROM expense_transactions WHERE org_id='" + orgId + "'")).toBe("2");
  expect(sql("SELECT count(*) FROM audit_events WHERE org_id='" + orgId + "' AND action='expense.transaction_binned'")).toBe("1");
});

test("retry original report looks up status before one same-key command", async ({ page }) => {
  const { orgId, userId, headers } = await owner(page);
  const imported = await seedImport(page, headers, "pending-card");
  const row = imported.result.transactions[0];
  const key = randomUUID();
  const payload = { title: "Original retry report", currency: "USD",
    transaction_ids: [row.id], transaction_versions: { [row.id]: row.version } };
  await savePending(page, userId, orgId, "/expenses/from-selected", key, payload);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  const attempts: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/expenses/commands/" + key + "/result")) attempts.push("lookup");
    if (request.url().endsWith("/api/v1/expenses") && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(key);
      attempts.push("write");
    }
  });
  await page.getByRole("button", { name: "Retry original report request", exact: true }).click();
  await expect(page.getByText(/Report saved: Original retry report/)).toBeVisible();
  expect(attempts[0]).toBe("lookup");
  expect(attempts.filter((value) => value === "write")).toHaveLength(1);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("1");
});

test("unreadable saved input explains recovery and starts no financial request", async ({ page }) => {
  const { userId, orgId } = await owner(page);
  await page.evaluate(({ userId, orgId }) => {
    sessionStorage.setItem("invoiceiq:command:" + userId + ":" + orgId + ":/expenses/from-selected", "{bad");
  }, { userId, orgId });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/expenses") && request.method() === "POST") writes.push(request.url());
  });
  await page.getByRole("button", { name: "Retry original report request", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "previous report request is unreadable" })).toBeVisible();
  await page.getByRole("button", { name: "Check previous report outcome", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "previous request is unreadable" })).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("0");
});

test("a rate-limited status lookup retains identity and cannot resend the report", async ({ page }) => {
  const { userId, orgId } = await owner(page);
  const key = randomUUID(), id = randomUUID();
  await savePending(page, userId, orgId, "/expenses/from-selected", key, {
    title: "Synthetic unresolved", currency: "USD",
    transaction_ids: [id], transaction_versions: { [id]: 1 },
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await page.route("**/api/v1/expenses/commands/" + key + "/result", (route) =>
    route.fulfill({ status: 429, contentType: "application/json",
      body: JSON.stringify({ detail: "Wait before checking the saved outcome again.", code: "rate_limited" }) }));
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/expenses") && request.method() === "POST") writes.push(request.url());
  });
  await page.getByRole("button", { name: "Check previous report outcome", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Wait before checking" })).toBeVisible();
  await page.getByRole("button", { name: "Retry original report request", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Could not check the previous request" })).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("0");
});
