import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const DataPage = lazy(() => import("./DataPage"));

export const routes: RouteObject[] = [{ path: "data", element: <DataPage /> }];
