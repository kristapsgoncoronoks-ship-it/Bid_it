import { FormField, inputClass } from "./Form";
import type { ReactNode } from "react";

const SYMBOLS: Record<string, string> = { EUR: "€", USD: "$", GBP: "£", CHF: "CHF", PLN: "zł", SEK: "kr" };

function symbolFor(currency: string): string {
  return SYMBOLS[currency] ?? currency;
}

/** Keep only digits and a single decimal point (finance amounts are non-negative). */
function sanitize(raw: string): string {
  let out = raw.replace(/[^\d.]/g, "");
  const firstDot = out.indexOf(".");
  if (firstDot >= 0) {
    out = out.slice(0, firstDot + 1) + out.slice(firstDot + 1).replace(/\./g, "");
  }
  return out;
}

export interface CurrencyInputProps {
  label: ReactNode;
  value: string;
  onValueChange: (value: string) => void;
  currency?: string;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
  name?: string;
  className?: string;
}

/**
 * Money input. Right-aligned tabular figures with a leading currency symbol, a
 * numeric keypad on mobile (`inputMode="decimal"`), and input sanitised to a
 * non-negative decimal so a stray letter can't corrupt an amount. The value is a
 * plain numeric string ("1234.50") — the caller converts/quantises at submit
 * (never trust the browser to round money). Currency is announced via the hint.
 */
export function CurrencyInput({
  label, value, onValueChange, currency = "EUR",
  hint, error, required, disabled, placeholder = "0.00", name, className,
}: CurrencyInputProps) {
  return (
    <FormField
      label={label}
      hint={hint ?? `Amount in ${currency}`}
      error={error}
      required={required}
      className={className}
    >
      {(f) => (
        <div className="relative">
          <span
            className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-sm font-medium text-slate-400"
            aria-hidden="true"
          >
            {symbolFor(currency)}
          </span>
          <input
            {...f}
            name={name}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            disabled={disabled}
            placeholder={placeholder}
            value={value}
            onChange={(e) => onValueChange(sanitize(e.target.value))}
            className={inputClass(!!error, "pl-8 text-right tabular-nums")}
          />
        </div>
      )}
    </FormField>
  );
}
