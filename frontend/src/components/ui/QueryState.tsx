import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";
import { Button } from "./Button";

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
      <EmptyState
        title={errorTitle}
        description="Something went wrong fetching this data."
        action={query.refetch && <Button variant="secondary" size="sm" onClick={() => query.refetch!()}>Retry</Button>}
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
