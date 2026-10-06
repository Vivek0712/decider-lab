import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const OverviewPage = lazy(() => import("./OverviewPage"));

// Routes owned by the Overview area (mounted under the AppShell).
export const routes: RouteObject[] = [{ index: true, element: <OverviewPage /> }];
