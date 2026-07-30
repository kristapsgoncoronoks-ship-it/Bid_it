import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";

// Minimal shape we need from a TanStack `useQuery` result — kept structural so
// it works without importing the query client's generic types.
interface QueryLike<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch?: () => void;
}

/**
 * Standardizes the async triad — loading, error, empty — around a query so every
 * data view handles them the same way instead of ad-hoc `isLoading &&` checks.
 *
 *   <QueryState query={q} isEmpty={(d) => d.items.length === 0}
 *               loading={<TableSkeleton/>} empty={<EmptyState .../>}>
 *     {(data) => <DataTable rows={data.items} .../>}
 *   </QueryState>
 *
 * Fail-open vs fail-closed (WO-45): this component changes only how an
 * ALREADY-failed request is presented — previously fail-silent (rendered as
 * an empty state indistinguishable from "no data"), now fail-visible (a
 * `role="alert"` error state with a retry). It introduces no new gate and
 * makes nothing more permissive: a 403 renders the same error state as a
 * 500, and the UI draws no authorization conclusion either way — the server
 * remains the sole authority on access (master-context §6).
 */
export function QueryState<T>({
  query, children, loading, empty, isEmpty, errorTitle = "Couldn’t load this",
}: {
  query: QueryLike<T>;
  children: (data: T) => ReactNode;
  loading?: ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  errorTitle?: string;
}) {
  if (query.isError) {
    return (
      <ErrorState
        title={errorTitle}
        onRetry={query.refetch}
      />
    );
  }
  if (query.isLoading || query.data === undefined) {
    return <>{loading ?? null}</>;
  }
  if (isEmpty?.(query.data)) {
    return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  }
  return <>{children(query.data)}</>;
}
