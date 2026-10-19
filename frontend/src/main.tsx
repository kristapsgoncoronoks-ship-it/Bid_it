import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { useEffect, useState, type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ToastProvider, toast } from "./components/Toast";
import { apiError, isCanceledRequest, isUnexpectedError } from "./lib/api";
import "./index.css";

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // FE-019 (audit 2026-09-05): retry a genuine failure (network, 5xx)
        // once; never a 4xx — a 403/404/409/422 is the answer, and asking again
        // only delays the error state the page is about to render.
        retry: (count, err) => count < 1 && isUnexpectedError(err),
      },
    },
    // Surface genuine read failures (network / 5xx) globally; per-page logic still
    // handles expected 4xx (auth, validation, module gating).
    queryCache: new QueryCache({
      onError: (err, query) => {
        // Pages with a complete, sanitized error surface own their read errors.
        if (query.meta?.errorHandled === true) return;
        if (isUnexpectedError(err)) toast.error(apiError(err));
      },
    }),
    // FE-001 (audit 2026-09-05): a mutation that fails with no `onError` of its
    // own used to fail SILENTLY — the button un-busied, nothing else happened
    // (21 such mutations shipped: revoke session, remove transaction, delete
    // draft…). This backstop toasts the server's message for exactly those.
    // Mutations that declare `onError` keep their own surface and are not
    // double-reported.
    // R2-B2: a mutation that renders its own `error` inline opts out with
    // `meta: { silent: true }`, so the message is not shown twice.
    mutationCache: new MutationCache({
      onError: (err, _vars, _ctx, mutation) => {
        if (isCanceledRequest(err)) return;
        if (mutation.options.onError || mutation.options.meta?.silent) return;
        toast.error(apiError(err));
      },
    }),
  });
}

function OwnedQueryClient({ children }: { children: ReactNode }) {
  const [client] = useState(createQueryClient);
  useEffect(() => () => {
    void client.cancelQueries();
    client.clear(); // also removes mutation state; late callbacks own only this retired client
  }, [client]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function IdentityQueries({ children }: { children: ReactNode }) {
  const { cacheIdentity } = useAuth();
  return <OwnedQueryClient key={cacheIdentity}>{children}</OwnedQueryClient>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <IdentityQueries>
          <ToastProvider>
            <App />
          </ToastProvider>
        </IdentityQueries>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
