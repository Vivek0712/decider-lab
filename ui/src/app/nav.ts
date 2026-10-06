import { Activity, Boxes, Database, FlaskConical, LayoutDashboard, Server, Settings, Trophy, type LucideIcon } from "lucide-react";

export type NavItem = { id: string; label: string; to: string; icon: LucideIcon; chord: string; description: string };

// Order and ids are part of the e2e contract (nav-<id>, DESIGN.md 12).
export const NAV: NavItem[] = [
  { id: "overview", label: "Overview", to: "/", icon: LayoutDashboard, chord: "o", description: "What is happening and what came out of it" },
  { id: "labs", label: "Labs", to: "/labs", icon: FlaskConical, chord: "l", description: "Lab files: models, suites, baseline, compute" },
  { id: "jobs", label: "Jobs", to: "/jobs", icon: Activity, chord: "j", description: "Runs, pulls and evals, live" },
  { id: "results", label: "Results", to: "/results", icon: Trophy, chord: "r", description: "Leaderboards with 95% CIs, comparisons, rows" },
  { id: "models", label: "Models", to: "/models", icon: Boxes, chord: "m", description: "Pulled checkpoints and sources" },
  { id: "data", label: "Data", to: "/data", icon: Database, chord: "d", description: "Suites, CSV upload, generate, split, leakcheck" },
  { id: "compute", label: "Compute", to: "/compute", icon: Server, chord: "c", description: "This machine, vast.ai, AWS and SSH hosts" },
];
export const SETTINGS_NAV: NavItem = {
  id: "settings", label: "Settings", to: "/settings", icon: Settings, chord: "s", description: "Workspace, appearance, environment, about",
};
export const ALL_NAV = [...NAV, SETTINGS_NAV];
