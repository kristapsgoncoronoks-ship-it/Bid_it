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
  return { orgId: uuid(identity.organization.id), headers };
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
