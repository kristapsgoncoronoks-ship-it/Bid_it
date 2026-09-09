import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * FE-004 — the ratchet reaches zero (audit 2026-09-05, P2 batch 8).
 *
 * Twenty pages answered a failed read with something that was not the truth.
 * The shape differed by page, and the assertions below differ with it:
 *
 *  - most rendered their EMPTY copy — "No offers yet", "Nothing open",
 *    "No customers yet." — a true-looking answer to a request that never
 *    succeeded (`never`, a text assertion);
 *  - `/audit` and `/team` render through `DataTable`, which treats
 *    `rows === undefined` as still loading, so they showed a skeleton that
 *    never resolved (`neverBusy`: nothing may be left `aria-busy`);
 *  - `/access` and `/platform` drew their table headers as if the read had
 *    succeeded, so `never` names a header rather than empty copy;
 *  - `/partners` rendered a blank panel — its empty copy is guarded on
 *    `data?.length === 0`, false while `data` is undefined — so there the alert
 *    is the load-bearing half and the text assertion is a regression pin;
 *  - `/explore` disables its result query until the field catalogue loads, so a
 *    failing catalogue left the chart card saying "Loading…" for ever. The page
 *    now says why instead, and the assertion is that "Loading…" is gone.
 *
 * One test per page: mock the page's own read as a 500, assert the alert names
 * what failed, then assert the untrue thing is absent. The 500 is chosen over a
 * 403 only because it is the less ambiguous failure; the page draws no
 * authorization conclusion either way (a 403 renders the same alert — see
 * error-states.spec.ts). Live app shell, API mocked via page.route. Synthetic
 * fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };
const MODULES = [
  { key: "issuing", name: "Invoice issuing", description: "Issue customer invoices", core: false, enabled: true },
  { key: "expenses", name: "Employee expenses", description: "", core: false, enabled: true },
];

const json = (body: unknown, code = 200) => ({
  status: code,
  contentType: "application/json",
  body: JSON.stringify(body),
});

type Handler = (p: string, route: Route) => Promise<void> | void | false;

async function signedIn(page: Page, handler: Handler) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const user = {
    id: "u1",
    email: "crew@haulage.example",
    name: "Site Crew",
    role: "owner",
    org_id: "org-1",
    is_platform_admin: true,
  };
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me") return route.fulfill(json({ user, organization: ORG }));
    if (p === "/auth/organizations") {
      return route.fulfill(json([{ org_id: "org-1", name: ORG.name, role: "owner", current: true }]));
    }
    if (p === "/modules") return route.fulfill(json(MODULES));
    if (p === "/dashboard/onboarding") {
      return route.fulfill(json({ steps: [], done_count: 0, complete: true, dismissed: true, can_dismiss: true }));
    }
    const handled = await handler(p, route);
    if (handled === false) return route.fulfill(json([]));
  });
}

const FAILURE = { detail: "Mocked failure for this test", code: "mocked_failure" };

/** The page, the read that fails, what the alert must say, and what must NOT be
 * on the page afterwards — either text (`never`) or an unresolved loading
 * region (`neverBusy`, for the two `DataTable` pages). */
const CASES: {
  path: string;
  fails: string;
  title: string;
  never?: string;
  neverBusy?: boolean;
  /** A second thing that must be absent — a count that used to read a
   * confident zero beside the alert. */
  alsoNever?: string;
}[] = [
  { path: "/pipeline", fails: "/masters/offers-pipeline", title: "Couldn’t load the pipeline", never: "No offers yet" },
  { path: "/statements", fails: "/transport/statements/findings", title: "Couldn’t load the review queue", never: "Nothing open." },
  { path: "/team", fails: "/team/members", title: "Couldn’t load the members", neverBusy: true },
  { path: "/customers", fails: "/customers", title: "Couldn’t load customers", never: "No customers yet." },
  { path: "/customers/c-1", fails: "/customers/c-1/notes", title: "Couldn’t load the notes", never: "Nothing noted yet." },
  { path: "/audit", fails: "/audit", title: "Couldn’t load the audit log", neverBusy: true, alsoNever: "0 events" },
  { path: "/reconciliation", fails: "/reconciliation/statements", title: "Couldn’t load the statements", never: "No statements imported yet." },
  { path: "/reimbursements", fails: "/reimbursements", title: "Couldn’t load the payout batches", never: "No payout batches yet." },
  { path: "/fx", fails: "/fx/ecb-comparison", title: "Couldn’t load the ECB comparison", never: "No foreign-currency invoices yet" },
  { path: "/explore", fails: "/analytics/fields", title: "Couldn’t load the field catalogue", never: "Loading…" },
  { path: "/budget", fails: "/budget/overview", title: "Couldn’t load the budget", never: "No spend or budgets" },
  { path: "/automation", fails: "/automation/rules", title: "Couldn’t load the rules", never: "No rules yet" },
  { path: "/access", fails: "/access/matrix", title: "Couldn’t load the plan matrix", never: "Invoices / month" },
  { path: "/platform", fails: "/platform/tenants", title: "Couldn’t load the tenants", never: "Seats" },
  { path: "/partners", fails: "/partners", title: "Couldn’t load partners", never: "No partners yet." },
  { path: "/email", fails: "/email/inbox", title: "Couldn’t load the inbox", never: "Nothing yet. Forward an invoice", alsoNever: "0 received" },
  { path: "/benchmark", fails: "/analytics/combined-benchmark", title: "Couldn’t load the combined benchmark", never: "Not enough data yet" },
  { path: "/dunning", fails: "/dunning/policy", title: "Couldn’t load the dunning ladder", never: "No levels — dunning is disabled" },
  { path: "/expenses/policy", fails: "/expenses/policy", title: "Couldn’t load the expense policy", never: "Loading…" },
  { path: "/invoices/inv-1/review", fails: "/invoices/inv-1/review", title: "Couldn’t load this invoice", never: "Loading…" },
];

// The failed request also raises the global toast (its own role="alert");
// scoping to the ErrorState's title keeps each assertion about the page.
const errorState = (page: Page, title: string) => page.getByRole("alert").filter({ hasText: title });

for (const c of CASES) {
  const what = c.neverBusy ? "an endless skeleton" : `"${c.never}"`;
  test(`FE-004 ${c.path}: a failed read says so instead of ${what}`, async ({ page }) => {
    await signedIn(page, (p, route) => {
      if (p === c.fails) return route.fulfill(json(FAILURE, 500));
      return false;
    });
    await page.goto(c.path);

    await expect(errorState(page, c.title)).toBeVisible();
    if (c.neverBusy) {
      // `DataTable` reports `aria-busy` while it believes it is loading; before
      // the fix these two pages stayed that way for ever.
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    } else {
      await expect(page.getByText(c.never!)).toHaveCount(0);
    }
    if (c.alsoNever) {
      // A count is an answer too: "0 events" beside "couldn't load the audit
      // log" is the same false confidence in a smaller font.
      await expect(page.getByText(c.alsoNever)).toHaveCount(0);
    }
  });
}

test("FE-004: a failed REFETCH does not leave the empty copy under the alert", async ({ page }) => {
  // The first load succeeds and is empty, so react-query holds `[]`; the refetch
  // then fails and `isError` goes true while `data` stays `[]`. A page that
  // renders its empty copy on `data?.length === 0` alone shows BOTH — the alert
  // saying the read failed, above the sentence asserting there are none. That
  // is the false answer FE-004 exists to remove, and it is invisible to a
  // first-load test because `undefined === 0` is false.
  let listCalls = 0;
  await signedIn(page, (p, route) => {
    if (p === "/partners" && route.request().method() === "GET") {
      listCalls += 1;
      return listCalls === 1
        ? route.fulfill(json([]))
        : route.fulfill(json(FAILURE, 500));
    }
    if (p === "/partners") {
      return route.fulfill(json({ id: "p-1", name: "Baltic Freight AS" }));
    }
    // The created partner is selected, so its panel reads too — full fixtures,
    // or the panel throws on a shape it did not expect and the page is gone.
    if (p === "/partners/p-1") {
      return route.fulfill(
        json({
          id: "p-1",
          name: "Baltic Freight AS",
          email: null,
          requires_contract: false,
          requires_acceptance: false,
          penalty_enabled: false,
          penalty_rate: null,
          readiness: { ready: true, missing: [], required: [] },
          documents: [],
        }),
      );
    }
    if (p === "/partners/p-1/penalty") {
      return route.fulfill(
        json({
          currency: "EUR",
          total_penalty: "0.00",
          total_outstanding: "0.00",
          max_days_overdue: 0,
          can_generate: false,
          blocked_reason: "No overdue invoices.",
          lines: [],
        }),
      );
    }
    return false;
  });
  await page.goto("/partners");

  await expect(page.getByText("No partners yet.")).toBeVisible();

  // Creating a partner refetches the list — the flow that produces the state.
  await page.getByLabel("Company name").fill("Baltic Freight AS");
  await page.getByRole("button", { name: "Add partner" }).click();

  await expect(errorState(page, "Couldn’t load partners")).toBeVisible();
  await expect(page.getByText("No partners yet.")).toHaveCount(0);
});

test("FE-004: Try again after a 500 renders the page once the read succeeds", async ({ page }) => {
  // Always-500 first (a call-count sequence would depend on how many times
  // React Strict Mode's dev-mode double invocation fetches on mount); once the
  // error is on screen a SECOND route registration for the same path takes
  // over — Playwright dispatches the most recently added handler first — so
  // the retry's request, and only the retry's, gets the success response.
  await signedIn(page, (p, route) => {
    if (p === "/masters/offers-pipeline") return route.fulfill(json(FAILURE, 500));
    return false;
  });
  await page.goto("/pipeline");

  const alert = errorState(page, "Couldn’t load the pipeline");
  await expect(alert).toBeVisible();

  await page.route("**/api/v1/masters/offers-pipeline", (route) =>
    route.fulfill(
      json({
        stale_after_days: 14,
        columns: {
          draft: [],
          sent: [
            {
              offer_id: "o-1",
              number: "OFF-7",
              version: 1,
              title: "Yard extension",
              total: "1200.00",
              currency: "EUR",
              project_id: "p-1",
              project: "YARD-1",
              customer: "Cargo GmbH",
              days_in_stage: 3,
              stale: false,
            },
          ],
          accepted: [],
          rejected: [],
        },
      }),
    ),
  );
  await page.getByRole("button", { name: "Try again" }).click();

  await expect(page.getByText("OFF-7 v1")).toBeVisible();
  await expect(alert).toHaveCount(0);
});
