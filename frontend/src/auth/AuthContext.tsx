import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, invalidateSessionRequests, setRequestIdentity, tokenStore } from "../lib/api";
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
  cacheIdentity: string;
  login: (email: string, password: string) => Promise<void>;
  register: (
    organization_name: string,
    name: string,
    email: string,
    password: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
  /** Re-read the identity (`/auth/me`) — after a plan activation the served
   * organization status and permissions may have changed (R4 review U-9). */
  refresh: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [org, setOrg] = useState<Organization | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [sessionRevision, setSessionRevision] = useState(0);
  const identityRef = useRef<string | null>(null);

  const applyIdentity = useCallback((data: MeResponse) => {
    const nextPermissions = effectivePermissions(data.user, data.permissions);
    const next = JSON.stringify([data.user.id, data.organization.id, [...nextPermissions].sort()]);
    if (identityRef.current !== null && identityRef.current !== next) invalidateSessionRequests();
    identityRef.current = next;
    setRequestIdentity(data.user.id, data.organization.id);
    setUser(data.user);
    setOrg(data.organization);
    setPermissions(nextPermissions);
  }, []);

  // Restore session on load if a token is present.
  useEffect(() => {
    let active = true;
    const token = tokenStore.get();
    if (!token) {
      setLoading(false);
      return;
    }
    const generation = tokenStore.generation();
    const current = () => active && generation === tokenStore.generation() && token === tokenStore.get();
    api
      .get<MeResponse>("/auth/me")
      .then((r) => {
        if (current()) { applyIdentity(r.data); setLoading(false); }
      })
      .catch(() => {
        if (current()) { tokenStore.clear(); setLoading(false); }
      });
    return () => { active = false; };
  }, [applyIdentity]);

  // Another tab changing the shared credential is also a boundary. Restore the
  // new identity in a fresh document rather than keep the old tab's UI/caches.
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key !== null && event.key !== tokenStore.key) return;
      invalidateSessionRequests();
      setUser(null);
      setOrg(null);
      setPermissions([]);
      setLoading(true);
      setSessionRevision((revision) => revision + 1);
      location.reload();
    };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, []);

  const applyAuth = useCallback(
    (data: AuthResponse) => {
      tokenStore.set(data.token.access_token);
      setSessionRevision((revision) => revision + 1);
      applyIdentity(data);
      setLoading(false);
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
    const token = tokenStore.get();
    // End local ownership immediately, even while revocation is offline/slow.
    // Do not clear again after await: a new account may already have signed in.
    tokenStore.clear();
    identityRef.current = null;
    setUser(null);
    setOrg(null);
    setPermissions([]);
    setLoading(false);
    setSessionRevision((revision) => revision + 1);
    try {
      if (token) await api.post("/auth/logout", null, { headers: { Authorization: `Bearer ${token}` } });
    } catch {
      /* Best-effort revocation; local ownership has already ended. */
    }
  }, []);

  const refresh = useCallback(async () => {
    const r = await api.get<MeResponse>("/auth/me");
    applyIdentity(r.data);
  }, [applyIdentity]);

  const hasPerm = useCallback((perm: string) => permissions.includes(perm), [permissions]);
  const cacheIdentity = JSON.stringify([sessionRevision, user?.id ?? null, org?.id ?? null, [...permissions].sort()]);

  const value = useMemo(
    () => ({ user, org, permissions, hasPerm, loading, cacheIdentity, login, register, logout, refresh }),
    [user, org, permissions, hasPerm, loading, cacheIdentity, login, register, logout, refresh],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
