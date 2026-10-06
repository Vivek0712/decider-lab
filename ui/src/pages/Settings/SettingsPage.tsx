import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getAbout, getEnv, getMeta, getSettings, putSettings, systemKeys } from "@/api/system";
import type { Backend, Settings } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeInline } from "@/components/Code";
import { ErrorState } from "@/components/ErrorState";
import { Checkbox, Input, SegmentedControl, Select } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { useTheme } from "@/theme/ThemeProvider";

/** Workspace, appearance, run defaults, environment variable status (names only), about. */
export default function SettingsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const theme = useTheme();
  const settings = useQuery({ queryKey: systemKeys.settings, queryFn: getSettings });
  const meta = useQuery({ queryKey: systemKeys.meta, queryFn: getMeta });
  const about = useQuery({ queryKey: systemKeys.about, queryFn: getAbout });
  const env = useQuery({ queryKey: systemKeys.env(), queryFn: () => getEnv() });

  const save = async (patch: Partial<Settings>) => {
    try {
      const next = await putSettings(patch);
      qc.setQueryData(systemKeys.settings, next);
      toast({ tone: "success", title: "Saved" });
    } catch (e) {
      toast({ tone: "info", title: "Not saved", description: (e as Error).message });
    }
  };
  const s = settings.data;

  return (
    <div data-testid="page-settings" className="flex max-w-3xl flex-col gap-5">
      <PageHeader title="Settings" description="Stored in this workspace's Studio state directory. Nothing here leaves your machine." />
      <Card><CardHeader title="Workspace" /><CardBody>
        {meta.isError ? <ErrorState error={meta.error} /> : !meta.data ? <Skeleton className="h-16" /> : (
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-small" data-testid="settings-workspace">
            <dt className="text-muted">Workspace</dt><dd><CodeInline code={meta.data.workspace} /></dd>
            <dt className="text-muted">Studio state</dt><dd><CodeInline code={meta.data.state_dir} /></dd>
            <dt className="text-muted">Model cache</dt><dd><CodeInline code={meta.data.cache_dir} /></dd>
          </dl>
        )}
      </CardBody></Card>

      <Card><CardHeader title="Appearance" /><CardBody className="flex flex-col gap-4">
        <SegmentedControl label="Theme" value={theme.pref} data-testid="settings-theme"
          onChange={(v) => { theme.setPref(v); void save({ theme: v }); }}
          options={[{ value: "system", label: "System" }, { value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} />
        {s && <SegmentedControl label="Density" value={s.density} data-testid="settings-density" onChange={(v) => save({ density: v })}
          options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} />}
        {s && <SegmentedControl label="Reduce motion" value={s.reduce_motion} onChange={(v) => save({ reduce_motion: v })}
          options={[{ value: "system", label: "Follow system" }, { value: "on", label: "On" }]} />}
      </CardBody></Card>

      <Card><CardHeader title="Runs and pulls" /><CardBody className="flex flex-col gap-4">
        {settings.isError ? <ErrorState error={settings.error} /> : !s ? <Skeleton className="h-32" /> : <>
          <Select label="Default backend for new runs" value={s.default_backend} data-testid="settings-backend"
            onChange={(v) => save({ default_backend: v as Backend })}
            options={[{ value: "local", label: "local (this machine)" }, { value: "ssh", label: "ssh (a machine you have)" }, { value: "aws", label: "aws (EC2 for the run)" }, { value: "vast", label: "vast (a rented GPU)" }]} />
          <Input label="Jobs running at once" type="number" min={1} max={8} defaultValue={s.max_concurrent_jobs} data-testid="settings-max-jobs"
            hint="1 to 8. Extra jobs wait in a queue."
            onBlur={(e) => { const n = Number(e.target.value); if (n >= 1 && n <= 8 && n !== s.max_concurrent_jobs) void save({ max_concurrent_jobs: n }); }} />
          <Checkbox label="Require Hugging Face sources pinned to a commit by default" checked={s.require_pinned_default} data-testid="settings-require-pinned"
            onChange={(v) => save({ require_pinned_default: v })} />
          <Checkbox label="Require a sha256 for archive and URL sources" checked={s.require_sha256}
            onChange={(v) => save({ require_sha256: v })} />
        </>}
      </CardBody></Card>

      <Card><CardHeader title="Environment variables" description="Names Studio or your labs use. Values are never shown or sent to the browser." /><CardBody>
        {env.isError ? <ErrorState error={env.error} /> : !env.data ? <Skeleton className="h-24" /> : (
          <table className="w-full text-small" data-testid="settings-env">
            <thead><tr className="text-left text-muted"><th className="py-1">Name</th><th>Status</th><th>Used by</th><th>Purpose</th></tr></thead>
            <tbody>{env.data.items.map((v) => (
              <tr key={v.name} className="border-t border-border" data-testid={`env-${v.name}`}>
                <td className="py-1.5 font-mono">{v.name}</td>
                <td>{v.set ? <Badge tone="success">set</Badge> : <Badge tone="neutral">not set</Badge>}</td>
                <td className="text-muted">{v.referenced_by.join(", ") || "—"}</td>
                <td className="text-muted">{v.purpose}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </CardBody></Card>

      <Card><CardHeader title="About" /><CardBody>
        {!about.data ? <Skeleton className="h-16" /> : (
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-small" data-testid="settings-about">
            <dt className="text-muted">decider-lab</dt><dd>{about.data.decider_lab_version}{about.data.studio_build ? ` (build ${about.data.studio_build})` : ""}</dd>
            <dt className="text-muted">strands-decider</dt><dd>{about.data.strands_decider ?? "not installed"}</dd>
            <dt className="text-muted">Python</dt><dd>{about.data.python}</dd>
            <dt className="text-muted">License</dt><dd>{about.data.license}</dd>
            <dt className="text-muted">Telemetry</dt><dd>{about.data.telemetry === "none" ? "none: Studio sends nothing anywhere" : about.data.telemetry}</dd>
          </dl>
        )}
      </CardBody></Card>
    </div>
  );
}
