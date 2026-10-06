import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAnnounce } from "./LiveRegion";

export function useCopy(): [(text: string) => Promise<void>, boolean] {
  const [copied, setCopied] = useState(false);
  const announce = useAnnounce();
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    announce("Copied");
    window.setTimeout(() => setCopied(false), 1500);
  };
  return [copy, copied];
}

export function CopyButton({ text, label = "Copy", className }: { text: string; label?: string; className?: string }) {
  const [copy, copied] = useCopy();
  return (
    <button
      type="button"
      onClick={() => copy(text)}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      className={cn(
        "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-muted hover:bg-surface-3 hover:text-text max-sm:h-11 max-sm:w-11",
        className,
      )}
    >
      {copied ? <Check size={14} aria-hidden className="text-success" /> : <Copy size={14} aria-hidden />}
    </button>
  );
}

/** Inline code (paths, ids, a command), optionally with a copy button. */
export function CodeInline({ code, copy, className }: { code: string; copy?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex max-w-full items-center gap-1 align-middle", className)}>
      <code className="min-w-0 truncate rounded-xs bg-surface-2 px-1.5 py-0.5 font-mono text-mono text-text">{code}</code>
      {copy && <CopyButton text={code} label={`Copy ${code.length > 40 ? "command" : code}`} />}
    </span>
  );
}

/** A block of code (commands, YAML, JSON) with a copy button. */
export function CodeBlock({
  code,
  copy = true,
  language,
  className,
  "data-testid": testId,
}: {
  code: string;
  copy?: boolean;
  language?: "bash" | "yaml" | "json" | "text";
  className?: string;
  "data-testid"?: string;
}) {
  return (
    <div className={cn("relative min-w-0 rounded-sm border border-border bg-surface-2", className)} data-testid={testId}>
      <pre className="scrollbar-thin overflow-x-auto p-3 pr-10 font-mono text-mono text-text" data-language={language}>
        <code>{code}</code>
      </pre>
      {copy && <CopyButton text={code} label="Copy" className="absolute right-1.5 top-1.5" />}
    </div>
  );
}
