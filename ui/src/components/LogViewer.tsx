import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ChevronDown, ChevronUp, Download, WrapText } from "lucide-react";
import type { LogLevel, LogLine } from "@/api/types";
import { getJobLog, jobEvents } from "@/api/jobs";
import { fileUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtClock } from "@/lib/format";
import { useEventSource } from "@/hooks/useEventSource";
import { Checkbox } from "./Field";
import { ErrorState } from "./ErrorState";
import { Skeleton } from "./Skeleton";

const ROW = 20; // px per line when not wrapping (virtualized)
const OVERSCAN = 40;

type LevelFilter = "all" | "warn" | "error";

function HighlightText({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  const ql = q.toLowerCase();
  let i = 0;
  let k = 0;
  for (;;) {
    const j = lower.indexOf(ql, i);
    if (j < 0) break;
    if (j > i) parts.push(text.slice(i, j));
    parts.push(
      <mark key={k++} className="rounded-[2px] text-text" style={{ background: "var(--tint-warning)", outline: "1px solid var(--warning)" }}>
        {text.slice(j, j + q.length)}
      </mark>,
    );
    i = j + q.length;
  }
  parts.push(text.slice(i));
  return <>{parts}</>;
}

/**
 * Log viewer (presentational): monospace lines with time, level filter, search with match
 * navigation (Enter / Shift+Enter), follow (turns off when you scroll up; a "Jump to latest" pill
 * shows new lines), wrap, download. Virtualized when not wrapping. role="log", not live; a polite
 * summary is announced every 10 s instead.
 */
export function LogViewer({
  lines,
  follow: followProp,
  onFollowChange,
  height = 480,
  downloadHref,
  loading,
  toolbarExtra,
  className,
}: {
  lines: LogLine[];
  follow?: boolean;
  onFollowChange?: (f: boolean) => void;
  height?: number | string;
  downloadHref?: string;
  loading?: boolean;
  toolbarExtra?: ReactNode;
  className?: string;
}) {
  const [followInner, setFollowInner] = useState(true);
  const follow = followProp ?? followInner;
  const setFollow = useCallback((f: boolean) => (onFollowChange ? onFollowChange(f) : setFollowInner(f)), [onFollowChange]);
  const [q, setQ] = useState("");
  const [level, setLevel] = useState<LevelFilter>("all");
  const [wrap, setWrap] = useState(false);
  const [matchIdx, setMatchIdx] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(400);
  const [seenCount, setSeenCount] = useState(0);
  const [summary, setSummary] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const programmatic = useRef(false);

  const visible = useMemo(
    () => lines.filter((l) => level === "all" || (level === "error" ? l.level === "error" : l.level !== "info")),
    [lines, level],
  );
  const matches = useMemo(() => {
    if (!q) return [] as number[];
    const ql = q.toLowerCase();
    const out: number[] = [];
    visible.forEach((l, i) => l.text.toLowerCase().includes(ql) && out.push(i));
    return out;
  }, [visible, q]);

  useEffect(() => setMatchIdx(0), [q, level]);

  const scrollToIndex = (i: number) => {
    const el = box.current;
    if (!el) return;
    programmatic.current = true;
    if (wrap) {
      el.querySelector<HTMLElement>(`[data-idx="${i}"]`)?.scrollIntoView({ block: "center" });
    } else {
      el.scrollTop = Math.max(0, i * ROW - el.clientHeight / 2);
    }
  };

  const gotoMatch = (dir: 1 | -1) => {
    if (!matches.length) return;
    setFollow(false);
    const n = (matchIdx + dir + matches.length) % matches.length;
    setMatchIdx(n);
    scrollToIndex(matches[n]);
  };

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setViewH(el.clientHeight);
    if (follow) {
      programmatic.current = true;
      el.scrollTop = el.scrollHeight;
      setSeenCount(visible.length);
    }
  }, [visible.length, follow, wrap]);

  useEffect(() => {
    const t = window.setInterval(() => {
      const errors = lines.filter((l) => l.level === "error").length;
      setSummary(`${lines.length} lines, ${errors} error${errors === 1 ? "" : "s"}`);
    }, 10_000);
    return () => window.clearInterval(t);
  }, [lines]);

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    if (programmatic.current) {
      programmatic.current = false;
      return;
    }
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < ROW * 2;
    if (!atBottom && follow) setFollow(false);
    if (atBottom && !follow) setFollow(true);
  };

  const start = wrap ? 0 : Math.max(0, Math.floor(scrollTop / ROW) - OVERSCAN);
  const end = wrap ? visible.length : Math.min(visible.length, Math.ceil((scrollTop + viewH) / ROW) + OVERSCAN);
  const current = matches[matchIdx];
  const newLines = follow ? 0 : Math.max(0, visible.length - seenCount);

  return (
    <div className={cn("flex min-w-0 flex-col rounded-md border border-border bg-surface-1", className)}>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 flex-1 items-center gap-1 sm:max-w-[360px]">
          <label className="sr-only" htmlFor="log-search">
            Search logs
          </label>
          <input
            id="log-search"
            data-testid="log-search"
            type="search"
            placeholder="Search logs…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                gotoMatch(e.shiftKey ? -1 : 1);
              } else if (e.key === "Escape") setQ("");
            }}
            className="h-8 min-w-0 flex-1 rounded-sm border border-border-strong bg-surface-2 px-2 text-small text-text placeholder:text-subtle max-sm:min-h-[44px]"
          />
          <span className="tnum min-w-[48px] text-center text-caption text-muted" aria-live="off" data-testid="log-match-count">
            {q ? (matches.length ? `${matchIdx + 1}/${matches.length}` : "0/0") : ""}
          </span>
          <button type="button" aria-label="Previous match" onClick={() => gotoMatch(-1)} className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-muted hover:bg-surface-3">
            <ChevronUp size={16} aria-hidden />
          </button>
          <button type="button" aria-label="Next match" onClick={() => gotoMatch(1)} className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-muted hover:bg-surface-3">
            <ChevronDown size={16} aria-hidden />
          </button>
        </div>
        <label className="sr-only" htmlFor="log-level">
          Level
        </label>
        <select
          id="log-level"
          data-testid="log-level"
          value={level}
          onChange={(e) => setLevel(e.target.value as LevelFilter)}
          className="h-8 rounded-sm border border-border-strong bg-surface-2 px-2 text-small text-text"
        >
          <option value="all">All levels</option>
          <option value="warn">Warnings and errors</option>
          <option value="error">Errors</option>
        </select>
        <Checkbox label="Follow" checked={follow} onChange={setFollow} data-testid="log-follow" />
        <button
          type="button"
          aria-pressed={wrap}
          onClick={() => setWrap(!wrap)}
          className={cn("inline-flex h-8 items-center gap-1 rounded-sm px-2 text-small", wrap ? "bg-tint-accent text-accent" : "text-muted hover:bg-surface-3")}
        >
          <WrapText size={14} aria-hidden /> Wrap
        </button>
        {downloadHref && (
          <a href={downloadHref} download className="inline-flex h-8 items-center gap-1 rounded-sm px-2 text-small text-muted no-underline hover:bg-surface-3 hover:text-text">
            <Download size={14} aria-hidden /> Download
          </a>
        )}
        {toolbarExtra}
      </div>
      <div className="relative">
        <div
          ref={box}
          role="log"
          aria-live="off"
          aria-label="Job log"
          data-testid="log-viewer"
          tabIndex={0}
          onScroll={onScroll}
          style={{ height }}
          className="scrollbar-thin overflow-auto bg-[var(--bg)] py-1 font-mono text-[12.5px] leading-5"
          aria-busy={loading || undefined}
        >
          {loading && (
            <div className="p-3">
              <Skeleton lines={6} />
            </div>
          )}
          {!loading && visible.length === 0 && <div className="p-3 text-small text-subtle">{lines.length ? "No lines match this level." : "No output yet."}</div>}
          <div style={wrap ? undefined : { height: visible.length * ROW, position: "relative" }}>
            {visible.slice(start, end).map((l, k) => {
              const i = start + k;
              return (
                <div
                  key={l.seq}
                  data-idx={i}
                  data-seq={l.seq}
                  data-level={l.level}
                  style={wrap ? undefined : { position: "absolute", top: i * ROW, left: 0, right: 0, height: ROW }}
                  className={cn(
                    "flex gap-3 border-l-2 px-3",
                    wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre",
                    l.level === "error" ? "border-danger" : l.level === "warn" ? "border-warning" : "border-transparent",
                    i === current && "bg-surface-3",
                  )}
                >
                  <span className="tnum shrink-0 select-none text-subtle">{fmtClock(l.ts)}</span>
                  <span className={cn("min-w-0", l.level === "error" ? "text-danger" : "text-text")}>
                    <HighlightText text={l.text} q={q} />
                  </span>
                </div>
              );
            })}
          </div>
        </div>
        {newLines > 0 && (
          <button
            type="button"
            onClick={() => setFollow(true)}
            className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full bg-accent px-3 py-1 text-small font-semibold text-on-accent shadow-elev-2"
          >
            <ArrowDown size={14} aria-hidden /> Jump to latest ({newLines} new)
          </button>
        )}
      </div>
      <div className="sr-only" aria-live="polite">
        {summary}
      </div>
    </div>
  );
}

export function levelOf(text: string): LogLevel {
  if (/FAILED|Traceback|Error|failed|WARNING: instance/.test(text)) return "error";
  if (/WARNING|WARN|too few|⚠/.test(text)) return "warn";
  return "info";
}

/**
 * A job's log, live: loads history with GET /api/jobs/:id/log, then follows the job's SSE stream
 * from the next line (API.md 6). Needs the Jobs area endpoints.
 */
export function JobLogViewer({ jobId, height }: { jobId: string; height?: number | string }) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const lastSeq = useRef(-1);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setError(null);
    lastSeq.current = -1;
    getJobLog(jobId, { offset: 0, limit: 20000 })
      .then((page) => {
        if (cancelled) return;
        setLines(page.lines);
        lastSeq.current = page.lines.length ? page.lines[page.lines.length - 1].seq : -1;
        setReady(true);
      })
      .catch((e) => !cancelled && setError(e));
    return () => {
      cancelled = true;
    };
  }, [jobId, attempt]);

  useEventSource(
    () => jobEvents(jobId, lastSeq.current >= 0 ? { last_event_id: lastSeq.current } : { from: 0 }),
    {
      log: (l: LogLine) => {
        if (l.seq <= lastSeq.current) return;
        lastSeq.current = l.seq;
        setLines((xs) => [...xs, l]);
      },
    },
    { enabled: ready, deps: [jobId, ready] },
  );

  if (error) return <ErrorState error={error} onRetry={() => setAttempt((a) => a + 1)} />;
  return <LogViewer lines={lines} loading={!ready} height={height} downloadHref={fileUrl(`/api/jobs/${jobId}/log.txt`)} />;
}
