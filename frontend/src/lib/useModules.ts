import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { ModuleInfo } from "./types";

// One place to read the org's activated modules. React Query dedupes the network
// call by key; this also dedupes the "which are enabled" derivation that was
// re-implemented in Layout, Expenses, Issue and Settings.
export function useModules() {
  const q = useQuery<ModuleInfo[]>({
    queryKey: ["modules"],
    queryFn: async () => (await api.get("/modules")).data,
  });
  const enabled = new Set((q.data ?? []).filter((m) => m.enabled).map((m) => m.key));
  return {
    data: q.data,
    isLoading: q.isLoading,
    isEnabled: (key: string) => enabled.has(key),
  };
}
