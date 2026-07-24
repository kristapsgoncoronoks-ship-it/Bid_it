import { useMemo, useState } from "react";
import type { SortState } from "./DataTable";

/**
 * Client-side sorting helper for `DataTable`. Tracks a `{key, dir}` state, toggles
 * asc→desc→asc on repeated activation of the same column, and returns the sorted
 * rows. `accessors` maps a sort key to the comparable value for a row.
 *
 *   const { sort, toggle, sorted } = useSort(rows, {
 *     amount: (r) => r.amount, date: (r) => r.date,
 *   }, { key: "date", dir: "desc" });
 *   <DataTable rows={sorted} sort={sort} onSortChange={toggle} columns={...} />
 */
export function useSort<T>(
  rows: T[] | undefined,
  accessors: Record<string, (row: T) => string | number>,
  initial: SortState,
) {
  const [sort, setSort] = useState<SortState>(initial);

  function toggle(key: string) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  }

  const sorted = useMemo(() => {
    if (!rows) return rows;
    const get = accessors[sort.key];
    if (!get) return rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av < bv) return -1 * factor;
      if (av > bv) return 1 * factor;
      return 0;
    });
    // accessors is assumed stable across renders (defined inline is fine for small lists).
  }, [rows, sort, accessors]);

  return { sort, toggle, sorted };
}
