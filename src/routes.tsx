import { createBrowserRouter, Navigate, Outlet } from "react-router";
import { RootLayout } from "./layouts/RootLayout";
import { AuthLayout } from "./layouts/AuthLayout";

function ProtectedRoute() {
  const isLoggedIn = localStorage.getItem("isLoggedIn") === "true";
  return isLoggedIn ? <Outlet /> : <Navigate to="/login" replace />;
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
