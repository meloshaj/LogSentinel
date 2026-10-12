import { createBrowserRouter, Link, Navigate, Outlet, useLocation } from "react-router";
import { useEffect, useState } from "react";
import { RootLayout } from "./layouts/RootLayout";
import { AuthLayout } from "./layouts/AuthLayout";
import {
  clearAuthToken,
  getAuthToken,
  isAuthTokenValid,
  refreshSession,
} from "./utils/auth";

function ProtectedRoute() {
  const token = getAuthToken();
  const isValid = isAuthTokenValid(token);
  const [restoring, setRestoring] = useState(!isValid);

  useEffect(() => {
    if (isValid) return;
    let active = true;
    refreshSession().finally(() => { if (active) setRestoring(false); });
    return () => { active = false; };
  }, [isValid]);

  if (restoring) return <GlobalLoading />;

  if (!isValid) {
    clearAuthToken();
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

function NotFound() {
  const location = useLocation();
  return <main id="main-content" className="min-h-screen bg-[#0d1117] text-[#e6edf3] flex items-center justify-center p-6">
    <div className="max-w-md text-center"><p className="text-sky-400 font-mono">404</p><h1 className="text-2xl font-bold mt-2">Page not found</h1><p className="text-[#8b949e] mt-3">No LogSentinel page exists at {location.pathname}.</p><Link className="inline-block mt-6 text-sky-400 underline" to="/">Return to dashboard</Link></div>
  </main>;
}

function GlobalLoading() {
  return (
    <div className="min-h-screen w-full bg-[#060c18] flex items-center justify-center text-sky-400 font-mono text-xs select-none">
      <div className="flex flex-col items-center gap-3">
        <svg className="animate-spin w-6 h-6 text-sky-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <span>Loading LogSentinel...</span>
      </div>
    </div>
  );
}

export const router = createBrowserRouter([
  // ── Auth routes (no sidebar) ──────────────────────────────────
  {
    path: "/",
    Component: AuthLayout,
    HydrateFallback: GlobalLoading,
    children: [
      {
        path: "login",
        lazy: async () => ({ Component: (await import("./pages/LoginPage")).LoginPage }),
      },
      {
        path: "register",
        lazy: async () => ({ Component: (await import("./pages/RegisterPage")).RegisterPage }),
      },
      {
        path: "verify-email",
        lazy: async () => ({ Component: (await import("./pages/VerifyEmailPage")).VerifyEmailPage }),
      },
      {
        path: "forgot-password",
        lazy: async () => ({ Component: (await import("./pages/ForgotPasswordPage")).ForgotPasswordPage }),
      },
      {
        path: "reset-password",
        lazy: async () => ({ Component: (await import("./pages/ResetPasswordPage")).ResetPasswordPage }),
      },
      {
        path: "terms-of-service",
        lazy: async () => ({ Component: (await import("./pages/TermsOfServicePage")).TermsOfServicePage }),
      },
      {
        path: "privacy-policy",
        lazy: async () => ({ Component: (await import("./pages/PrivacyPolicyPage")).PrivacyPolicyPage }),
      },
    ],
  },
  // ── Dashboard routes (guarded, with sidebar) ──────────────────
  {
    path: "/",
    Component: ProtectedRoute,
    HydrateFallback: GlobalLoading,
    children: [
      {
        path: "",
        Component: RootLayout,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import("./pages/OverviewPage")).OverviewPage }),
          },
          {
            path: "logs",
            lazy: async () => ({ Component: (await import("./pages/LogsPage")).LogsPage }),
          },
          {
            path: "anomalies",
            lazy: async () => ({ Component: (await import("./pages/AnomaliesPage")).AnomaliesPage }),
          },
          {
            path: "ai",
            lazy: async () => ({ Component: (await import("./pages/AIAnalysisPage")).AIAnalysisPage }),
          },
          {
            path: "incidents",
            lazy: async () => ({ Component: (await import("./pages/IncidentsPage")).IncidentsPage }),
          },
          {
            path: "analytics",
            lazy: async () => ({ Component: (await import("./pages/AnalyticsPage")).AnalyticsPage }),
          },
          {
            path: "settings",
            lazy: async () => ({ Component: (await import("./pages/SettingsPage")).SettingsPage }),
          },
        ],
      },
    ],
  },
  { path: "*", Component: NotFound },
]);
