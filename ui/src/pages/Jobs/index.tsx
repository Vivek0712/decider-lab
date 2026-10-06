import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const JobsPage = lazy(() => import("./JobsPage"));
const JobPage = lazy(() => import("./JobPage"));

export const routes: RouteObject[] = [
  { path: "jobs", element: <JobsPage /> },
  { path: "jobs/:jobId", element: <JobPage /> },
];
