import { FormField, inputClass } from "./Form";
import { cx } from "../../lib/cx";
import type { ReactNode } from "react";

/** Clamp to a sane VAT range and keep at most one decimal point. */
function sanitize(raw: string): string {
  let out = raw.replace(/[^\d.]/g, "");
  const dot = out.indexOf(".");
  if (dot >= 0) out = out.slice(0, dot + 1) + out.slice(dot + 1).replace(/\./g, "");
  return out;
}

export interface TaxRateInputProps {
  label: ReactNode;
  /** Percentage as a string, e.g. "21" or "9.5". */
  value: string;
  onValueChange: (value: string) => void;
  /** Quick-pick common rates (e.g. local VAT bands). Rendered as buttons. */
  presets?: number[];
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  name?: string;
  className?: string;
}

/**
 * Tax-rate (VAT %) input. A trailing "%" adornment, numeric keypad on mobile, and
 * optional preset chips for the jurisdiction's common bands (e.g. 0 / 9 / 21) so a
 * bookkeeper picks the right rate in one tap instead of typing. Value is a plain
 * numeric string ("21"); the caller converts to a fraction where needed.
 */
export function TaxRateInput({
  label, value, onValueChange, presets, hint, error, required, disabled, name, className,
}: TaxRateInputProps) {
  return (
    <FormField label={label} hint={hint} error={error} required={required} className={className}>
      {(f) => (
        <div className="flex flex-col gap-2">
          <div className="relative">
            <input
              {...f}
              name={name}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              disabled={disabled}
              placeholder="0"
              value={value}
              onChange={(e) => onValueChange(sanitize(e.target.value))}
              className={inputClass(!!error, "pr-8 text-right tabular-nums")}
            />
            <span
              className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3 text-sm font-medium text-slate-400"
              aria-hidden="true"
            >
              %
            </span>
          </div>
          {presets && presets.length > 0 && (
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Common rates">
              {presets.map((p) => {
                const selected = value === String(p);
                return (
                  <button
                    key={p}
                    type="button"
                    disabled={disabled}
                    aria-pressed={selected}
                    onClick={() => onValueChange(String(p))}
                    className={cx(
                      "rounded-full border px-2.5 py-0.5 text-xs font-medium transition",
                      "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300",
                      selected
                        ? "border-brand-500 bg-brand-50 text-brand-700"
                        : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50",
                    )}
                  >
                    {p}%
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </FormField>
  );
}
