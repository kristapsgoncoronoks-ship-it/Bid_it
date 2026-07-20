import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Kind = "error" | "success";
type ToastItem = { id: number; message: string; kind: Kind };
type ToastApi = { error: (m: string) => void; success: (m: string) => void };

const Ctx = createContext<ToastApi | null>(null);
let _seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((message: string, kind: Kind) => {
    const id = ++_seq;
    setItems((xs) => [...xs, { id, message, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5000);
  }, []);

  const api: ToastApi = {
    error: (m) => push(m, "error"),
    success: (m) => push(m, "success"),
  };

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role="alert"
            className={`pointer-events-auto flex items-start gap-2 rounded-lg px-4 py-3 text-sm shadow-lg ${
              t.kind === "error" ? "bg-rose-600 text-white" : "bg-emerald-600 text-white"
            }`}
            onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}
          >
            <span className="flex-1">{t.message}</span>
            <button className="opacity-70 hover:opacity-100" aria-label="Dismiss">×</button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}
