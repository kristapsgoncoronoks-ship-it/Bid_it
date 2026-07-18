import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Invoices from "./pages/Invoices";
import InvoiceDetail from "./pages/InvoiceDetail";
import Upload from "./pages/Upload";
import Benchmark from "./pages/Benchmark";
import Fx from "./pages/Fx";
import Review from "./pages/Review";
import Settings from "./pages/Settings";
import Issuer from "./pages/Issuer";
import Issue from "./pages/Issue";
import Team from "./pages/Team";
import Billing from "./pages/Billing";
import Platform from "./pages/Platform";
import AcceptInvite from "./pages/AcceptInvite";

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
        <Route path="/" element={<Dashboard />} />
        <Route path="/benchmark" element={<Benchmark />} />
        <Route path="/fx" element={<Fx />} />
        <Route path="/invoices" element={<Invoices />} />
        <Route path="/invoices/:id" element={<InvoiceDetail />} />
        <Route path="/review" element={<Review />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/issue" element={<Issue />} />
        <Route path="/issuer" element={<Issuer />} />
        <Route path="/team" element={<Team />} />
        <Route path="/billing" element={<Billing />} />
        <Route path="/platform" element={<Platform />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
