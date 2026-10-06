import { cn } from "@/lib/cn";

/** Loading placeholder in the shape of the final layout. The container should set aria-busy. */
export function Skeleton({
  shape = "line",
  width,
  height,
  lines = 1,
  className,
}: {
  shape?: "line" | "block" | "circle";
  width?: number | string;
  height?: number | string;
  lines?: number;
  className?: string;
}) {
  const base =
    "block animate-[dl-shimmer_1.4s_linear_infinite] bg-[length:800px_100%] bg-gradient-to-r from-surface-2 via-surface-3 to-surface-2";
  if (shape === "line" && lines > 1) {
    return (
      <span aria-hidden className={cn("flex flex-col gap-2", className)}>
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className={cn(base, "h-3 rounded-xs")} style={{ width: i === lines - 1 ? "60%" : width ?? "100%" }} />
        ))}
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className={cn(base, shape === "circle" ? "rounded-full" : shape === "block" ? "rounded-md" : "h-3 rounded-xs", className)}
      style={{ width: width ?? (shape === "circle" ? 24 : "100%"), height: height ?? (shape === "line" ? undefined : shape === "circle" ? 24 : 120) }}
    />
  );
}
