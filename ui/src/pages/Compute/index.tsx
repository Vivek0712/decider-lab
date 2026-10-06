import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const ComputePage = lazy(() => import("./ComputePage"));

export const routes: RouteObject[] = [{ path: "compute", element: <ComputePage /> }];
