import { useEffect, useState } from "react";
import { tokenStore } from "../lib/api";

// The backend redirects here after a successful SSO login with the issued token
// in the URL fragment (#access_token=...). We store it and hard-navigate to the
// app so the AuthProvider restores the session from the token.
export default function SsoCallback() {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const token = hash.get("access_token");
    // Scrub the token from the visible URL immediately (before storing/navigating)
    // so it can't linger in the address bar or be re-read if navigation is delayed.
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    if (token) {
      tokenStore.set(token);
      window.location.replace("/");
    } else {
      setFailed(true);
    }
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">
      {failed ? (
        <div className="text-center">
          <p className="text-rose-600">Single sign-on failed.</p>
          <a className="text-brand-600 hover:underline" href="/login">Back to sign in</a>
        </div>
      ) : (
        <p>Signing you in…</p>
      )}
    </div>
  );
}
