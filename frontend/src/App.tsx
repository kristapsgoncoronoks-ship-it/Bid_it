import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import SsoCallback from "./pages/SsoCallback";
import AcceptInvite from "./pages/AcceptInvite";
import VerifyEmail from "./pages/VerifyEmail";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";

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
const Partners = lazy(() => import("./pages/Partners"));
const Team = lazy(() => import("./pages/Team"));
const Billing = lazy(() => import("./pages/Billing"));
const Expenses = lazy(() => import("./pages/Expenses"));
const ExpenseDetail = lazy(() => import("./pages/ExpenseDetail"));
const EmailIntake = lazy(() => import("./pages/EmailIntake"));
const Budget = lazy(() => import("./pages/Budget"));
const Access = lazy(() => import("./pages/Access"));
const Sessions = lazy(() => import("./pages/Sessions"));
const Audit = lazy(() => import("./pages/Audit"));
const Platform = lazy(() => import("./pages/Platform"));

// Design-system showcase (public, fixtures-only). Lives under /design so the
// living style guide + shell demo can be reviewed and visual/e2e-tested without a
// login or a backend. See docs/DESIGN_SYSTEM.md.
const DesignLayout = lazy(() => import("./design/DesignLayout").then((m) => ({ default: m.DesignLayout })));
const Gallery = lazy(() => import("./design/Gallery"));
const DsDashboard = lazy(() => import("./design/routes/Dashboard"));
const DsSupplierInvoices = lazy(() => import("./design/routes/SupplierInvoices"));
const DsCustomerInvoices = lazy(() => import("./design/routes/CustomerInvoices"));
const DsExpenses = lazy(() => import("./design/routes/Expenses"));
const DsPayments = lazy(() => import("./design/routes/Payments"));
const DsReports = lazy(() => import("./design/routes/Reports"));
const DsContacts = lazy(() => import("./design/routes/Contacts"));
const DsSettings = lazy(() => import("./design/routes/Settings"));
const DsAdministration = lazy(() => import("./design/routes/Administration"));

function PageFallback() {
  return <div className="p-8 text-sm text-slate-400">Loading…</div>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/sso/callback" element={<SsoCallback />} />
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />

      {/* Design-system showcase — public, fixtures-only (no auth, no backend). */}
      <Route
        path="/design/gallery"
        element={
          <Suspense fallback={<PageFallback />}>
            <Gallery />
          </Suspense>
        }
      />
      <Route
        path="/design"
        element={
          <Suspense fallback={<PageFallback />}>
            <DesignLayout />
          </Suspense>
        }
      >
        <Route index element={<DsDashboard />} />
        <Route path="supplier-invoices" element={<DsSupplierInvoices />} />
        <Route path="customer-invoices" element={<DsCustomerInvoices />} />
        <Route path="expenses" element={<DsExpenses />} />
        <Route path="payments" element={<DsPayments />} />
        <Route path="reports" element={<DsReports />} />
        <Route path="contacts" element={<DsContacts />} />
        <Route path="settings" element={<DsSettings />} />
        <Route path="administration" element={<DsAdministration />} />
      </Route>
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
                <Route path="/partners" element={<Partners />} />
                <Route path="/issuer" element={<Issuer />} />
                <Route path="/expenses" element={<Expenses />} />
                <Route path="/expenses/:id" element={<ExpenseDetail />} />
                <Route path="/team" element={<Team />} />
                <Route path="/access" element={<Access />} />
                <Route path="/sessions" element={<Sessions />} />
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
