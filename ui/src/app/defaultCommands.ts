import { Copy, Keyboard, Monitor, Moon, PanelLeft, Plus, Sun, SunMoon } from "lucide-react";
import type { ThemePref } from "@/api/types";
import type { Command } from "@/palette/registry";
import { ALL_NAV } from "./nav";

/**
 * The shell's built-in commands. Area pages override ids they implement better (for example a
 * dialog for "model.pull") by registering the same id with useRegisterCommands.
 */
export function defaultCommands(opts: {
  setTheme: (p: ThemePref) => void;
  cycleTheme: () => void;
  toggleSidebar: () => void;
  showShortcuts: () => void;
  copyWorkspace: () => void;
}): Command[] {
  const nav: Command[] = ALL_NAV.map((n) => ({
    id: `nav.${n.id}`,
    label: `Go to ${n.label}`,
    detail: n.description,
    section: "Navigation",
    icon: n.icon,
    shortcut: ["g", n.chord],
    href: n.to,
    keywords: [n.label],
  }));
  const go = (id: string, label: string, href: string, keywords: string[] = []): Command => ({
    id,
    label,
    section: "Commands",
    href,
    keywords,
  });
  return [
    ...nav,
    { ...go("lab.new", "New lab…", "/labs/new", ["create", "template", "init"]), icon: Plus, shortcut: ["n"] },
    go("lab.run", "Run lab…", "/labs", ["start"]),
    go("eval.quick", "Quick eval…", "/?quick-eval=1", ["evaluate", "url", "baseline"]),
    go("results.compare", "Compare two runs…", "/results/compare", ["diff", "paired"]),
    go("model.pull", "Pull a model…", "/models?tab=pull", ["download", "hf", "s3", "checkpoint"]),
    go("data.uploadCsv", "Upload CSV…", "/data?tab=csv", ["import", "rows"]),
    go("data.generate", "Generate training rows…", "/data?tab=generate"),
    go("data.split", "Split dev/test…", "/data?tab=split"),
    go("data.leakcheck", "Leakcheck training data…", "/data?tab=leakcheck", ["overlap", "contamination"]),
    go("compute.doctor", "Check this machine (doctor)", "/compute?tab=local", ["doctor", "gpu", "install"]),
    go("compute.vastOffers", "Find vast.ai offers…", "/compute?tab=vast", ["gpu", "rent"]),
    go("compute.awsIdentity", "Show AWS identity", "/compute?tab=aws", ["account", "profile"]),
    go("compute.sshAdd", "Add SSH host…", "/compute?tab=ssh&add=1", ["machine", "server"]),
    { id: "theme.toggle", label: "Toggle theme", section: "Commands", icon: SunMoon, shortcut: ["t"], run: () => opts.cycleTheme() },
    { id: "theme.dark", label: "Theme: dark", section: "Commands", icon: Moon, run: () => opts.setTheme("dark") },
    { id: "theme.light", label: "Theme: light", section: "Commands", icon: Sun, run: () => opts.setTheme("light") },
    { id: "theme.system", label: "Theme: system", section: "Commands", icon: Monitor, run: () => opts.setTheme("system") },
    { id: "ui.shortcuts", label: "Keyboard shortcuts", section: "Commands", icon: Keyboard, shortcut: ["?"], run: () => opts.showShortcuts() },
    { id: "ui.sidebar", label: "Toggle sidebar", section: "Commands", icon: PanelLeft, shortcut: ["mod", "\\"], run: () => opts.toggleSidebar() },
    { id: "ui.copyWorkspace", label: "Copy workspace path", section: "Commands", icon: Copy, run: () => opts.copyWorkspace() },
  ];
}
