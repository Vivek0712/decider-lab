import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const ModelsPage = lazy(() => import("./ModelsPage"));

export const routes: RouteObject[] = [{ path: "models", element: <ModelsPage /> }];
