import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ApiError } from "@/lib/api";
import { LiveRegionProvider } from "@/components/LiveRegion";
import { ToastProvider } from "@/components/Toast";
import { ThemeProvider } from "@/theme/ThemeProvider";
import { appRoutes } from "./routes";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: true,
      // never retry client errors (4xx); retry network/5xx twice
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
    mutations: { retry: false },
  },
});

const router = createBrowserRouter(appRoutes);

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <LiveRegionProvider>
          <ToastProvider>
            <RouterProvider router={router} />
          </ToastProvider>
        </LiveRegionProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
