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
import type { AuthResponse, Organization, User } from "../lib/types";

interface AuthState {
  user: User | null;
  org: Organization | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (
    organization_name: string,
    name: string,
    email: string,
    password: string,
  ) => Promise<void>;
  logout: () => void;
}

const AuthCtx = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [org, setOrg] = useState<Organization | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore session on load if a token is present.
  useEffect(() => {
    const token = tokenStore.get();
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .get("/auth/me")
      .then((r) => {
        setUser(r.data.user);
        setOrg(r.data.organization);
      })
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  const applyAuth = useCallback((data: AuthResponse) => {
    tokenStore.set(data.token.access_token);
    setUser(data.user);
    setOrg(data.organization);
  }, []);

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

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
    setOrg(null);
  }, []);

  const value = useMemo(
    () => ({ user, org, loading, login, register, logout }),
    [user, org, loading, login, register, logout],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
