import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

// e2e against the real Studio server: scripts/e2e_server.py seeds a fresh workspace (a lab with a
// fake System One server and baselines, already run), sets DECIDER_LAB_FAKE_CLOUD=1, and serves the
// built SPA from src/decider_lab/ui/static. `npm run test:e2e` builds first.
//
//   E2E_PORT (default 7871)   E2E_TOKEN (default e2e-token-0123456789)   PYTHON (default ../.venv/bin/python)
//
// One server is shared by every spec, so specs run serially (workers: 1) and each test must create
// the state it changes (new labs, its own jobs) or restore it.
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
export const E2E_PORT = Number(process.env.E2E_PORT ?? 7871);
export const E2E_TOKEN = process.env.E2E_TOKEN ?? "e2e-token-0123456789";
const python = process.env.PYTHON ?? path.join(repo, ".venv", "bin", "python");

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${E2E_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    colorScheme: "dark",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } },
  ],
  webServer: {
    command: `"${python}" "${path.join(repo, "scripts", "e2e_server.py")}"`,
    cwd: repo,
    url: `http://127.0.0.1:${E2E_PORT}/api/health`,
    env: { E2E_PORT: String(E2E_PORT), E2E_TOKEN, DECIDER_LAB_FAKE_CLOUD: "1" },
    reuseExistingServer: !process.env.CI && process.env.E2E_REUSE === "1",
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
