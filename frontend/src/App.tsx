import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";

// Auth pages load eagerly (first paint); everything behind the app shell is
// code-split so the initial bundle stays small and charts load on demand.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Invoices = lazy(() => import("./pages/Invoices"));
const InvoiceDetail = lazy(() => import("./pages/InvoiceDetail"));
const Upload = lazy(() => import("./pages/Upload"));
const Benchmark = lazy(() => import("./pages/Benchmark"));
const Explore = lazy(() => import("./pages/Explore"));
const Fx = lazy(() => import("./pages/Fx"));
const Review = lazy(() => import("./pages/Review"));
const Settings = lazy(() => import("./pages/Settings"));
const Issuer = lazy(() => import("./pages/Issuer"));
const Issue = lazy(() => import("./pages/Issue"));
const IssuedReports = lazy(() => import("./pages/IssuedReports"));
const Team = lazy(() => import("./pages/Team"));
const Billing = lazy(() => import("./pages/Billing"));
const Expenses = lazy(() => import("./pages/Expenses"));
const ExpenseDetail = lazy(() => import("./pages/ExpenseDetail"));
const EmailIntake = lazy(() => import("./pages/EmailIntake"));
const Budget = lazy(() => import("./pages/Budget"));
const Access = lazy(() => import("./pages/Access"));
const Audit = lazy(() => import("./pages/Audit"));
const Platform = lazy(() => import("./pages/Platform"));

function PageFallback() {
  return <div className="p-8 text-sm text-slate-400">Loading…</div>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route
          path="/*"
          element={
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/explore" element={<Explore />} />
                <Route path="/benchmark" element={<Benchmark />} />
                <Route path="/fx" element={<Fx />} />
                <Route path="/invoices" element={<Invoices />} />
                <Route path="/invoices/:id" element={<InvoiceDetail />} />
                <Route path="/review" element={<Review />} />
                <Route path="/upload" element={<Upload />} />
                <Route path="/email" element={<EmailIntake />} />
                <Route path="/budget" element={<Budget />} />
                <Route path="/issue" element={<Issue />} />
                <Route path="/issue/reports" element={<IssuedReports />} />
                <Route path="/issuer" element={<Issuer />} />
                <Route path="/expenses" element={<Expenses />} />
                <Route path="/expenses/:id" element={<ExpenseDetail />} />
                <Route path="/team" element={<Team />} />
                <Route path="/access" element={<Access />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/billing" element={<Billing />} />
                <Route path="/platform" element={<Platform />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
