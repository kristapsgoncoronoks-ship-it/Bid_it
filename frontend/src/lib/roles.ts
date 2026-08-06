import type { User, UserRoleName } from "./types";

// Per-company roles (low → high). None grants any cross-company power; a platform
// operator (is_platform_admin) is a separate, system-level capability.
//
// finance_manager/accountant/approver/auditor rank alongside "user" (non-admin)
// for THIS cosmetic ladder — none of them is a company administrator in the
// backend authz matrix (app/core/authz.py::ROLE_PERMISSIONS), matching
// backend/app/core/roles.py::ROLE_RANK. Remember this rendering is cosmetic
// only; the server is the actual control (see master context guidance).
export const ROLE_RANK: Record<UserRoleName, number> = {
  user_free: 0,
  user: 1,
  admin: 2,
  owner: 3,
  finance_manager: 1,
  accountant: 1,
  approver: 1,
  auditor: 1,
};

export const ROLE_LABELS: Record<UserRoleName, string> = {
  user_free: "User-free",
  user: "User",
  admin: "Admin",
  owner: "Owner",
  finance_manager: "Finance Manager",
  accountant: "Accountant",
  approver: "Approver",
  auditor: "Auditor",
};

export const ASSIGNABLE_ROLES: UserRoleName[] = [
  "user_free",
  "user",
  "admin",
  "owner",
  "finance_manager",
  "accountant",
  "approver",
  "auditor",
];

// ---------------------------------------------------------------------------
// Transport / VAT permission mirror (WO-78)
//
// The backend's authorization matrix is `app/core/authz.py::ROLE_PERMISSIONS`,
// and the server is the ONLY control — this mirror exists so the UI does not
// render an action the caller will only ever be refused (a dead button is worse
// than no button). It is cosmetic, exactly like `ROLE_RANK` above: hiding a
// control is never a security boundary, and every page still handles the
// server's 403 if one arrives.
//
// The legacy stored roles map onto business roles the same way
// `authz.business_role` maps them: owner→OWNER, admin→ADMINISTRATOR,
// user→EMPLOYEE, user_free→READ_ONLY. Values taken verbatim from the matrix:
// OWNER/ADMINISTRATOR/FINANCE_MANAGER hold all three; ACCOUNTANT holds
// read+write but NOT submit (submitting acquires invoice locks — the WO-49
// split); AUDITOR and READ_ONLY hold read; APPROVER and EMPLOYEE hold none.
export type VatPermission = "vat.read" | "vat.write" | "vat.submit";

const ALL_VAT: VatPermission[] = ["vat.read", "vat.write", "vat.submit"];

export const VAT_PERMISSIONS: Record<UserRoleName, VatPermission[]> = {
  owner: ALL_VAT,
  admin: ALL_VAT,
  finance_manager: ALL_VAT,
  accountant: ["vat.read", "vat.write"],
  auditor: ["vat.read"],
  user_free: ["vat.read"],
  approver: [],
  user: [],
};

/** Does this user's role hold the given VAT permission? A platform operator
 * (cross-tenant) is granted every permission by `authz.permissions_for`, so it
 * short-circuits here too. Unknown/absent user → nothing (deny-by-default). */
export function hasVatPerm(user: User | null | undefined, perm: VatPermission): boolean {
  if (!user) return false;
  if (user.is_platform_admin) return true;
  return (VAT_PERMISSIONS[user.role] ?? []).includes(perm);
}

export function rank(user?: User | null): number {
  if (!user) return 0;
  if (user.is_platform_admin) return ROLE_RANK.owner;
  return ROLE_RANK[user.role] ?? 0;
}

export function isAdminOrAbove(user?: User | null): boolean {
  return rank(user) >= ROLE_RANK.admin;
}

// The company's top role (owner) — full administration of THAT company only.
export function isOwner(user?: User | null): boolean {
  return rank(user) >= ROLE_RANK.owner;
}

// A platform operator (cross-tenant). The only capability above a company owner.
export function isPlatformOperator(user?: User | null): boolean {
  return !!user?.is_platform_admin;
}
