import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Database, FileUp, ShieldCheck, Sparkles, Split } from "lucide-react";
import { dataKeys, listSuites, refLabel } from "@/api/data";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { TabPanel, Tabs } from "@/components/Tabs";
import { useRegisterCommands } from "@/palette/registry";
import { CsvTab } from "./CsvTab";
import { SuiteInspector } from "./SuiteInspector";
import { SuitesTab } from "./SuitesTab";
import { GenerateTab, LeakcheckTab, SplitTab } from "./ToolTabs";

const TABS = [
  { id: "suites", label: "Suites" },
  { id: "csv", label: "Upload CSV" },
  { id: "generate", label: "Generate" },
  { id: "split", label: "Split" },
  { id: "leakcheck", label: "Leakcheck" },
];

// Owner: Data area. Tabs: Suites | Upload CSV | Generate | Split | Leakcheck. DESIGN.md 4.6; API.md section 9.
// URL state: ?tab=, ?inspect=<suite ref> (the inspector drawer), ?train=<file_id>&against=<ref,ref> (leakcheck).
export default function DataPage() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "suites";
  const inspect = sp.get("inspect");
  const suites = useQuery({ queryKey: dataKeys.suites, queryFn: listSuites, refetchInterval: 20_000 });
  const set = (patch: Record<string, string | null>) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v == null || (k === "tab" && v === "suites")) n.delete(k);
          else n.set(k, v);
        }
        return n;
      },
      { replace: true },
    );
  const openInspect = (ref: string) => set({ inspect: ref });
  const toLeakcheck = (fileId: string, against?: string[]) => set({ tab: "leakcheck", train: fileId, against: against?.join(",") ?? null });

  useRegisterCommands(
    "data-page",
    [
      { id: "data.uploadCsv", label: "Upload CSV…", section: "Commands", icon: FileUp, run: ({ close }) => { close(); set({ tab: "csv" }); } },
      { id: "data.generate", label: "Generate training rows…", section: "Commands", icon: Sparkles, run: ({ close }) => { close(); set({ tab: "generate" }); } },
      { id: "data.split", label: "Split dev/test…", section: "Commands", icon: Split, run: ({ close }) => { close(); set({ tab: "split" }); } },
      { id: "data.leakcheck", label: "Leakcheck training data…", section: "Commands", icon: ShieldCheck, run: ({ close }) => { close(); set({ tab: "leakcheck" }); } },
      ...["smoke", "synthetic"].map((s) => ({
        id: `data.inspectSuite.${s}`,
        label: `Inspect suite: ${s}`,
        section: "Commands" as const,
        icon: Database,
        run: ({ close }: { close: () => void }) => {
          close();
          set({ tab: null, inspect: s });
        },
      })),
    ],
    [sp],
  );

  const s = suites.data;
  return (
    <div data-testid="page-data">
      <PageHeader title="Data" description="Suites and the tools that make and check rows." />
      <Tabs label="Data" value={tab} onChange={(id) => set({ tab: id })} tabs={TABS} testIdPrefix="data-tab" className="mb-5" />
      {suites.error ? (
        <ErrorState error={suites.error} onRetry={() => suites.refetch()} />
      ) : !s ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <Skeleton shape="block" height={180} />
          <Skeleton shape="block" height={120} />
        </div>
      ) : (
        <>
          <TabPanel id="suites" active={tab === "suites"}>
            <SuitesTab suites={s} onInspect={openInspect} onLeakcheck={(id) => toLeakcheck(id)} />
          </TabPanel>
          <TabPanel id="csv" active={tab === "csv"}>
            <CsvTab onInspect={openInspect} />
          </TabPanel>
          <TabPanel id="generate" active={tab === "generate"}>
            <GenerateTab suites={s} onInspect={openInspect} onLeakcheck={toLeakcheck} />
          </TabPanel>
          <TabPanel id="split" active={tab === "split"}>
            <SplitTab suites={s} onInspect={openInspect} />
          </TabPanel>
          <TabPanel id="leakcheck" active={tab === "leakcheck"}>
            <LeakcheckTab suites={s} initialTrain={sp.get("train")} initialAgainst={sp.get("against")?.split(",").filter(Boolean) ?? null} />
          </TabPanel>
        </>
      )}
      <SuiteInspector suiteRef={inspect} title={inspect ? refLabel(inspect, s?.files ?? []) : ""} onClose={() => set({ inspect: null })} />
    </div>
  );
}
