import { icon, type NavItem } from "../components/shell/nav";
import type { VatPermission } from "./roles";

/**
 * The live app's navigation IA (board I1.2). Mirrors `docs/DESIGN_SYSTEM.md`'s
 * grouping (Overview / Payables / Receivables / Insights / Workspace) but with the
 * real ~28 destinations `frontend/src/App.tsx` actually routes, not the nine-item
 * fixture showcase under `/design`.
 *
 * Each item carries the same three gating flags the flat nav it replaces used
 * (`frontend/src/components/Layout.tsx` before this change): `module` (only shown
 * when that add-on module is enabled), `admin` (business-admin or above), `owner`
 * (company owner or above). Filtering happens in `Layout.tsx`, not here — this
 * module is data + a couple of pure helpers, no React, no auth/module reads.
 */
export interface LiveNavItem extends NavItem {
  /** Only shown when this module key is enabled for the org. */
  module?: string;
  /** Only shown to admin-or-above (`isAdminOrAbove`). */
  admin?: boolean;
  /** Only shown to the company owner (`isOwner`). */
  owner?: boolean;
  /**
   * Only shown when the user's role holds this permission in the backend
   * matrix (`app/core/authz.py::ROLE_PERMISSIONS`, mirrored cosmetically by
   * `lib/roles.ts::hasVatPerm`). Used by the transport vertical, whose routes
   * gate on VAT_* permissions rather than on the admin/owner ladder — an
   * EMPLOYEE holds no VAT permission at all, so the destination would 403.
   * Absent on every other item, so filtering is unchanged for them.
   */
  perm?: VatPermission;
}

export interface LiveNavGroup {
  title: string;
  items: LiveNavItem[];
}

export const LIVE_NAV: LiveNavGroup[] = [
  {
    title: "Overview",
    items: [
      { to: "/", label: "Dashboard", end: true, icon: icon("M4 13h6V4H4v9zm10 7h6V4h-6v16zM4 20h6v-4H4v4z") },
      { to: "/schedule", label: "Schedule", icon: icon("M7 2v3M17 2v3M3 8h18M5 5h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1zM8 12h3v3H8v-3z") },
    ],
  },
  {
    title: "Payables",
    items: [
      { to: "/invoices", label: "Invoices", icon: icon("M6 3h9l3 3v15H6V3zM9 8h6M9 12h6M9 16h4") },
      { to: "/captures", label: "Captures", icon: icon("M4 7l8-4 8 4v10l-8 4-8-4V7zm8-4v18") },
      // H-1: failures need their own entry, not a tab inside the queue of things
      // that worked. A document that never became an invoice is invisible unless
      // something in the navigation says it exists.
      {
        to: "/captures/failures",
        // NOT "Unread documents": the Workspace group already has a "Documents"
        // item for the document store, and a label that contains another item's
        // label reads as a sub-page of it. "Failed captures" also pairs with the
        // "Captures" item directly above, which is exactly the relationship.
        label: "Failed captures",
        icon: icon("M12 9v4m0 4h.01M10.3 3.9l-8 14A2 2 0 004 21h16a2 2 0 001.7-3.1l-8-14a2 2 0 00-3.4 0z"),
      },
      { to: "/review", label: "Review", icon: icon("M9 12l2 2 4-4M12 3l8 4v5c0 4.5-3.4 7.7-8 9-4.6-1.3-8-4.5-8-9V7l8-4z") },
      { to: "/payment-runs", label: "Payment runs", icon: icon("M3 6h18v12H3V6zm0 4h18M7 14h4") },
      { to: "/vendors", label: "Suppliers", icon: icon("M4 21V10l8-6 8 6v11h-5v-6H9v6H4z") },
      { to: "/upload", label: "Upload", icon: icon("M12 16V4m0 0L7 9m5-5l5 5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3") },
      { to: "/email", label: "Email intake", module: "email_intake", icon: icon("M3 6h18v12H3V6zm0 0l9 7 9-7") },
      { to: "/expenses", label: "Expenses", module: "expenses", end: true, icon: icon("M3 7h18v10H3V7zm0 4h18M7 15h3") },
      {
        to: "/expenses/policy",
        label: "Expense policy",
        module: "expenses",
        admin: true,
        icon: icon("M9 12l2 2 4-4M12 3l8 4v5c0 4.5-3.4 7.7-8 9-4.6-1.3-8-4.5-8-9V7l8-4z"),
      },
    ],
  },
  {
    title: "Receivables",
    items: [
      { to: "/issue", label: "Issue", module: "issuing", end: true, icon: icon("M6 3h12v18l-3-2-3 2-3-2-3 2V3zM9 8h6M9 12h6") },
      { to: "/customers", label: "Customers", module: "issuing", icon: icon("M16 20v-1a4 4 0 00-4-4H8a4 4 0 00-4 4v1M10 11a3 3 0 100-6 3 3 0 000 6zm10 9v-1a4 4 0 00-3-3.8") },
      { to: "/receipts", label: "Receipts", module: "issuing", icon: icon("M6 2h12v20l-3-2-3 2-3-2-3 2V2zM9 7h6M9 11h6") },
      { to: "/reconciliation", label: "Reconciliation", module: "issuing", icon: icon("M4 4l16 16M20 4L4 20") },
      { to: "/issue/reports", label: "Invoice reports", module: "issuing", icon: icon("M4 20V10M10 20V4M16 20v-7M22 20H2") },
      { to: "/partners", label: "Partners", module: "issuing", icon: icon("M17 20v-1a4 4 0 00-4-4H7a4 4 0 00-4 4v1M9 11a3 3 0 100-6 3 3 0 000 6zm9 9v-1a3.9 3.9 0 00-2.5-3.6M15 5a3 3 0 010 5.8") },
      { to: "/dunning", label: "Dunning", module: "issuing", admin: true, icon: icon("M12 9v4m0 4h.01M4.9 4.9l14.2 14.2") },
    ],
  },
  {
    title: "Insights",
    items: [
      { to: "/explore", label: "Explore", icon: icon("M11 4a7 7 0 105.3 12.6l4.05 4.05 1.4-1.4-4.05-4.05A7 7 0 0011 4zm0 2a5 5 0 110 10 5 5 0 010-10z") },
      { to: "/benchmark", label: "Benchmark", icon: icon("M4 20V10M10 20V4M16 20v-7M22 20H2") },
      { to: "/fx", label: "FX", icon: icon("M7 8l4-4 4 4M11 4v12M17 16l-4 4-4-4M13 20V8") },
      { to: "/cash-position", label: "Cash position", icon: icon("M3 6h18v12H3V6zm0 4h18M7 14h4") },
      { to: "/budget", label: "Budget", module: "budget", icon: icon("M4 4h16v16H4V4zm4 12V8m4 8V11m4 5V6") },
    ],
  },
  {
    // The transport vertical (EU cross-border VAT refunds, Dir. 2008/9/EC) — a
    // plug-in bounded context, so it is its own group and appears only for an
    // org that has the `transport` module AND a role holding VAT_READ.
    title: "Transport",
    items: [
      {
        // The portfolio answer — "how much can we still recover this year, and
        // what is stopping each euro of it?". Gated on `transport.read`, the
        // permission `routes/transport/recovery.py` actually declares (WO-81
        // reserved it for exactly the derived analytics slices), not on the
        // `vat.read` the claim routes use. Identical role coverage today; the
        // point is that a future split in the backend matrix lands here too.
        to: "/recovery",
        label: "Cash recovery",
        module: "transport",
        perm: "transport.read",
        icon: icon("M12 3v18M8 7h6a3 3 0 010 6H9a3 3 0 000 6h7"),
      },
      {
        to: "/vat-claims",
        label: "VAT claims",
        module: "transport",
        perm: "vat.read",
        icon: icon("M6 3h9l3 3v15H6V3zM9 12l2 2 4-4"),
      },
      {
        // The R44 activation ladder. Same gating as the claims entry: the
        // routes are VAT_READ (with VAT_WRITE on the transitions), so the
        // destination is shown to anyone who can read it and the mutating
        // controls hide themselves inside the page.
        to: "/vat-customers",
        label: "Customer activation",
        module: "transport",
        perm: "vat.read",
        icon: icon("M16 20v-1a4 4 0 00-4-4H8a4 4 0 00-4 4v1M10 11a3 3 0 100-6 3 3 0 000 6zm10 9v-1a4 4 0 00-3-3.8"),
      },
      {
        // The supplier side of recovery — what a supplier owes US, as against
        // what a member state owes us. Same `transport.read` gating as the
        // dashboard: `overcharges.py` declares TRANSPORT_READ at router level
        // and overrides the mutations to VAT_WRITE, which the page mirrors
        // control by control.
        to: "/overcharges",
        label: "Supplier overcharges",
        module: "transport",
        perm: "transport.read",
        icon: icon("M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"),
      },
      {
        // The registry that keeps the overcharge demand honest: without the
        // rebate document those litres are priced at list.
        to: "/rebates",
        label: "Off-invoice rebates",
        module: "transport",
        perm: "transport.read",
        icon: icon("M9 14l6-6M9.5 8.5h.01M14.5 14.5h.01M7 3h10a4 4 0 014 4v10a4 4 0 01-4 4H7a4 4 0 01-4-4V7a4 4 0 014-4z"),
      },
      {
        // The other price analysis, and deliberately not next to the supplier
        // one in vocabulary: these figures are negotiation evidence, not a
        // contractual entitlement (R53's second framing, which the backend
        // enforces structurally and `pages/Savings.tsx` must not undo). Same
        // `transport.read` gating — `routes/transport/savings.py` declares it at
        // router level and declares no write verb at all.
        to: "/savings",
        label: "Negotiation evidence",
        module: "transport",
        perm: "transport.read",
        icon: icon("M3 17l6-6 4 4 8-8M21 7h-5m5 0v5"),
      },
      {
        // The SECOND recoverable-cash stream, over the same validated diesel
        // lines — a separate regime handled by a customs authority, not by a tax
        // authority, which is why it is its own destination rather than a tab on
        // a VAT screen. Same `transport.read` gating as the analytics entries:
        // `routes/transport/excise.py` declares it at router level and overrides
        // only the two rate mutations to `vat.write`, which the page mirrors
        // control by control. Labelled for the regime, never for an entitlement:
        // the figure asserts no eligibility and the page must not imply one.
        to: "/excise",
        label: "Diesel excise",
        module: "transport",
        perm: "transport.read",
        icon: icon("M3 22h12V9l-3-3H3v16zM7 10h4M7 14h4M18 8v9a2 2 0 01-4 0"),
      },
      {
        // The one transport destination written for the CLIENT rather than the
        // operator (`BA_fleet_fuel.md` §1.2: the read-only `user` role sees only
        // "open" pages, of which `/claim-status` is one). Gated on `vat.read` —
        // the permission `routes/transport/claim_status.py` declares at router
        // level, and the one the READ_ONLY role already holds — rather than the
        // `transport.read` the derived-analytics screens use: this surface
        // returns the CLAIMS themselves, translated, not portfolio aggregates.
        to: "/claim-status",
        label: "Claim status",
        module: "transport",
        perm: "vat.read",
        icon: icon("M12 8v4l3 2M12 3a9 9 0 100 18 9 9 0 000-18z"),
      },
      {
        to: "/vat-admin",
        label: "VAT configuration",
        module: "transport",
        perm: "vat.read",
        icon: icon("M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 00-1.7-1L14.5 3h-4l-.3 2.4a7 7 0 00-1.7 1l-2.4-1-2 3.4 2 1.6a7 7 0 000 2l-2 1.6 2 3.4 2.4-1a7 7 0 001.7 1l.3 2.4h4l.3-2.4a7 7 0 001.7-1l2.4 1 2-3.4-2-1.6a7 7 0 00.1-1z"),
      },
    ],
  },
  {
    title: "Workspace",
    items: [
      { to: "/tax-codes", label: "Tax codes", admin: true, icon: icon("M9 12l2 2 4-4M12 3l8 4v5c0 4.5-3.4 7.7-8 9-4.6-1.3-8-4.5-8-9V7l8-4z") },
      { to: "/currencies", label: "Currencies", admin: true, icon: icon("M7 8l4-4 4 4M11 4v12M17 16l-4 4-4-4M13 20V8") },
      { to: "/cost-objects", label: "Cost objects", admin: true, icon: icon("M4 4h16v16H4V4zm4 12V8m4 8V11m4 5V6") },
      { to: "/documents", label: "Documents", admin: true, icon: icon("M6 3h9l3 3v15H6V3zM9 8h6M9 12h6M9 16h4") },
      { to: "/team", label: "Team", icon: icon("M16 20v-1a4 4 0 00-4-4H8a4 4 0 00-4 4v1M10 11a3 3 0 100-6 3 3 0 000 6zm10 9v-1a4 4 0 00-3-3.8") },
      { to: "/access", label: "Access", owner: true, icon: icon("M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 00-1.7-1L14.5 3h-4l-.3 2.4a7 7 0 00-1.7 1l-2.4-1-2 3.4 2 1.6a7 7 0 000 2l-2 1.6 2 3.4 2.4-1a7 7 0 001.7 1l.3 2.4h4l.3-2.4a7 7 0 001.7-1l2.4 1 2-3.4-2-1.6a7 7 0 00.1-1z") },
      { to: "/templates", label: "Templates", icon: icon("M6 3h9l3 3v15H6V3zM9 9h6M9 13h6M9 17h4") },
      { to: "/audit", label: "Audit log", owner: true, icon: icon("M4 20V10M10 20V4M16 20v-7M22 20H2") },
      { to: "/billing", label: "Billing", icon: icon("M3 6h18v12H3V6zm0 4h18M7 14h4") },
      { to: "/settings", label: "Settings", icon: icon("M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 00-1.7-1L14.5 3h-4l-.3 2.4a7 7 0 00-1.7 1l-2.4-1-2 3.4 2 1.6a7 7 0 000 2l-2 1.6 2 3.4 2.4-1a7 7 0 001.7 1l.3 2.4h4l.3-2.4a7 7 0 001.7-1l2.4 1 2-3.4-2-1.6a7 7 0 00.1-1z") },
    ],
  },
];

/** Best-effort current-page label for the top-bar breadcrumb: exact match first,
 * else the longest registered `to` that prefixes the pathname (so a detail route
 * like `/invoices/inv-1` still reads "Invoices"). Returns `undefined` for a path
 * with no ancestor in the nav (there is always at least "/" for a live route). */
export function matchNavItem(pathname: string, groups: LiveNavGroup[] = LIVE_NAV): LiveNavItem | undefined {
  const flat = groups.flatMap((g) => g.items);
  const exact = flat.find((i) => i.to === pathname);
  if (exact) return exact;
  const prefixed = flat
    .filter((i) => i.to !== "/" && pathname.startsWith(i.to))
    .sort((a, b) => b.to.length - a.to.length);
  return prefixed[0];
}
