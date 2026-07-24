/**
 * Tiny classNames joiner. Filters out falsy values so conditional classes read
 * cleanly:  cx("base", isActive && "active", error && "text-rose-600").
 *
 * Deliberately dependency-free — the UI kit stays lean.
 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
