import { NavLink } from "react-router-dom";

/** The existing intake steps, available to invoice readers. */
export function CaptureProcessNav() {
  return (
    <nav aria-label="Document processing" className="flex flex-wrap gap-2 text-sm">
      {[
        { to: "/upload", label: "Upload document" },
        { to: "/captures", label: "Awaiting review" },
        { to: "/captures/failures", label: "Processing failures" },
      ].map(({ to, label }) => (
        <NavLink key={to} to={to} end
          className={({ isActive }) => `rounded-lg px-3 py-2 font-medium ${isActive ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
