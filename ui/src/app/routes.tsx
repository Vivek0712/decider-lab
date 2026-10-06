import { Suspense, type ReactNode } from "react";
import type { RouteObject } from "react-router-dom";
import { Skeleton } from "@/components/Skeleton";
import { routes as overview } from "@/pages/Overview";
import { routes as labs } from "@/pages/Labs";
import { routes as jobs } from "@/pages/Jobs";
import { routes as results } from "@/pages/Results";
import { routes as models } from "@/pages/Models";
import { routes as data } from "@/pages/Data";
import { routes as compute } from "@/pages/Compute";
import { routes as settings } from "@/pages/Settings";
import NotFound from "@/pages/NotFound";
import { AppShell } from "./AppShell";

function PageFallback() {
  return (
    <div aria-busy="true" aria-label="Loading page">
      <Skeleton width={220} height={28} shape="block" className="mb-3" />
      <Skeleton width="50%" className="mb-6" />
      <Skeleton shape="block" height={240} />
    </div>
  );
}

const suspend = (element: ReactNode) => <Suspense fallback={<PageFallback />}>{element}</Suspense>;
const wrap = (rs: RouteObject[]): RouteObject[] => rs.map((r) => ({ ...r, element: suspend(r.element) }) as RouteObject);

// Each area owns src/pages/<Area>/index.tsx (its `routes`); this file only composes them.
export const appRoutes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [...wrap([...overview, ...labs, ...jobs, ...results, ...models, ...data, ...compute, ...settings]), { path: "*", element: <NotFound /> }],
  },
];
