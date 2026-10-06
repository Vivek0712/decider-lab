import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const LabsPage = lazy(() => import("./LabsPage"));
const NewLabPage = lazy(() => import("./NewLabPage"));
const LabPage = lazy(() => import("./LabPage"));

export const routes: RouteObject[] = [
  { path: "labs", element: <LabsPage /> },
  { path: "labs/new", element: <NewLabPage /> },
  { path: "labs/:labId", element: <LabPage /> },
];
