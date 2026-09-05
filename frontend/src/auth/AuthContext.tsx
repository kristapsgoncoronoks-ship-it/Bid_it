import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, tokenStore } from "../lib/api";
import { effectivePermissions } from "../lib/roles";
import type { AuthResponse, MeResponse, Organization, User } from "../lib/types";

interface AuthState {
  user: User | null;
  org: Organization | null;
  /** PROD-003: the caller's effective permissions — served by the API on every
   * identity response, mirrored client-side only as a fallback. Rendering
   * guidance, never a security boundary. */
  permissions: string[];
  hasPerm: (perm: string) => boolean;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (
    organization_name: string,
    name: string,
    email: string,
    password: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [org, setOrg] = useState<Organization | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  const applyIdentity = useCallback((data: MeResponse) => {
    setUser(data.user);
    setOrg(data.organization);
    setPermissions(effectivePermissions(data.user, data.permissions));
  }, []);

  // Restore session on load if a token is present.
  useEffect(() => {
    const token = tokenStore.get();
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .get<MeResponse>("/auth/me")
      .then((r) => applyIdentity(r.data))
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, [applyIdentity]);

  const applyAuth = useCallback(
    (data: AuthResponse) => {
      tokenStore.set(data.token.access_token);
      applyIdentity(data);
    },
    [applyIdentity],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await api.post<AuthResponse>("/auth/login", { email, password });
      applyAuth(r.data);
    },
    [applyAuth],
  );

  const register = useCallback(
    async (organization_name: string, name: string, email: string, password: string) => {
      const r = await api.post<AuthResponse>("/auth/register", {
        organization_name,
        name,
        email,
        password,
      });
      applyAuth(r.data);
    },
    [applyAuth],
  );

  const logout = useCallback(async () => {
    // Revoke the session server-side so the token is truly dead, then clear
    // locally regardless of the network result.
    try {
      await api.post("/auth/logout");
    } catch {
      /* best-effort — always clear locally below */
    }
    tokenStore.clear();
    setUser(null);
    setOrg(null);
    setPermissions([]);
  }, []);

  const hasPerm = useCallback((perm: string) => permissions.includes(perm), [permissions]);

  const value = useMemo(
    () => ({ user, org, permissions, hasPerm, loading, login, register, logout }),
    [user, org, permissions, hasPerm, loading, login, register, logout],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
