// Chart palette (DESIGN.md 5.1): models get --chart-<1+i%6> in lab order; the baseline is grey.
// More than 6 models repeat colors with a dash pattern.

export const chartColor = (colorIndex: number | null | undefined, isBaseline = false): string =>
  isBaseline || colorIndex == null ? "var(--chart-baseline)" : `var(--chart-${1 + (colorIndex % 6)})`;

export const chartDash = (colorIndex: number | null | undefined): string | undefined => {
  if (colorIndex == null) return undefined;
  const cycle = Math.floor(colorIndex / 6) % 3;
  return cycle === 0 ? undefined : cycle === 1 ? "4 2" : "1 2";
};

export const AXIS = { tick: "var(--text-subtle)", line: "var(--border)", grid: "var(--border)", ref: "var(--text-muted)" };
