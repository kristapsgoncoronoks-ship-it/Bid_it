import type { User, UserRoleName } from "./types";

// Per-company roles (low → high). None grants any cross-company power; a platform
// operator (is_platform_admin) is a separate, system-level capability.
export const ROLE_RANK: Record<UserRoleName, number> = {
  user_free: 0,
  user: 1,
  admin: 2,
  owner: 3,
};

export const ROLE_LABELS: Record<UserRoleName, string> = {
  user_free: "User-free",
  user: "User",
  admin: "Admin",
  owner: "Owner",
};

export const ASSIGNABLE_ROLES: UserRoleName[] = ["user_free", "user", "admin", "owner"];

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
