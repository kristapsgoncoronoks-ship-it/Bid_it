import type { ReactNode } from "react";
import { Skeleton } from "./Skeleton";
import { EmptyState } from "./EmptyState";

export type Align = "left" | "right" | "center";

export interface Column<T> {
  /** Stable key (also used for the React key of header/cells). */
  key: string;
  header: ReactNode;
  /** Cell renderer. Receives the row and its index. */
  cell: (row: T, index: number) => ReactNode;
  align?: Align;
  /** Extra classes for the <td> (and matching <th>). */
  className?: string;
  /** Fixed column width, e.g. "8rem". */
  width?: string;
  /** When true, the header becomes a sort toggle (needs `sort`/`onSortChange`). */
  sortable?: boolean;
  /** Sort identifier if it differs from `key`. */
  sortKey?: string;
}

export interface SortState {
  key: string;
  dir: "asc" | "desc";
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  /** `undefined` is treated as "not loaded yet" when `loading` isn't passed. */
  rows: T[] | undefined;
  /** Stable identity per row (for React keys). */
  rowKey: (row: T) => string;
  loading?: boolean;
  /** How many skeleton rows to show while loading. */
  skeletonRows?: number;
  /** Shown when there are zero rows and we're not loading. */
  empty?: ReactNode;
  /** Screen-reader caption describing the table. */
  caption?: string;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string;
  /** Tighter vertical padding. */
  dense?: boolean;
  className?: string;
  /** Current sort (for `sortable` columns). Sets `aria-sort` on the header. */
  sort?: SortState;
  /** Called with the column's sort key when a sortable header is activated. */
  onSortChange?: (key: string) => void;
}

const ARIA_SORT: Record<"asc" | "desc", "ascending" | "descending"> = {
  asc: "ascending",
  desc: "descending",
};

const ALIGN: Record<Align, string> = { left: "text-left", right: "text-right", center: "text-center" };

/**
 * A column-driven table with loading skeletons, an empty state, horizontal
 * scroll on small screens, and accessible markup (scoped headers, a caption,
 * aria-busy while loading, and keyboard-activatable rows when clickable).
 *
 * One component replaces the bespoke <table> markup hand-rolled across pages.
 */
export function DataTable<T>({
  columns, rows, rowKey, loading = false, skeletonRows = 6, empty,
  caption, onRowClick, rowClassName, dense = false, className = "",
  sort, onSortChange,
}: DataTableProps<T>) {
  const isLoading = loading || rows === undefined;
  const showEmpty = !isLoading && rows !== undefined && rows.length === 0;
  const pad = dense ? "px-4 py-2" : "px-4 py-3";
  const clickable = !!onRowClick;

  return (
    <div className={`overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      <table className="w-full text-sm" aria-busy={isLoading || undefined}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            {columns.map((c) => {
              const key = c.sortKey ?? c.key;
              const active = c.sortable && sort?.key === key;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={c.sortable ? (active ? ARIA_SORT[sort!.dir] : "none") : undefined}
                  style={c.width ? { width: c.width } : undefined}
                  className={`${pad} ${ALIGN[c.align ?? "left"]} ${c.className ?? ""}`}
                >
                  {c.sortable && onSortChange ? (
                    <button
                      type="button"
                      onClick={() => onSortChange(key)}
                      className="group -mx-1 inline-flex items-center gap-1 rounded px-1 uppercase tracking-wide hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                    >
                      {c.header}
                      <span aria-hidden="true" className={active ? "text-brand-600" : "text-slate-300 group-hover:text-slate-400"}>
                        {active ? (sort!.dir === "asc" ? "↑" : "↓") : "↕"}
                      </span>
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {isLoading &&
            Array.from({ length: skeletonRows }).map((_, r) => (
              <tr key={`sk-${r}`} className="border-b border-slate-100 last:border-0">
                {columns.map((c) => (
                  <td key={c.key} className={`${pad} ${ALIGN[c.align ?? "left"]}`}>
                    <Skeleton className={`h-3.5 ${c.align === "right" ? "ml-auto w-16" : "w-24"}`} />
                  </td>
                ))}
              </tr>
            ))}

          {showEmpty && (
            <tr>
              <td colSpan={columns.length}>
                {empty ?? <EmptyState title="Nothing here yet" />}
              </td>
            </tr>
          )}

          {!isLoading &&
            rows?.map((row, i) => (
              <tr
                key={rowKey(row)}
                className={`border-b border-slate-100 last:border-0 ${clickable ? "cursor-pointer hover:bg-slate-50 focus-within:bg-slate-50" : ""} ${rowClassName?.(row) ?? ""}`}
                {...(clickable
                  ? {
                      onClick: () => onRowClick!(row),
                      tabIndex: 0,
                      role: "button",
                      onKeyDown: (e: React.KeyboardEvent) => {
                        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRowClick!(row); }
                      },
                    }
                  : {})}
              >
                {columns.map((c) => (
                  <td key={c.key} className={`${pad} ${ALIGN[c.align ?? "left"]} ${c.className ?? ""}`}>
                    {c.cell(row, i)}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
