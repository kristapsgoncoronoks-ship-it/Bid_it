import type { ReactNode } from "react";

export type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "info";

const TONES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-600",
  brand: "bg-brand-50 text-brand-700",
  success: "bg-emerald-100 text-emerald-700",
  warning: "bg-amber-100 text-amber-700",
  danger: "bg-rose-100 text-rose-700",
  info: "bg-sky-100 text-sky-700",
};

export function Badge({ tone = "neutral", children, className = "" }:
  { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${TONES[tone]} ${className}`}>
      {children}
    </span>
  );
}

// Small optional colored dot for status rows (paired with a label).
export function Dot({ tone = "neutral" }: { tone?: Tone }) {
  const BG: Record<Tone, string> = {
    neutral: "bg-slate-400", brand: "bg-brand-500", success: "bg-emerald-500",
    warning: "bg-amber-500", danger: "bg-rose-500", info: "bg-sky-500",
  };
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${BG[tone]}`} aria-hidden="true" />;
}
