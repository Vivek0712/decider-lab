// Shared e2e helpers. Import { test, expect } from "./fixtures" instead of @playwright/test.
//
//   test("something", async ({ page, studio }) => {
//     await studio.open("/labs");                 // logs in with the token, then navigates
//     await studio.nav("results");                // clicks the sidebar link (or the mobile drawer)
//     const meta = await studio.api("/api/meta");  // authed API call from the test
//   });
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";

export const TOKEN = process.env.E2E_TOKEN ?? "e2e-token-0123456789";

export type Studio = {
  /** First load with ?token= (sets the session cookie), then go to `path`. */
  open: (path?: string) => Promise<void>;
  /** Click a main navigation item by id (overview, labs, jobs, results, models, data, compute, settings). */
  nav: (id: string) => Promise<void>;
  /** Authed JSON request to the API (for setup and assertions, not instead of clicking). */
  api: <T = unknown>(path: string, init?: { method?: string; body?: unknown }) => Promise<T>;
  isMobile: boolean;
  /** Run axe on the current page; fails on serious or critical violations. */
  axe: () => Promise<void>;
};

async function login(page: Page) {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByTestId("sidebar").or(page.getByTestId("topbar-menu"))).toBeVisible();
}

export const test = base.extend<{ studio: Studio }>({
  studio: async ({ page, request, viewport }, use) => {
    const isMobile = (viewport?.width ?? 1440) < 1024;
    const studio: Studio = {
      isMobile,
      open: async (path = "/") => {
        await login(page);
        if (path !== "/") await page.goto(path);
      },
      nav: async (id: string) => {
        if (isMobile) {
          await page.getByTestId("topbar-menu").click();
          await page.getByTestId("nav-drawer").getByTestId(`nav-${id}`).click();
        } else {
          await page.getByTestId(`nav-${id}`).click();
        }
      },
      api: async <T,>(path: string, init?: { method?: string; body?: unknown }) => {
        const res = await request.fetch(path, {
          method: init?.method ?? "GET",
          headers: { Authorization: `Bearer ${TOKEN}`, "X-Studio": "1" },
          data: init?.body,
        });
        if (!res.ok()) throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status()}: ${await res.text()}`);
        return (await res.json()) as T;
      },
      axe: async () => {
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
        const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        expect(bad.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")})`)).toEqual([]);
      },
    };
    await use(studio);
  },
});

export { expect };
