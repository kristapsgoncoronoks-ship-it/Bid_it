import { Link } from "react-router-dom";

// The shared "this module isn't active" notice (was copy-pasted in Expenses and
// Issue). `name` is the human module name, e.g. "employee expenses".
export function ModuleInactive({ name }: { name: string }) {
  return (
    <div className="card text-sm text-slate-600">
      The {name} module isn't active. Activate it in{" "}
      <Link to="/settings" className="font-medium underline">Settings → Modules</Link>.
    </div>
  );
}
