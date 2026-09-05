import axios from "axios";

const TOKEN_KEY = "invoiceiq_token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export const api = axios.create({
  baseURL: (import.meta.env.VITE_API_BASE_URL || "") + "/api/v1",
});

// Attach the bearer token to every request.
api.interceptors.request.use((config) => {
  const token = tokenStore.get();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// FE-002 (audit 2026-09-05): the screens a signed-OUT person uses. A 401 on
// one of these is the page's own business — an expired invitation, a used
// reset token, a stale verification link — and the server's message is the
// whole point of the response. Bouncing to /login here threw that message
// away and dropped the user mid-flow. Keep in step with the public routes in
// App.tsx.
export const PUBLIC_PATHS = [
  "/login",
  "/accept-invite",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
  "/sso/callback",
  "/portal/",
  "/design",
] as const;

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => (p.endsWith("/") ? pathname.startsWith(p) : pathname === p || pathname.startsWith(p + "/")));
}

// Where to send someone back after they sign in: the path they were on, kept
// only when it is a same-origin relative path (`/x`, never `//evil` or a full
// URL — this must not become an open redirect) and not a public page.
export function loginPathFor(pathname: string, search = ""): string {
  const target = pathname + search;
  if (!pathname.startsWith("/") || pathname.startsWith("//") || pathname === "/" || isPublicPath(pathname)) {
    return "/login";
  }
  return `/login?next=${encodeURIComponent(target)}`;
}

export function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || /^\/[^/]*:/.test(raw)) return "/";
  const pathOnly = raw.split(/[?#]/)[0];
  return isPublicPath(pathOnly) ? "/" : raw;
}

// On 401, drop the token and bounce to login — carrying the path the user was
// on so they land back there (`?next=`), and NOT on a public page (see above).
//
// PROD-001 (audit 2026-09-05): a SUSPENDED workspace is the one 401 that is not
// "your credential is dead". The identity read and Plan & billing still answer
// for it, so the shell renders its suspended mode and the owner can fix the
// card; every data route 401s with `code: organization_suspended`. Logging the
// user out on that code would throw away the one screen that restores access —
// send them to it instead, and keep the token.
api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401) {
      if (error?.response?.data?.code === "organization_suspended") {
        if (location.pathname !== "/billing") location.assign("/billing");
        return Promise.reject(error);
      }
      if (isPublicPath(location.pathname)) return Promise.reject(error);
      tokenStore.clear();
      location.assign(loginPathFor(location.pathname, location.search));
    }
    return Promise.reject(error);
  },
);

export async function downloadFile(path: string, filename: string): Promise<void> {
  try {
    const res = await api.get(path, { responseType: "blob" });
    const url = URL.createObjectURL(res.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    // With responseType "blob" an error body arrives as a Blob, hiding the
    // {"detail","code"} shape from apiError/apiErrorCode. Re-inflate it so
    // callers can branch on the machine code (e.g. WO-9's already_exported /
    // skipped_payees confirm flows).
    if (axios.isAxiosError(e) && e.response && e.response.data instanceof Blob) {
      try {
        e.response.data = JSON.parse(await e.response.data.text());
      } catch {
        // Not JSON — leave the original error untouched.
      }
    }
    throw e;
  }
}

// The stable machine `code` slug from an API error body, if present.
export function apiErrorCode(e: unknown): string | null {
  if (axios.isAxiosError(e)) {
    const code = (e.response?.data as { code?: unknown } | undefined)?.code;
    if (typeof code === "string") return code;
  }
  return null;
}

// Open an authenticated file (e.g. a PDF) in a new browser tab. A raw <a href>
// can't carry the JWT header, so fetch the blob via axios and open an object URL.
export async function openFile(path: string): Promise<void> {
  const res = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(res.data as Blob);
  window.open(url, "_blank", "noopener");
  // Revoke a little later so the new tab has time to load it.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function apiError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = e.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
    return e.message;
  }
  if (e instanceof Error && e.message) return e.message;
  return "Unexpected error";
}

// True for genuine failures the user should be told about globally: no response
// (network/offline) or a 5xx. 4xx are business responses handled per-page (auth,
// validation, module gating) and must NOT raise a global toast.
export function isUnexpectedError(e: unknown): boolean {
  if (!axios.isAxiosError(e)) return true;
  const status = e.response?.status;
  return status === undefined || status >= 500;
}
