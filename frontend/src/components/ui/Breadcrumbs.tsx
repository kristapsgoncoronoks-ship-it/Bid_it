import { Fragment } from "react";
import { Link } from "react-router-dom";

export interface Crumb {
  label: string;
  /** Omit `to` on the current (last) page. */
  to?: string;
}

/**
 * Breadcrumb trail. Renders a labelled `<nav>` with an ordered list; the final
 * crumb is marked `aria-current="page"` and is not a link. Truncates gracefully
 * and stays on one line (horizontal scroll on very narrow screens).
 */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex items-center gap-1.5 overflow-x-auto text-sm text-slate-500">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <Fragment key={`${c.label}-${i}`}>
              <li className="min-w-0 shrink-0">
                {c.to && !last ? (
                  <Link
                    to={c.to}
                    className="rounded text-slate-500 hover:text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                  >
                    {c.label}
                  </Link>
                ) : (
                  <span aria-current={last ? "page" : undefined} className={last ? "font-medium text-slate-700" : ""}>
                    {c.label}
                  </span>
                )}
              </li>
              {!last && (
                <li aria-hidden="true" className="shrink-0 text-slate-300">
                  /
                </li>
              )}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
