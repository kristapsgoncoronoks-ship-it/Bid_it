// An iOS-style on/off switch — green when on, a smooth sliding knob, accessible.
// Reused across the admin panel so every on/off control looks and feels the same.

interface Props {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;              // accessible name
  size?: "md" | "sm";
}

export function Switch({ checked, onChange, disabled = false, label, size = "md" }: Props) {
  const dims =
    size === "sm"
      ? { track: "h-5 w-9", knob: "h-4 w-4", on: "translate-x-[18px]", off: "translate-x-0.5" }
      : { track: "h-6 w-11", knob: "h-5 w-5", on: "translate-x-[22px]", off: "translate-x-0.5" };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex ${dims.track} shrink-0 items-center rounded-full transition-colors duration-200 ease-in-out outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 ${
        checked ? "bg-[#34C759]" : "bg-slate-300"
      } ${disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}
    >
      <span
        className={`inline-block ${dims.knob} transform rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,0.25)] ring-0 transition-transform duration-200 ease-in-out ${
          checked ? dims.on : dims.off
        }`}
      />
    </button>
  );
}
