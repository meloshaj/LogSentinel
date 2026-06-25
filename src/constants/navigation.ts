import type { ElementType } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart2,
  Bell,
  Brain,
  Home,
  Settings,
} from "lucide-react";

export interface NavigationItem {
  to: string;
  icon: ElementType;
  label: string;
  badge?: number;
}

export const PRIMARY_NAV_ITEMS: NavigationItem[] = [
  { to: "/", icon: Home, label: "Overview" },
  { to: "/logs", icon: Activity, label: "Live Logs" },
  { to: "/anomalies", icon: AlertTriangle, label: "Anomalies", badge: 4 },
  { to: "/ai", icon: Brain, label: "AI Analysis" },
  { to: "/incidents", icon: Bell, label: "Incidents", badge: 2 },
  { to: "/analytics", icon: BarChart2, label: "Analytics" },
];

export const SECONDARY_NAV_ITEMS: NavigationItem[] = [
  { to: "/settings", icon: Settings, label: "Settings" },
];
