import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const ResultsPage = lazy(() => import("./ResultsPage"));
const ComparePage = lazy(() => import("./ComparePage"));
const RunRootPage = lazy(() => import("./RunRootPage"));
const RunPage = lazy(() => import("./RunPage"));

// "results/compare" is listed before "results/:rootId" (react-router ranks static segments first anyway).
export const routes: RouteObject[] = [
  { path: "results", element: <ResultsPage /> },
  { path: "results/compare", element: <ComparePage /> },
  { path: "results/:rootId", element: <RunRootPage /> },
  { path: "results/:rootId/:model/:suite", element: <RunPage /> },
];
