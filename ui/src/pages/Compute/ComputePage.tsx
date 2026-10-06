import { Cloud, Cpu, Server, Terminal } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { PageHeader, TabPanel, Tabs, useTabParam } from "@/components";
import { useRegisterCommands } from "@/palette/registry";
import { AwsTab } from "./AwsTab";
import { LocalTab } from "./LocalTab";
import { SshTab } from "./SshTab";
import { VastTab } from "./VastTab";

// Compute: this machine, rented GPUs and your own servers. DESIGN.md 4.7; API.md section 10.
const TABS = [
  { id: "local", label: "Local" },
  { id: "vast", label: "vast.ai" },
  { id: "aws", label: "AWS" },
  { id: "ssh", label: "SSH hosts" },
];

export default function ComputePage() {
  const [tab, setTab] = useTabParam("local");
  const navigate = useNavigate();
  const active = TABS.some((t) => t.id === tab) ? tab : "local";
  useRegisterCommands(
    "compute-page",
    [
      { id: "compute.tab.local", label: "Compute: this machine", section: "Commands", icon: Cpu, run: () => navigate("/compute?tab=local") },
      { id: "compute.tab.vast", label: "Compute: vast.ai instances and offers", section: "Commands", icon: Cloud, run: () => navigate("/compute?tab=vast") },
      { id: "compute.tab.aws", label: "Compute: AWS identity, quotas, Bedrock", section: "Commands", icon: Server, run: () => navigate("/compute?tab=aws") },
      { id: "compute.tab.ssh", label: "Compute: SSH hosts", section: "Commands", icon: Terminal, run: () => navigate("/compute?tab=ssh") },
    ],
    [navigate],
  );
  return (
    <div data-testid="page-compute">
      <PageHeader title="Compute" description="This machine, rented GPUs and your own servers. Only machines decider-lab created are listed or touched." />
      <Tabs tabs={TABS} value={active} onChange={setTab} label="Compute sections" testIdPrefix="compute-tab" className="mb-6" />
      <TabPanel id="local" active={active === "local"}>
        <LocalTab />
      </TabPanel>
      <TabPanel id="vast" active={active === "vast"}>
        <VastTab />
      </TabPanel>
      <TabPanel id="aws" active={active === "aws"}>
        <AwsTab />
      </TabPanel>
      <TabPanel id="ssh" active={active === "ssh"}>
        <SshTab />
      </TabPanel>
    </div>
  );
}
