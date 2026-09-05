import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { loginPathFor } from "../lib/api";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const { pathname, search } = useLocation();
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-slate-400">
        Loading…
      </div>
    );
  }
  // FE-002: remember where the person was heading so sign-in returns them there.
  if (!user) return <Navigate to={loginPathFor(pathname, search)} replace />;
  return <>{children}</>;
}
