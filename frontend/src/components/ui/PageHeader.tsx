import type { ReactNode } from "react";
import { Breadcrumbs, type Crumb } from "./Breadcrumbs";

export interface PageHeaderProps {
  title: ReactNode;
  /** One-line supporting context under the title. */
  description?: ReactNode;
  /** Right-aligned primary/secondary actions (buttons). Wrap below title on mobile. */
  actions?: ReactNode;
  /** Optional breadcrumb trail rendered above the title. */
  breadcrumbs?: Crumb[];
  /** Optional meta row (badges, counts) rendered under the description. */
  meta?: ReactNode;
}

/**
 * The consistent top-of-page region: breadcrumbs → title + actions → description
 * → meta. The single place page titles are set, so heading hierarchy (one `<h1>`
 * per page) and the title/description/action layout are uniform across the app.
 * Responsive: title and actions share a row on desktop and stack on mobile.
 */
export function PageHeader({ title, description, actions, breadcrumbs, meta }: PageHeaderProps) {
  return (
    <div className="mb-6">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <div className="mb-2">
          <Breadcrumbs items={breadcrumbs} />
        </div>
      )}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-slate-800 sm:text-2xl">{title}</h1>
          {description && <p className="mt-1 max-w-2xl text-sm text-slate-500">{description}</p>}
          {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
