import { createBrowserRouter, Navigate, Outlet } from "react-router";
import { RootLayout } from "./layouts/RootLayout";
import { AuthLayout } from "./layouts/AuthLayout";
import { clearAuthToken, getAuthToken } from "./utils/auth";

function decodeJwt(token: string): any {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      window.atob(base64)
        .split('')
        .map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(jsonPayload);
  } catch (e) {
    return null;
  }
}

function isTokenValid(token: string | null): boolean {
  if (!token) return false;
  const payload = decodeJwt(token);
  if (!payload || !payload.exp) return false;
  const now = Math.floor(Date.now() / 1000);
  return payload.exp > now;
}

function ProtectedRoute() {
  const token = getAuthToken();
  const isValid = isTokenValid(token);

  if (!isValid) {
    clearAuthToken();
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
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
        path: "forgot-password",
        lazy: async () => ({ Component: (await import("./pages/ForgotPasswordPage")).ForgotPasswordPage }),
      },
      {
        path: "reset-password",
        lazy: async () => ({ Component: (await import("./pages/ResetPasswordPage")).ResetPasswordPage }),
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
]);
