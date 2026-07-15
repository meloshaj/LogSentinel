import { createBrowserRouter, Navigate, Outlet } from "react-router";
import { RootLayout } from "./layouts/RootLayout";
import { AuthLayout } from "./layouts/AuthLayout";

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
  const token = localStorage.getItem("authToken");
  const isValid = isTokenValid(token);

  if (!isValid) {
    localStorage.removeItem("isLoggedIn");
    localStorage.removeItem("authToken");
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

export const router = createBrowserRouter([
  // ── Auth routes (no sidebar) ──────────────────────────────────
  {
    path: "/",
    Component: AuthLayout,
    children: [
      {
        path: "login",
        lazy: async () => ({ Component: (await import("./pages/LoginPage")).LoginPage }),
      },
      {
        path: "register",
        lazy: async () => ({ Component: (await import("./pages/RegisterPage")).RegisterPage }),
      },
    ],
  },
  // ── Dashboard routes (guarded, with sidebar) ──────────────────
  {
    path: "/",
    Component: ProtectedRoute,
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
