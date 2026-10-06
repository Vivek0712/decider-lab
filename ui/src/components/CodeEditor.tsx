import { lazy, Suspense } from "react";
import type { Problem } from "@/api/types";
import { Skeleton } from "./Skeleton";

export type CodeEditorProps = {
  value: string;
  onChange?: (value: string) => void;
  /** Lint markers (line/column from the server's validation). */
  problems?: Problem[];
  readOnly?: boolean;
  height?: string;
  ariaLabel: string;
  language?: "yaml" | "text";
  testId?: string;
};

const Impl = lazy(() => import("./CodeEditorImpl"));

/** CodeMirror 6 editor (YAML by default), loaded on demand so the shell stays small. */
export function CodeEditor(props: CodeEditorProps) {
  return (
    <Suspense fallback={<Skeleton shape="block" height={props.height ?? "420px"} />}>
      <Impl {...props} />
    </Suspense>
  );
}

/** DESIGN.md YamlEditor: the lab.yaml editor. */
export function YamlEditor(props: Omit<CodeEditorProps, "language" | "ariaLabel"> & { ariaLabel?: string }) {
  return <CodeEditor language="yaml" ariaLabel={props.ariaLabel ?? "lab.yaml editor"} testId={props.testId ?? "lab-editor"} {...props} />;
}
