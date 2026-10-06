import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Menu, Monitor, Moon, PanelLeftClose, PanelLeftOpen, RotateCw, Search, Sun, WifiOff } from "lucide-react";
import { listJobs, globalEvents, jobKeys } from "@/api/jobs";
import { ApiError, onUnauthorized } from "@/lib/api";
import { cn } from "@/lib/cn";
import { truncateMiddle } from "@/lib/format";
import { useMeta } from "@/hooks/useMeta";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useIsCompact } from "@/hooks/useMediaQuery";
import { useLocalStorage } from "@/hooks/useLocalStorage";
import { useEventSource } from "@/hooks/useEventSource";
import { useTheme } from "@/theme/ThemeProvider";
import { Button, IconButton } from "@/components/Button";
import { useCopy } from "@/components/Code";
import { Kbd } from "@/components/Kbd";
import { Drawer } from "@/components/Overlay";
import { ProgressBar } from "@/components/ProgressBar";
import { CommandPalette } from "@/palette/CommandPalette";
import { ShortcutHelp } from "@/palette/ShortcutHelp";
import { registry } from "@/palette/registry";
import { defaultCommands } from "./defaultCommands";
import { ALL_NAV, NAV, SETTINGS_NAV, type NavItem } from "./nav";

function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--brand)" />
      <rect x="11" y="11" width="10" height="10" fill="#2FDC85" transform="rotate(45 16 16)" />
    </svg>
  );
}

function SessionExpired() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4" data-testid="session-expired">
      <main className="w-full max-w-[520px] rounded-lg border border-border bg-surface-1 p-7 shadow-elev-2">
        <div className="flex items-center gap-3">
          <Mark className="h-7 w-7" />
          <h1 className="text-h2">Session expired</h1>
        </div>
        <p className="mt-3 text-body text-muted">
          This Studio session is no longer valid, usually because <code className="font-mono">decider-lab ui</code> was restarted with a new
          token. Open the link it printed in your terminal.
        </p>
      </main>
    </div>
  );
}

function SidebarNav({ collapsed, activeJobs, onNavigate }: { collapsed: boolean; activeJobs: number; onNavigate?: () => void }) {
  const item = (n: NavItem) => (
    <NavLink
      key={n.id}
      to={n.to}
      end={n.to === "/"}
      data-testid={`nav-${n.id}`}
      onClick={onNavigate}
      title={collapsed ? n.label : undefined}
      aria-label={collapsed ? n.label : undefined}
      className={({ isActive }) =>
        cn(
          "focus-inset group flex h-10 items-center gap-3 rounded-sm px-3 text-body font-medium no-underline transition-colors duration-fast max-md:h-11",
          isActive ? "bg-surface-3 text-text" : "text-muted hover:bg-surface-2 hover:text-text",
          collapsed && "justify-center px-0",
        )
      }
    >
      {({ isActive }) => (
        <>
          <n.icon size={20} strokeWidth={1.75} aria-hidden className={cn(isActive ? "text-accent" : "text-subtle group-hover:text-muted")} />
          {!collapsed && <span className="min-w-0 flex-1 truncate">{n.label}</span>}
          {!collapsed && n.id === "jobs" && activeJobs > 0 && (
            <span className="tnum rounded-full bg-tint-accent px-2 text-caption text-accent" aria-label={`${activeJobs} active`}>
              {activeJobs}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
  return (
    <div className="flex h-full flex-col gap-1 p-3">
      {NAV.map(item)}
      <div className="my-2 border-t border-border" />
      {item(SETTINGS_NAV)}
    </div>
  );
}

function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  const meta = useMeta();
  if (collapsed) return null;
  return (
    <div className="flex flex-col gap-2 border-t border-border p-4">
      {meta.data?.fake_cloud && (
        <span
          data-testid="fake-cloud-pill"
          title="Cloud calls return fixtures. Nothing is created or billed."
          className="inline-flex w-fit items-center rounded-full border border-warning px-2.5 py-0.5 text-caption font-semibold uppercase tracking-wide text-warning"
        >
          Fake cloud
        </span>
      )}
      <span className="text-caption text-subtle">decider-lab v{meta.data?.decider_lab_version ?? "…"}</span>
    </div>
  );
}

export function AppShell() {
  const meta = useMeta();
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const compact = useIsCompact();
  const [collapsed, setCollapsed] = useLocalStorage("dl-sidebar-collapsed", false);
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const [expired, setExpired] = useState(false);
  const [copy] = useCopy();

  useEffect(() => onUnauthorized(() => setExpired(true)), []);
  useEffect(() => setDrawer(false), [location.pathname]);

  // Active jobs badge. Until the Jobs area serves /api/jobs, this quietly shows nothing.
  const jobs = useQuery({
    queryKey: jobKeys.list({ status: "active" }),
    queryFn: () => listJobs({ status: "active" }),
    refetchInterval: (q) => (q.state.status === "error" ? false : 5000),
    retry: false,
  });
  const active = jobs.data?.items ?? [];
  useEventSource(
    globalEvents,
    {
      "job.created": () => qc.invalidateQueries({ queryKey: ["jobs"] }),
      "job.updated": () => qc.invalidateQueries({ queryKey: ["jobs"] }),
      "job.finished": () => {
        qc.invalidateQueries({ queryKey: ["jobs"] });
        qc.invalidateQueries({ queryKey: ["overview"] });
      },
      "labs.changed": () => qc.invalidateQueries({ queryKey: ["labs"] }),
      "runs.changed": () => qc.invalidateQueries({ queryKey: ["runs"] }),
      "models.changed": () => qc.invalidateQueries({ queryKey: ["models"] }),
    },
    { enabled: jobs.isSuccess, deps: [jobs.isSuccess] },
  );

  const toggleSidebar = useCallback(() => (compact ? setDrawer((d) => !d) : setCollapsed(!collapsed)), [compact, collapsed, setCollapsed]);
  const copyWorkspace = useCallback(() => meta.data && void copy(meta.data.workspace), [meta.data, copy]);

  useEffect(
    () =>
      registry.register(
        "shell",
        defaultCommands({ setTheme: theme.setPref, cycleTheme: theme.cycle, toggleSidebar, showShortcuts: () => setHelp(true), copyWorkspace }),
      ),
    [theme.setPref, theme.cycle, toggleSidebar, copyWorkspace],
  );

  const hotkeys = useMemo(
    () => [
      { keys: "mod+k", handler: () => setPalette((p) => !p), allowInInput: true },
      { keys: "mod+\\", handler: toggleSidebar, allowInInput: true },
      { keys: "?", handler: () => setHelp(true) },
      { keys: "t", handler: () => theme.cycle() },
      {
        keys: "/",
        handler: () => {
          const el = document.querySelector<HTMLElement>("[data-page-search]") ?? document.querySelector<HTMLElement>('main input[type="search"]');
          el?.focus();
        },
      },
      ...ALL_NAV.map((n) => ({ keys: `g ${n.chord}`, handler: () => navigate(n.to) })),
    ],
    [toggleSidebar, theme, navigate],
  );
  useHotkeys(hotkeys);

  if (expired) return <SessionExpired />;

  const connError = meta.isError && !(meta.error instanceof ApiError && meta.error.status === 401);
  const ThemeIcon = theme.pref === "system" ? Monitor : theme.pref === "dark" ? Moon : Sun;
  const workspace = meta.data?.workspace ?? "";

  return (
    <div className="min-h-screen bg-bg text-text">
      <a
        href="#main"
        className="sr-only z-toast rounded-sm bg-accent px-3 py-2 font-semibold text-on-accent focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-topbar flex h-[var(--topbar-h)] items-center gap-2 border-b border-border bg-surface-1 px-3 sm:gap-3 sm:px-4">
        {compact && <IconButton icon={Menu} label="Open navigation" onClick={() => setDrawer(true)} data-testid="topbar-menu" />}
        <Link to="/" className="flex shrink-0 items-center gap-2 text-text no-underline" aria-label="decider-lab Studio, Overview">
          <Mark className="h-7 w-7" />
          <span className="text-h3 max-sm:hidden">
            decider-lab <span className="font-normal text-muted">Studio</span>
          </span>
        </Link>
        {workspace && (
          <button
            type="button"
            onClick={() => copyWorkspace()}
            title={`${workspace} (click to copy)`}
            className="ml-2 hidden min-w-0 max-w-[320px] items-center gap-1 truncate rounded-sm px-2 py-1 font-mono text-caption text-muted hover:bg-surface-2 md:inline-flex"
            data-testid="topbar-workspace"
          >
            <span className="font-sans text-subtle">workspace:</span> {truncateMiddle(workspace, 36)}
          </button>
        )}
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setPalette(true)}
          data-testid="topbar-palette"
          aria-label="Search or run a command"
          aria-keyshortcuts="Meta+K Control+K"
          className="inline-flex h-9 items-center gap-2 rounded-sm border border-border-strong bg-surface-2 px-3 text-small text-muted hover:text-text max-sm:h-11 max-sm:w-11 max-sm:justify-center max-sm:px-0 sm:w-[300px]"
        >
          <Search size={16} aria-hidden />
          <span className="flex-1 text-left max-sm:hidden">Search or run a command</span>
          <Kbd keys={["mod", "K"]} className="max-sm:hidden" />
        </button>
        <JobsBadge count={active.length} items={active} />
        <IconButton
          icon={ThemeIcon}
          label={`Theme: ${theme.pref}. Switch to ${theme.next}`}
          onClick={theme.cycle}
          data-testid="topbar-theme"
          data-theme-pref={theme.pref}
        />
      </header>

      {connError && (
        <div role="status" className="flex items-center gap-3 border-b border-border bg-tint-warning px-4 py-2 text-small" data-testid="connection-banner">
          <WifiOff size={16} aria-hidden className="text-warning" />
          <span className="flex-1">Lost connection to the Studio server. Retrying…</span>
          <Button size="sm" icon={RotateCw} onClick={() => meta.refetch()}>
            Retry
          </Button>
        </div>
      )}

      <div className="flex">
        {!compact && (
          <nav
            aria-label="Main"
            className={cn(
              "sticky top-[var(--topbar-h)] z-sidebar flex h-[calc(100vh-var(--topbar-h))] shrink-0 flex-col border-r border-border bg-surface-1 transition-[width] duration",
              collapsed ? "w-[var(--sidebar-rail-w)]" : "w-[var(--sidebar-w)]",
            )}
            data-testid="sidebar"
          >
            <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
              <SidebarNav collapsed={collapsed} activeJobs={active.length} />
            </div>
            <SidebarFooter collapsed={collapsed} />
            <div className={cn("border-t border-border p-2", collapsed && "flex justify-center")}>
              <IconButton
                icon={collapsed ? PanelLeftOpen : PanelLeftClose}
                label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                onClick={() => setCollapsed(!collapsed)}
                size="sm"
              />
            </div>
          </nav>
        )}
        {compact && drawer && <MobileNav onClose={() => setDrawer(false)} activeJobs={active.length} />}

        <main id="main" tabIndex={-1} className="min-w-0 flex-1 focus:outline-none">
          <div className="mx-auto w-full max-w-content px-4 py-5 sm:px-5 md:px-6 md:py-6">
            <Outlet />
          </div>
        </main>
      </div>

      <CommandPalette open={palette} onOpenChange={setPalette} />
      <ShortcutHelp open={help} onOpenChange={setHelp} />
    </div>
  );
}

function MobileNav({ onClose, activeJobs }: { onClose: () => void; activeJobs: number }) {
  return (
    <DrawerNav onClose={onClose}>
      <SidebarNav collapsed={false} activeJobs={activeJobs} onNavigate={onClose} />
      <SidebarFooter collapsed={false} />
    </DrawerNav>
  );
}

// The nav drawer reuses the shared Drawer focus management.
function DrawerNav({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title="decider-lab Studio" side="left" width={280} data-testid="nav-drawer">
      <nav aria-label="Main" className="flex h-full flex-col">
        {children}
      </nav>
    </Drawer>
  );
}

function JobsBadge({ count, items }: { count: number; items: { job_id: string; title: string; progress: { fraction: number | null; label: string } }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        data-testid="topbar-jobs-badge"
        aria-expanded={open}
        aria-label={`${count} active job${count === 1 ? "" : "s"}`}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-sm px-2.5 text-small font-medium max-sm:h-11 max-sm:min-w-11 max-sm:justify-center",
          count ? "bg-tint-accent text-accent" : "text-muted hover:bg-surface-2",
        )}
      >
        <Activity size={16} aria-hidden className={cn(count > 0 && "dl-pulse")} />
        <span className="tnum">{count}</span>
        <span className="max-md:hidden">{count === 1 ? "job" : "jobs"}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-popover" aria-hidden onClick={() => setOpen(false)} />
          <div
            role="dialog"
            aria-label="Active jobs"
            className="absolute right-0 top-full z-popover mt-2 w-[min(360px,calc(100vw-24px))] rounded-md border border-border bg-surface-2 p-3 shadow-elev-2"
            onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
          >
            <div className="mb-2 text-caption uppercase tracking-wide text-muted">Active jobs</div>
            {items.length === 0 && <p className="text-small text-muted">Nothing is running.</p>}
            <ul className="flex flex-col gap-2">
              {items.slice(0, 5).map((j) => (
                <li key={j.job_id}>
                  <Link to={`/jobs/${j.job_id}`} onClick={() => setOpen(false)} className="block text-small text-text no-underline hover:underline">
                    {j.title}
                  </Link>
                  <ProgressBar value={j.progress.fraction} label={j.progress.label || "running"} showLabel />
                </li>
              ))}
            </ul>
            <Link to="/jobs" onClick={() => setOpen(false)} className="mt-3 inline-block text-small font-semibold">
              View all jobs →
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
