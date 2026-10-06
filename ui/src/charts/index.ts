// Chart building blocks (DESIGN.md 7). Results/Jobs charts (ForestPlot, FamilyHeatmap,
// ReliabilityDiagram, LatencyChart, JevBenchPanel, LabelBalanceBars) are built by their areas on
// these: ChartFrame for title/caption/aria/table toggle, palette for model colors.
export { BarSeriesChart } from "./BarSeriesChart";
export { ChartFrame, type TableData } from "./ChartFrame";
export { CIBar, ciDomain } from "./CIBar";
export { AXIS, chartColor, chartDash } from "./palette";
export { Sparkline } from "./Sparkline";
export { TimeSeriesChart, type Series } from "./TimeSeriesChart";
