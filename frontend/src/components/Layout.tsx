import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isSysadmin } from "../lib/roles";
import { useModules } from "../lib/useModules";

// `module` marks an item that only shows when that add-on module is enabled.
const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/explore", label: "Explore", end: false },
  { to: "/benchmark", label: "Benchmark", end: false },
  { to: "/fx", label: "FX", end: false },
  { to: "/invoices", label: "Invoices", end: false },
  { to: "/review", label: "Review", end: false },
  { to: "/upload", label: "Upload", end: false },
  { to: "/email", label: "Email intake", end: false, module: "email_intake" },
  { to: "/budget", label: "Budget", end: false, module: "budget" },
  { to: "/issue", label: "Issue", end: false, module: "issuing" },
  { to: "/expenses", label: "Expenses", end: false, module: "expenses" },
  { to: "/team", label: "Team", end: false },
  { to: "/access", label: "Access", end: false, sysadmin: true },
  { to: "/billing", label: "Billing", end: false },
  { to: "/settings", label: "Settings", end: false },
];

export function Layout() {
  const { user, org, logout } = useAuth();
  const navigate = useNavigate();
  const { isEnabled } = useModules();
  const nav = NAV.filter((n) => (!n.module || isEnabled(n.module)) && (!("sysadmin" in n && n.sysadmin) || isSysadmin(user)));
  if (user?.is_platform_admin) {
    nav.push({ to: "/platform", label: "Platform", end: false });
  }
  const suspended = org?.status && org.status !== "active";

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2">
              <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500 text-sm font-bold text-white">
                iQ
              </div>
              <span className="text-lg font-semibold tracking-tight">InvoiceIQ</span>
            </div>
            <nav className="flex items-center gap-1">
              {nav.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) =>
                    `rounded-lg px-3 py-1.5 text-sm font-medium ${
                      isActive
                        ? "bg-brand-50 text-brand-700"
                        : "text-slate-600 hover:bg-slate-100"
                    }`
                  }
                >
                  {n.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right text-sm">
              <div className="font-medium text-slate-700">{org?.name}</div>
              <div className="text-xs text-slate-400">{user?.email}</div>
            </div>
            <button
              className="btn-ghost"
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      {suspended && (
        <div className="bg-rose-600 px-4 py-2 text-center text-sm font-medium text-white">
          This workspace is {org?.status}. Some actions are disabled — please contact support or update billing.
        </div>
      )}
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
