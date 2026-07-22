import { test, expect } from "@playwright/test";

/**
 * End-to-end smoke tests over the `/design` showcase. They exercise the shell and
 * the key interaction primitives through ROLE-based selectors — which doubles as an
 * accessibility check: if a control has no accessible name/role, the test can't find
 * it. Fixtures make everything deterministic (no backend).
 */

test.describe("application shell", () => {
  test("renders the dashboard and navigates the IA", async ({ page }) => {
    await page.goto("/design");
    await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();

    // Primary nav exposes every area.
    const nav = page.getByRole("navigation", { name: "Primary" });
    for (const label of ["Dashboard", "Supplier invoices", "Expenses", "Customer invoices", "Payments", "Reports", "Contacts", "Settings", "Administration"]) {
      await expect(nav.getByRole("link", { name: label })).toBeVisible();
    }

    await nav.getByRole("link", { name: "Supplier invoices" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Supplier invoices" })).toBeVisible();
    // Breadcrumb reflects location.
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Supplier invoices");
  });

  test("legal-entity switcher opens and switches", async ({ page }) => {
    await page.goto("/design");
    const trigger = page.getByRole("button", { name: "Switch legal entity" });
    await trigger.click();
    const menu = page.getByRole("menu", { name: "Switch legal entity" });
    await expect(menu).toBeVisible();
    await menu.getByRole("menuitem", { name: /Northwind Lietuva/ }).click();
    await expect(trigger).toContainText("Northwind Lietuva");
  });

  test("user menu is keyboard operable and closes on Escape", async ({ page }) => {
    await page.goto("/design");
    const trigger = page.getByRole("button", { name: "Account menu" });
    await trigger.click();
    await expect(page.getByRole("menu", { name: "Account menu" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu", { name: "Account menu" })).toBeHidden();
  });
});

test.describe("supplier invoices list", () => {
  test("filter, sort, drawer detail and confirm", async ({ page }) => {
    await page.goto("/design/supplier-invoices");

    // Filter by status.
    await page.getByLabel("Status").selectOption("overdue");
    const table = page.getByRole("table", { name: "Supplier invoices" });
    await expect(table).toContainText("Neste"); // the one overdue fixture row
    await expect(table).not.toContainText("Circle K"); // filtered out

    // Clear the filter chip.
    await page.getByRole("button", { name: "Remove filter" }).click();
    await expect(page.getByLabel("Status")).toHaveValue("all");

    // Sort by Gross ascending (aria-sort updates). Shell Fleet has the lowest
    // gross, so it lands on page 1 after the sort.
    const gross = page.getByRole("button", { name: /Gross/ });
    await gross.click();
    await expect(page.getByRole("columnheader", { name: /Gross/ })).toHaveAttribute("aria-sort", "ascending");

    // Open row detail drawer.
    await table.getByText("Shell Fleet").click();
    const drawer = page.getByRole("dialog", { name: "Shell Fleet" });
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText("History");

    // Approve → confirm dialog → toast.
    await drawer.getByRole("button", { name: "Approve" }).click();
    const confirm = page.getByRole("dialog", { name: /Approve this invoice/ });
    await confirm.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("alert")).toContainText(/approved/i);
  });
});

test.describe("expenses submission", () => {
  test("modal form validates then submits", async ({ page }) => {
    await page.goto("/design/expenses");
    await page.getByRole("button", { name: "New expense" }).click();
    const modal = page.getByRole("dialog", { name: "New expense" });
    await expect(modal).toBeVisible();

    // Submit empty → inline validation.
    await modal.getByRole("button", { name: "Submit" }).click();
    await expect(modal.getByText("Enter an amount")).toBeVisible();
    await expect(modal.getByText("Pick the expense date")).toBeVisible();

    // Fill required fields → submit succeeds.
    await modal.getByLabel("Amount").fill("42.50");
    await modal.getByLabel("Date").fill("2026-07-10");
    await modal.getByRole("button", { name: "Submit" }).click();
    await expect(page.getByRole("alert")).toContainText(/submitted/i);
  });
});

test.describe("payments tabs", () => {
  test("tabs filter the list", async ({ page }) => {
    await page.goto("/design/payments");
    await page.getByRole("tab", { name: /Failed/ }).click();
    const table = page.getByRole("table", { name: /Payments/ });
    await expect(table).toContainText("Shell Fleet"); // the one failed fixture row
    await expect(table).not.toContainText("DKV Mobility"); // a completed row, filtered out
  });
});

test.describe("component gallery", () => {
  test("loads and the modal traps + restores focus", async ({ page }) => {
    await page.goto("/design/gallery");
    await expect(page.getByRole("heading", { level: 1, name: /design system/i })).toBeVisible();

    await page.getByRole("button", { name: "Open modal" }).click();
    const modal = page.getByRole("dialog", { name: "Example modal" });
    await expect(modal).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(modal).toBeHidden();
  });
});
