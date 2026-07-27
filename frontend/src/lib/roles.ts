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
