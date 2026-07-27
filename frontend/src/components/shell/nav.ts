import type { ReactNode } from "react";
import { createElement as h } from "react";

/** A single navigation destination. */
export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  /** Exact-match the route for "active" styling (root/dashboard items). Default false. */
  end?: boolean;
}

/** A titled group of nav items (e.g. "Payables", "Receivables"). */
export interface NavGroup {
  title: string;
  items: NavItem[];
}

// Minimal inline icons (no icon dependency). 1.5px stroke, currentColor.
// Exported so the live app's nav (`frontend/src/lib/nav.ts`) can build its own
// icon set in the same visual language without a separate dependency.
export function icon(path: string): ReactNode {
  return h(
    "svg",
    { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", "aria-hidden": true },
    h("path", { d: path, stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" }),
  );
}

/**
 * The application information architecture — the grouped left-nav for the finance
 * workspace. Grouping (Overview / Payables / Receivables / Insights / Workspace)
 * keeps ~9 destinations scannable without a flat list. Paths are namespaced under
 * `/design` so this shell demo never collides with the live app's routes.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "Overview",
    items: [{ to: "/design", label: "Dashboard", icon: icon("M4 13h6V4H4v9zm10 7h6V4h-6v16zM4 20h6v-4H4v4z"), end: true }],
  },
  {
    title: "Payables",
    items: [
      { to: "/design/supplier-invoices", label: "Supplier invoices", icon: icon("M6 3h9l3 3v15H6V3zM9 8h6M9 12h6M9 16h4") },
      { to: "/design/expenses", label: "Expenses", icon: icon("M3 7h18v10H3V7zm0 4h18M7 15h3") },
      { to: "/design/payments", label: "Payments", icon: icon("M3 6h18v12H3V6zm0 4h18M7 14h4") },
    ],
  },
  {
    title: "Receivables",
    items: [
      { to: "/design/customer-invoices", label: "Customer invoices", icon: icon("M6 3h12v18l-3-2-3 2-3-2-3 2V3zM9 8h6M9 12h6") },
      { to: "/design/contacts", label: "Contacts", icon: icon("M16 20v-1a4 4 0 00-4-4H8a4 4 0 00-4 4v1M10 11a3 3 0 100-6 3 3 0 000 6zm10 9v-1a4 4 0 00-3-3.8") },
    ],
  },
  {
    title: "Insights",
    items: [{ to: "/design/reports", label: "Reports", icon: icon("M4 20V10M10 20V4M16 20v-7M22 20H2") }],
  },
  {
    title: "Workspace",
    items: [
      { to: "/design/settings", label: "Settings", icon: icon("M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 00-1.7-1L14.5 3h-4l-.3 2.4a7 7 0 00-1.7 1l-2.4-1-2 3.4 2 1.6a7 7 0 000 2l-2 1.6 2 3.4 2.4-1a7 7 0 001.7 1l.3 2.4h4l.3-2.4a7 7 0 001.7-1l2.4 1 2-3.4-2-1.6a7 7 0 00.1-1z") },
      { to: "/design/administration", label: "Administration", icon: icon("M12 3l8 4v5c0 4.5-3.4 7.7-8 9-4.6-1.3-8-4.5-8-9V7l8-4zM9.5 12l2 2 3.5-4") },
    ],
  },
];

/** Flat list of every destination — used to resolve the current page's title. */
export const NAV_FLAT: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);
