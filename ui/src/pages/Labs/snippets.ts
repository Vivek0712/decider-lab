// Text snippets for the lab editor's Insert menu (DESIGN.md 4.2.2). Studio never rewrites YAML
// structurally: a snippet is inserted as text under its section (or appended as a new section),
// then validated like any other edit.

export type Snippet = { id: string; label: string; group: "Model" | "Suite" | "Option" | "Compute"; text: string };

export const SNIPPETS: Snippet[] = [
  { id: "serve-hf", group: "Model", label: "serve · Hugging Face (pinned)", text: "v19:\n  serve: hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd" },
  { id: "serve-s3", group: "Model", label: "serve · S3 archive", text: "mine:\n  serve: s3://my-bucket/models/mine.tar.gz\n  sha256: <64-hex checksum>" },
  { id: "serve-https", group: "Model", label: "serve · https archive", text: "mine:\n  serve: https://example.com/models/mine.tar.gz\n  sha256: <64-hex checksum>" },
  { id: "serve-dir", group: "Model", label: "serve · local directory", text: "local:\n  serve: ./checkpoints/mine" },
  { id: "url", group: "Model", label: "url · System One server", text: 'remote: {url: "http://127.0.0.1:8000"}' },
  { id: "bedrock", group: "Model", label: "bedrock · Amazon Nova Pro", text: "nova: {bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}" },
  { id: "strands", group: "Model", label: "strands · Bedrock via Strands Agents", text: "agent: {strands: {provider: bedrock, model_id: us.amazon.nova-pro-v1:0}}" },
  { id: "chat", group: "Model", label: "chat · OpenAI-compatible", text: "gpt: {chat: gpt-4o-mini, base_url: \"https://api.openai.com/v1\", api_key_env: OPENAI_API_KEY}" },
  { id: "python", group: "Model", label: "python · your code", text: 'heuristic: {python: "my_model:Heuristic"}' },
  { id: "baseline", group: "Model", label: "baseline · majority", text: "majority: {baseline: majority}" },
  { id: "suite-smoke", group: "Suite", label: "smoke (90 rows)", text: "smoke" },
  { id: "suite-synthetic", group: "Suite", label: "synthetic (per_kind 100)", text: "synthetic: {per_kind: 100}" },
  { id: "suite-heldout", group: "Suite", label: "heldout (needs the extra)", text: "heldout" },
  { id: "suite-file", group: "Suite", label: "a JSONL file", text: "file: data/my_eval.jsonl" },
  { id: "calibrate", group: "Option", label: "calibrate: true", text: "calibrate: true" },
  { id: "baseline-key", group: "Option", label: "baseline: majority", text: "baseline: majority" },
  { id: "jevbench", group: "Option", label: "jevbench: [v19]", text: "jevbench: [v19]" },
  { id: "finetune", group: "Option", label: "finetune block", text: "finetune:\n  mine:\n    from: hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd\n    train: data/train.jsonl\n    steps: 150" },
  { id: "compute-ssh", group: "Compute", label: "compute · ssh", text: "compute:\n  backend: ssh\n  max_hours: 2\n  ssh: {host: ubuntu@my-gpu-box, key: ~/.ssh/id_ed25519}" },
  { id: "compute-aws", group: "Compute", label: "compute · aws", text: "compute:\n  backend: aws\n  max_hours: 2\n  aws: {instance_type: g6e.xlarge, region: us-east-1, profile: default}" },
  { id: "compute-vast", group: "Compute", label: "compute · vast.ai", text: "compute:\n  backend: vast\n  max_hours: 2\n  vast: {gpu: A100_SXM4, max_price: 0.8, ssh_key: ~/.ssh/id_ed25519}" },
];

const indent = (text: string, pad: string) => text.split("\n").map((l) => pad + l).join("\n");

/** Insert a snippet's text into the YAML: under `models:` / `suites:` when present, else as a new section. */
export function insertSnippet(yaml: string, s: Snippet): string {
  const src = yaml.endsWith("\n") || yaml === "" ? yaml : yaml + "\n";
  const lines = src.split("\n");
  if (s.group === "Model") {
    const i = lines.findIndex((l) => /^models:\s*(#.*)?$/.test(l));
    if (i >= 0) {
      lines.splice(i + 1, 0, indent(s.text, "  "));
      return lines.join("\n");
    }
    const flow = lines.findIndex((l) => /^models:\s*\{/.test(l));
    if (flow >= 0) return src + `# move this model into models: above\n# ${s.text.replace(/\n/g, "\n# ")}\n`;
    return src + "models:\n" + indent(s.text, "  ") + "\n";
  }
  if (s.group === "Suite") {
    const i = lines.findIndex((l) => /^suites:\s*(#.*)?$/.test(l));
    if (i >= 0) {
      lines.splice(i + 1, 0, `  - ${s.text}`);
      return lines.join("\n");
    }
    const flow = lines.findIndex((l) => /^suites:\s*\[.*\]\s*(#.*)?$/.test(l));
    if (flow >= 0) {
      const item = s.text.includes(":") ? `{${s.text}}` : s.text;
      const line = lines[flow];
      const close = line.lastIndexOf("]");
      const empty = /\[\s*$/.test(line.slice(0, close));
      lines[flow] = line.slice(0, close) + (empty ? item : `, ${item}`) + line.slice(close);
      return lines.join("\n");
    }
    return src + `suites:\n  - ${s.text}\n`;
  }
  const key = s.text.split(":")[0];
  if (lines.some((l) => l.startsWith(`${key}:`))) return src + `# already set above; replace it with:\n# ${s.text.replace(/\n/g, "\n# ")}\n`;
  return src + s.text + "\n";
}
