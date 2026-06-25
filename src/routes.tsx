import { createBrowserRouter } from "react-router";
import { RootLayout } from "./layouts/RootLayout";

export const router = createBrowserRouter([
  {
    path: "/",
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
]);
