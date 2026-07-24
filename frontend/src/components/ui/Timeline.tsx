import type { ReactNode } from "react";
import { type Tone } from "./Badge";

export interface TimelineEvent {
  id: string;
  /** Short action label, e.g. "Invoice approved". */
  title: ReactNode;
  /** Who did it. */
  actor?: ReactNode;
  /** Absolute timestamp text (already formatted). */
  timestamp: ReactNode;
  /** Optional detail line / diff / note. */
  detail?: ReactNode;
  /** Dot colour by semantic meaning. */
  tone?: Tone;
}

const DOT: Record<Tone, string> = {
  neutral: "bg-slate-300",
  brand: "bg-brand-500",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  info: "bg-sky-500",
};

/**
 * Audit-history timeline. A semantic ordered list (newest first is the caller's
 * choice) with a connecting rail, a status dot per event, and actor + timestamp
 * metadata. Read-only; pair it with the immutable audit trail or a record's change
 * log. Each row is plain text for screen readers — the rail/dot are decorative.
 */
export function Timeline({ events, className = "" }: { events: TimelineEvent[]; className?: string }) {
  return (
    <ol className={`relative ml-2 border-l border-slate-200 ${className}`}>
      {events.map((e) => (
        <li key={e.id} className="relative py-3 pl-6">
          <span
            className={`absolute -left-[5px] top-4 h-2.5 w-2.5 rounded-full ring-4 ring-white ${DOT[e.tone ?? "neutral"]}`}
            aria-hidden="true"
          />
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
            <p className="text-sm font-medium text-slate-700">{e.title}</p>
            <time className="text-xs text-slate-400 tabular-nums">{e.timestamp}</time>
          </div>
          {e.actor && <p className="mt-0.5 text-xs text-slate-500">{e.actor}</p>}
          {e.detail && <div className="mt-1 text-sm text-slate-500">{e.detail}</div>}
        </li>
      ))}
    </ol>
  );
}
