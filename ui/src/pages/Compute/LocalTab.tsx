import { useQuery } from "@tanstack/react-query";
import { CircleCheck, Info, RotateCw, TriangleAlert } from "lucide-react";
import type { DoctorCheck } from "@/api/types";
import { computeKeys, doctorKey, getComputeDoctor } from "@/api/compute";
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, Skeleton } from "@/components";
import { cn } from "@/lib/cn";
import { Fact } from "./shared";

const STATUS: Record<DoctorCheck["status"], { Icon: typeof Info; cls: string; label: string }> = {
  ok: { Icon: CircleCheck, cls: "text-success", label: "ok" },
  warn: { Icon: TriangleAlert, cls: "text-warning", label: "warning" },
  info: { Icon: Info, cls: "text-muted", label: "info" },
};

/** Doctor checks as a status list. Rows whose item starts with spaces are capabilities of the row above. */
export function DoctorList({ checks }: { checks: DoctorCheck[] }) {
  return (
    <ul data-testid="doctor-list" className="divide-y divide-border" aria-label="Doctor checks">
      {checks.map((c, i) => {
        const sub = /^\s+/.test(c.item);
        const s = STATUS[c.status] ?? STATUS.info;
        return (
          <li
            key={`${c.item}-${i}`}
            data-testid={`doctor-row-${doctorKey(c.item)}`}
            data-status={c.status}
            className={cn("grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 gap-y-0.5 py-2.5 sm:grid-cols-[20px_minmax(140px,220px)_minmax(0,1fr)]", sub && "pl-6")}
          >
            <s.Icon size={16} aria-hidden className={cn("mt-[3px]", s.cls)} />
            <div className={cn("min-w-0 font-medium", sub && "text-muted")}>
              <span className="sr-only">{s.label}: </span>
              {c.item.trim()}
            </div>
            <div className="col-start-2 min-w-0 break-words font-mono text-mono text-muted sm:col-start-3">{c.detail}</div>
          </li>
        );
      })}
    </ul>
  );
}

export function LocalTab() {
  const q = useQuery({ queryKey: computeKeys.doctor, queryFn: getComputeDoctor, staleTime: 60_000, retry: false });
  const d = q.data;
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <Card aria-busy={q.isLoading || undefined}>
        <CardHeader
          title="Doctor"
          description={
            <>
              What this machine can do, and what to install for the rest. Same checks as <code className="font-mono">decider-lab doctor</code>.
            </>
          }
          actions={
            <Button size="sm" icon={RotateCw} loading={q.isFetching && !q.isLoading} onClick={() => void q.refetch()} data-testid="doctor-recheck">
              Re-check
            </Button>
          }
        />
        <CardBody className="py-2">
          {q.isLoading && <Skeleton lines={8} className="py-3" />}
          {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} className="my-2" />}
          {d && (
            <>
              <div className="flex flex-wrap gap-2 pb-2 pt-1" data-testid="doctor-counts">
                <Badge tone="success" icon={CircleCheck}>
                  {d.counts.ok} ok
                </Badge>
                <Badge tone={d.counts.warn ? "warning" : "neutral"} icon={TriangleAlert}>
                  {d.counts.warn} warnings
                </Badge>
                <Badge tone="neutral" icon={Info}>
                  {d.counts.info} info
                </Badge>
              </div>
              <DoctorList checks={d.checks} />
            </>
          )}
        </CardBody>
      </Card>
      <Card data-testid="machine-card" aria-busy={q.isLoading || undefined}>
        <CardHeader title="This machine" />
        <CardBody>
          {q.isLoading && <Skeleton lines={5} />}
          {d && (
            <dl className="grid grid-cols-2 gap-4">
              <Fact label="OS">{d.machine.os}</Fact>
              <Fact label="Python">{d.machine.python}</Fact>
              <Fact label="CPUs">{d.machine.cpus ?? "—"}</Fact>
              <Fact label="Memory">{d.machine.memory_gb != null ? `${d.machine.memory_gb} GB` : "—"}</Fact>
              <Fact label="Accelerator" testId="machine-accelerator" className="col-span-2">
                  {d.machine.gpus.length > 0 ? (
                    <ul className="flex flex-col gap-1">
                      {d.machine.gpus.map((g) => (
                        <li key={g.index} className="font-mono text-mono">
                          GPU {g.index}: {g.name}, {g.memory_total_gb} GB
                        </li>
                      ))}
                    </ul>
                  ) : d.machine.accelerator === "CPU" ? (
                    "CPU only"
                  ) : (
                    d.machine.accelerator
                  )}
              </Fact>
            </dl>
          )}
          {d && (
            <p className="mt-4 text-small text-muted">
              Serving or training a model here needs torch and strands-decider; evaluating against a URL, Bedrock or a remote backend does not.
            </p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
