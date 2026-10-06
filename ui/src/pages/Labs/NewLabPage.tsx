import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createLab, labKeys, listTemplates } from "@/api/labs";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody } from "@/components/Card";
import { CodeInline } from "@/components/Code";
import { CodeEditor } from "@/components/CodeEditor";
import { ErrorState } from "@/components/ErrorState";
import { Input } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";

const NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export default function NewLabPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const templates = useQuery({ queryKey: labKeys.templates, queryFn: listTemplates, staleTime: Infinity });
  const [template, setTemplate] = useState<"eval" | "finetune">("eval");
  const [name, setName] = useState("first-lab");
  const [dir, setDir] = useState("labs/first-lab");
  const [dirTouched, setDirTouched] = useState(false);
  const [touched, setTouched] = useState(false);

  const nameErr = touched && !NAME.test(name) ? "Use lowercase letters, digits, - and _ (start with a letter or digit)." : undefined;
  const dirErr = touched && (!dir.trim() || dir.startsWith("/")) ? "A directory inside the workspace, for example labs/my-lab." : undefined;
  const m = useMutation({
    mutationFn: () => createLab({ template, name, dir: dir.trim() }),
    onSuccess: async (created) => {
      await qc.invalidateQueries({ queryKey: labKeys.all });
      toast({ title: `Created ${created.name}`, description: created.path });
      nav(`/labs/${created.lab_id}?tab=editor`);
    },
  });
  const err = m.error instanceof ApiError ? m.error : null;
  const serverField = (f: string) => (err?.detail?.fields as { field: string; message: string }[] | undefined)?.find((x) => x.field === f)?.message;
  const dirServerErr = err?.code === "dir_not_empty" || err?.code === "path_outside_workspace" ? err.message : serverField("dir");

  const preview = useMemo(() => {
    const t = templates.data?.items.find((x) => x.id === template);
    if (!t) return "";
    return t.lab_yaml.replace(/^name:[^\n]*$/m, `name: ${name || "my-lab"}`);
  }, [templates.data, template, name]);

  const valid = NAME.test(name) && dir.trim() && !dir.startsWith("/");
  const submit = () => {
    setTouched(true);
    if (valid) m.mutate();
  };

  return (
    <div data-testid="page-lab-new">
      <PageHeader
        title="New lab"
        description="Start from a template. A lab is one YAML file; you can edit everything after creating it."
        breadcrumbs={[{ label: "Labs", to: "/labs" }, { label: "New lab" }]}
      />
      <Card>
        <CardBody>
          <form
            className="grid gap-6 lg:grid-cols-[minmax(0,380px),minmax(0,1fr)]"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="flex flex-col gap-5">
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-small font-medium">Template</legend>
                {templates.isLoading && <Skeleton lines={2} />}
                {templates.isError && <ErrorState error={templates.error} onRetry={() => templates.refetch()} />}
                {templates.data?.items.map((t) => (
                  <label
                    key={t.id}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-sm border px-3 py-2.5",
                      template === t.id ? "border-accent bg-tint-accent" : "border-border-strong hover:bg-surface-2",
                    )}
                  >
                    <input
                      type="radio"
                      name="template"
                      value={t.id}
                      checked={template === t.id}
                      onChange={() => setTemplate(t.id)}
                      className="mt-1 h-4 w-4 accent-[var(--accent)]"
                      data-testid={`lab-template-${t.id}`}
                    />
                    <span>
                      <span className="block font-semibold">{t.title}</span>
                      <span className="block text-small text-muted">{t.description}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
              <Input
                label="Name"
                hint="Lowercase letters, digits, - and _"
                value={name}
                mono
                autoComplete="off"
                onChange={(e) => {
                  setName(e.target.value);
                  if (!dirTouched) setDir(`labs/${e.target.value}`);
                }}
                error={nameErr ?? serverField("name")}
                data-testid="lab-name"
              />
              <Input
                label="Directory"
                hint="Inside the workspace; must not exist or be empty"
                value={dir}
                mono
                autoComplete="off"
                onChange={(e) => {
                  setDir(e.target.value);
                  setDirTouched(true);
                }}
                error={dirErr ?? dirServerErr}
                data-testid="lab-dir"
              />
              <div className="text-small text-muted">
                Same as <CodeInline code={`decider-lab init ${dir || "labs/my-lab"} --template ${template}`} copy />
              </div>
              {err && !dirServerErr && !serverField("name") && (
                <Callout tone="danger" alert>
                  {err.message}
                </Callout>
              )}
              <div className="flex justify-end gap-2 max-sm:sticky max-sm:bottom-0 max-sm:z-actionbar max-sm:-mx-4 max-sm:border-t max-sm:border-border max-sm:bg-surface-1 max-sm:px-4 max-sm:py-3">
                <Button variant="ghost" onClick={() => nav("/labs")}>
                  Cancel
                </Button>
                <Button type="submit" variant="primary" loading={m.isPending} disabled={touched && !valid} data-testid="lab-create">
                  Create lab
                </Button>
              </div>
            </div>
            <div className="min-w-0">
              <div className="mb-1 text-small font-medium" id="preview-label">
                Preview
              </div>
              <CodeEditor value={preview} readOnly ariaLabel="lab.yaml preview (read-only)" height="460px" testId="lab-preview" />
            </div>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
