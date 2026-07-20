import type { ReactNode } from "react";
import { Switch } from "./Switch";

// An iOS "settings list" row: title + description on the left, a control on the
// right. Use inside a `.card`; stack rows with a thin divider between them.

export function SettingRow({
  title,
  desc,
  checked,
  disabled,
  onChange,
  right,
}: {
  title: string;
  desc?: string;
  checked?: boolean;
  disabled?: boolean;
  onChange?: (v: boolean) => void;
  right?: ReactNode; // custom control; defaults to a Switch when checked/onChange given
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <div className="font-medium text-slate-700">{title}</div>
        {desc && <div className="mt-0.5 text-sm text-slate-500">{desc}</div>}
      </div>
      <div className="mt-0.5 shrink-0">
        {right ?? (
          <Switch checked={!!checked} disabled={disabled} onChange={(v) => onChange?.(v)} label={title} />
        )}
      </div>
    </div>
  );
}
