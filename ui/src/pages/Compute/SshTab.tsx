import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CircleCheck, CircleX, Pencil, Plug, Plus, Terminal, Trash2 } from "lucide-react";
import { addSshHost, computeKeys, deleteSshHost, listSshHosts, testSshHost, updateSshHost, type SshHostInput } from "@/api/compute";
import type { Items, SshHost } from "@/api/types";
import { ApiError } from "@/lib/api";
import {
  Button,
  Callout,
  Card,
  CardHeader,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  useToast,
  type Column,
} from "@/components";
import { fmtAbsolute, fmtRelative } from "@/lib/format";
import { TableOrEmpty } from "./shared";

const TARGET_RE = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}@([A-Za-z0-9][A-Za-z0-9.-]{0,252}|\[[0-9A-Fa-f:.]{2,45}\])(:\d{1,5})?$/;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

function LastTest({ host, testing }: { host: SshHost; testing: boolean }) {
  if (testing) return <span className="text-small text-muted" role="status">Testing…</span>;
  const t = host.last_test;
  if (!t) return <span className="text-small text-muted">not tested</span>;
  return t.ok ? (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-small" title={`Tested ${fmtAbsolute(t.at)}`} data-testid={`ssh-result-${host.name}`} data-ok="true">
      <CircleCheck size={14} aria-hidden className="shrink-0 text-success" />
      <span className="truncate">
        <span className="sr-only">Connected: </span>
        {t.latency_ms} ms · {t.gpu ?? "no NVIDIA GPU"}
      </span>
    </span>
  ) : (
    <span className="inline-flex min-w-0 items-start gap-1.5 text-small" title={`Tested ${fmtAbsolute(t.at)}`} data-testid={`ssh-result-${host.name}`} data-ok="false">
      <CircleX size={14} aria-hidden className="mt-[3px] shrink-0 text-danger" />
      <span className="break-words">
        <span className="sr-only">Failed: </span>
        {t.error}
      </span>
    </span>
  );
}

export function SshTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const [sp, setSp] = useSearchParams();
  const hosts = useQuery({ queryKey: computeKeys.sshHosts, queryFn: listSshHosts });
  const [editing, setEditing] = useState<SshHost | "new" | null>(sp.get("add") === "1" ? "new" : null);
  const [deleting, setDeleting] = useState<SshHost | null>(null);
  const [testing, setTesting] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (sp.get("add") === "1") {
      setEditing("new");
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev);
          n.delete("add");
          return n;
        },
        { replace: true },
      );
    }
  }, [sp, setSp]);

  const test = useMutation({
    mutationFn: (h: SshHost) => testSshHost(h.host_id),
    onMutate: (h) => setTesting((s) => new Set(s).add(h.host_id)),
    onSettled: (_d, _e, h) =>
      setTesting((s) => {
        const n = new Set(s);
        n.delete(h.host_id);
        return n;
      }),
    onSuccess: (res, h) => {
      qc.setQueryData<Items<SshHost>>(computeKeys.sshHosts, (old) =>
        old ? { items: old.items.map((x) => (x.host_id === h.host_id ? { ...x, last_test: res } : x)) } : old,
      );
      if (res.ok) toast({ title: `${h.name} is reachable`, description: `${res.latency_ms} ms · ${res.gpu ?? "no NVIDIA GPU"}` });
    },
  });

  const columns: Column<SshHost>[] = [
    { id: "name", header: "Name", cell: (h) => <span className="font-semibold">{h.name}</span>, sortValue: (h) => h.name },
    { id: "target", header: "Target", cell: (h) => <span className="font-mono text-mono">{h.target}</span>, sortValue: (h) => h.target },
    {
      id: "key",
      header: "Key",
      cell: (h) =>
        h.key_path ? (
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <span className="truncate font-mono text-mono">{h.key_path}</span>
            <span className={h.key_exists ? "text-success" : "text-warning"} title={h.key_exists ? "The key file exists" : "No file at this path"}>
              {h.key_exists ? "✓" : "✕"}
              <span className="sr-only">{h.key_exists ? "key file exists" : "key file not found"}</span>
            </span>
          </span>
        ) : (
          <span className="text-small text-muted">ssh agent / default</span>
        ),
      hideBelow: "md",
    },
    { id: "test", header: "Last test", cell: (h) => <LastTest host={h} testing={testing.has(h.host_id)} />, sortValue: (h) => h.last_test?.at ?? "" },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      align: "right",
      cell: (h) => (
        <span className="inline-flex items-center justify-end gap-1">
          <Button size="sm" icon={Plug} loading={testing.has(h.host_id)} onClick={() => test.mutate(h)} data-testid={`ssh-test-${h.name}`} aria-label={`Test connection to ${h.name}`}>
            Test
          </Button>
          <IconButton size="sm" icon={Pencil} label={`Edit ${h.name}`} onClick={() => setEditing(h)} data-testid={`ssh-edit-${h.name}`} />
          <IconButton size="sm" icon={Trash2} label={`Delete ${h.name}`} onClick={() => setDeleting(h)} data-testid={`ssh-delete-${h.name}`} />
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader
          title="SSH hosts"
          description={
            <>
              Your own machines for <code className="font-mono">--on ssh</code>. Studio stores the target and the key path only; it never reads the key. Tests run{" "}
              <code className="font-mono">ssh -o BatchMode=yes</code>, so they never prompt.
            </>
          }
          actions={
            <Button variant="primary" icon={Plus} onClick={() => setEditing("new")} data-testid="ssh-add">
              Add host
            </Button>
          }
        />
        <div className="max-sm:p-3">
          {hosts.isError ? (
            <div className="p-4">
              <ErrorState error={hosts.error} onRetry={() => void hosts.refetch()} />
            </div>
          ) : (
            <TableOrEmpty
              data-testid="ssh-hosts"
              caption="Saved SSH hosts"
              columns={columns}
              rows={hosts.data?.items ?? []}
              rowKey={(h) => h.host_id}
              rowTestId={(h) => `ssh-host-${h.name}`}
              loading={hosts.isLoading}
              mobile="cards"
              empty={
                <EmptyState
                  icon={Terminal}
                  title="No SSH hosts yet."
                  body="Add a GPU server you can reach with ssh to run labs on it."
                  action={
                    <Button variant="primary" icon={Plus} onClick={() => setEditing("new")}>
                      Add host
                    </Button>
                  }
                  command="decider-lab run lab.yaml --on ssh --host user@host"
                />
              }
            />
          )}
        </div>
        {test.isError && (
          <div className="px-4 pb-4">
            <Callout tone="danger" alert>
              {test.error instanceof Error ? test.error.message : "The test could not run."}
            </Callout>
          </div>
        )}
        {hosts.data && hosts.data.items.length > 0 && (
          <p className="border-t border-border px-4 py-3 text-caption text-muted sm:px-5">
            Last tests: {hosts.data.items.filter((h) => h.last_test).map((h) => `${h.name} ${fmtRelative(h.last_test!.at)}`).join(" · ") || "none yet"}
          </p>
        )}
      </Card>
      <HostDialog
        host={editing}
        onClose={() => setEditing(null)}
        onSaved={(h, isNew) => {
          void qc.invalidateQueries({ queryKey: computeKeys.sshHosts });
          toast({ title: isNew ? `Added ${h.name}` : `Saved ${h.name}` });
          setEditing(null);
        }}
      />
      <ConfirmDialog
        open={deleting != null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "host"}`}
        tone="danger"
        confirmLabel="Delete host"
        body="This removes the bookmark from Studio. Nothing changes on the machine, and the key file is not touched."
        onConfirm={async () => {
          const h = deleting!;
          await deleteSshHost(h.host_id);
          void qc.invalidateQueries({ queryKey: computeKeys.sshHosts });
          toast({ title: `Deleted ${h.name}` });
        }}
      />
    </div>
  );
}

function HostDialog({ host, onClose, onSaved }: { host: SshHost | "new" | null; onClose: () => void; onSaved: (h: SshHost, isNew: boolean) => void }) {
  const isNew = host === "new";
  const empty: SshHostInput = { name: "", target: "", key_path: "~/.ssh/id_ed25519", work_dir: null };
  const [form, setForm] = useState<SshHostInput>(empty);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (host == null) return;
    setForm(host === "new" ? empty : { name: host.name, target: host.target, key_path: host.key_path, work_dir: host.work_dir });
    setErrors({});
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host]);

  const validate = (): Record<string, string> => {
    const e: Record<string, string> = {};
    if (!NAME_RE.test(form.name.trim())) e.name = "Use letters, digits, dot, dash or underscore, starting with a letter or digit.";
    if (!TARGET_RE.test(form.target.trim())) e.target = "Use user@host or user@host:port.";
    if (form.work_dir && !/^[A-Za-z0-9_./~-]{1,512}$/.test(form.work_dir)) e.work_dir = "Use a plain path (letters, digits, / . _ - ~).";
    return e;
  };

  const submit = async (ev?: FormEvent) => {
    ev?.preventDefault();
    const e = validate();
    setErrors(e);
    setError(null);
    if (Object.keys(e).length) {
      const first = Object.keys(e)[0];
      document.getElementById(`ssh-field-${first}`)?.focus();
      return;
    }
    setBusy(true);
    try {
      const body = { ...form, name: form.name.trim(), target: form.target.trim(), work_dir: form.work_dir?.trim() || null };
      const saved = isNew ? await addSshHost(body) : await updateSshHost((host as SshHost).host_id, body);
      onSaved(saved, isNew);
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = (err.detail?.fields as { field: string; message: string }[] | undefined) ?? [];
        if (fields.length) setErrors(Object.fromEntries(fields.map((f) => [f.field, f.message])));
        setError(err.code === "name_taken" ? err.message : fields.length ? "Some fields are not valid." : err.message);
      } else setError("The host could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={host != null}
      onOpenChange={(o) => !o && onClose()}
      title={isNew ? "Add SSH host" : `Edit ${(host as SshHost | null)?.name ?? "host"}`}
      description="Studio stores the target and the key path only; it never reads the key."
      data-testid="ssh-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} onClick={() => void submit()} data-testid="ssh-save">
            {isNew ? "Add host" : "Save host"}
          </Button>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={(e) => void submit(e)} noValidate>
        <Input
          ref={nameRef}
          id="ssh-field-name"
          label="Name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          error={errors.name}
          placeholder="gpu-box"
          autoComplete="off"
          data-testid="ssh-field-name"
          data-autofocus
        />
        <Input
          id="ssh-field-target"
          label="Target"
          mono
          value={form.target}
          onChange={(e) => setForm({ ...form, target: e.target.value })}
          error={errors.target}
          hint="user@host or user@host:port"
          placeholder="ubuntu@10.0.0.5:22"
          autoComplete="off"
          spellCheck={false}
          data-testid="ssh-field-target"
        />
        <Input
          id="ssh-field-key_path"
          label="Key path"
          mono
          value={form.key_path}
          onChange={(e) => setForm({ ...form, key_path: e.target.value })}
          error={errors.key_path}
          hint="A path to the private key on this machine. Leave empty to use your ssh agent or ~/.ssh/config."
          autoComplete="off"
          spellCheck={false}
          data-testid="ssh-field-key"
        />
        <Input
          id="ssh-field-work_dir"
          label="Remote work dir (optional)"
          mono
          value={form.work_dir ?? ""}
          onChange={(e) => setForm({ ...form, work_dir: e.target.value || null })}
          error={errors.work_dir}
          placeholder="~/decider-lab-work"
          autoComplete="off"
          spellCheck={false}
          data-testid="ssh-field-workdir"
        />
        {error && (
          <Callout tone="danger" alert data-testid="ssh-error">
            {error}
          </Callout>
        )}
        <button type="submit" hidden aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}
