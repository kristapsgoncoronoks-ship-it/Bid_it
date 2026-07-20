import type { User, UserRoleName } from "./types";

export const ROLE_RANK: Record<UserRoleName, number> = {
  user_free: 0,
  user: 1,
  admin: 2,
  sysadmin: 3,
};

export const ROLE_LABELS: Record<UserRoleName, string> = {
  user_free: "User-free",
  user: "User",
  admin: "Admin",
  sysadmin: "Sysadmin",
};

export const ASSIGNABLE_ROLES: UserRoleName[] = ["user_free", "user", "admin", "sysadmin"];

export function rank(user?: User | null): number {
  if (!user) return 0;
  if (user.is_platform_admin) return ROLE_RANK.sysadmin;
  return ROLE_RANK[user.role] ?? 0;
}

export function isAdminOrAbove(user?: User | null): boolean {
  return rank(user) >= ROLE_RANK.admin;
}

export function isSysadmin(user?: User | null): boolean {
  return rank(user) >= ROLE_RANK.sysadmin;
}
