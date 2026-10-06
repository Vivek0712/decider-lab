import { cn } from "@/lib/cn";
import { modKey } from "@/lib/platform";

/** Keyboard keys; "mod" renders ⌘ on macOS and Ctrl elsewhere. */
export function Kbd({ keys, className }: { keys: string[]; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      {keys.map((k, i) => (
        <kbd
          key={i}
          className="inline-flex h-5 min-w-5 items-center justify-center rounded-xs border border-border bg-surface-2 px-1 font-sans text-caption text-muted"
        >
          {k.toLowerCase() === "mod" ? modKey : k}
        </kbd>
      ))}
    </span>
  );
}
