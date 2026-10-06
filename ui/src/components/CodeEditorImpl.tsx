import { useMemo } from "react";
import CodeMirror, { EditorView, type Extension } from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { lintGutter, linter, type Diagnostic } from "@codemirror/lint";
import type { Problem } from "@/api/types";
import type { CodeEditorProps } from "./CodeEditor";

// Colors come from the design tokens, so the editor follows the theme without a rebuild.
const studioTheme = EditorView.theme({
  "&": { backgroundColor: "var(--surface-2)", color: "var(--text)", fontSize: "13px" },
  ".cm-content": { fontFamily: "var(--font-mono)", caretColor: "var(--accent)" },
  ".cm-gutters": { backgroundColor: "var(--surface-1)", color: "var(--text-subtle)", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "var(--tint-accent)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--surface-3)", color: "var(--text)" },
  "&.cm-focused": { outline: "2px solid var(--focus)", outlineOffset: "-2px" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": { backgroundColor: "var(--surface-3) !important" },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-tooltip": { backgroundColor: "var(--surface-2)", color: "var(--text)", border: "1px solid var(--border-strong)" },
  ".cm-diagnostic-error": { borderLeftColor: "var(--danger)" },
  ".cm-diagnostic-warning": { borderLeftColor: "var(--warning)" },
});

function toDiagnostics(view: EditorView, problems: Problem[]): Diagnostic[] {
  const doc = view.state.doc;
  return problems.map((p) => {
    const lineNo = Math.min(Math.max(p.line ?? 1, 1), doc.lines);
    const line = doc.line(lineNo);
    const from = p.line ? Math.min(line.from + Math.max((p.column ?? 1) - 1, 0), line.to) : 0;
    const to = p.line ? line.to : Math.min(doc.length, doc.line(1).to);
    return { from, to: Math.max(to, from), severity: p.severity === "error" ? "error" : "warning", message: p.message, source: p.code };
  });
}

export default function CodeEditorImpl({ value, onChange, problems = [], readOnly, height = "420px", ariaLabel, language = "yaml", testId }: CodeEditorProps) {
  const extensions = useMemo<Extension[]>(() => {
    const ext: Extension[] = [studioTheme, EditorView.lineWrapping, EditorView.contentAttributes.of({ "aria-label": ariaLabel })];
    if (language === "yaml") ext.push(yaml());
    ext.push(lintGutter(), linter((view) => toDiagnostics(view, problems), { delay: 0 }));
    return ext;
  }, [problems, ariaLabel, language]);
  return (
    <div data-testid={testId} className="overflow-hidden rounded-sm border border-border-strong">
      <CodeMirror
        value={value}
        onChange={(v) => onChange?.(v)}
        readOnly={readOnly}
        editable={!readOnly}
        height={height}
        theme="none"
        basicSetup={{ foldGutter: false, highlightActiveLine: true, autocompletion: false }}
        extensions={extensions}
      />
    </div>
  );
}
