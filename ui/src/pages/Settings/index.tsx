import { lazy } from "react";
import type { RouteObject } from "react-router-dom";

const SettingsPage = lazy(() => import("./SettingsPage"));

export const routes: RouteObject[] = [{ path: "settings", element: <SettingsPage /> }];
