import { lazy, type ComponentType } from "react";

/** React caches a rejected lazy import. A page remount cannot retry it. */
export class PageLoadError extends Error {
  readonly cause: unknown;

  constructor(cause: unknown) {
    super("The page could not load.");
    this.name = "PageLoadError";
    this.cause = cause;
  }
}

// Preserve React.lazy's component/props inference while marking load failures.
export function lazyPage<T extends ComponentType<any>>(load: () => Promise<{ default: T }>) {
  return lazy(async () => {
    try {
      return await load();
    } catch (cause) {
      throw new PageLoadError(cause);
    }
  });
}
