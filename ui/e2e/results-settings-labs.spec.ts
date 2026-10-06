import { test, expect } from "./fixtures";

// Results (run list, run root views, row explorer, single run, compare), Settings, and the
// Labs -> Run -> Job -> Results loop, against the seeded workspace (first-lab finished; second-lab never run).

test.describe("results", () => {
  test("run list shows the seeded run root and opens it", async ({ page, studio }) => {
    await studio.open("/results");
    await expect(page.getByTestId("page-results")).toBeVisible();
    const card = page.getByTestId("runroot-card-first-lab");
    await expect(card).toBeVisible();
    await page.getByTestId("results-filter").fill("no-such-lab");
    await expect(card).toBeHidden();
    await page.getByTestId("results-filter").fill("first");
    await card.click();
    await expect(page.getByTestId("page-runroot")).toBeVisible();
  });

  test("leaderboard: CIs, proxy notice, suites, raw/cal, row opens the run", async ({ page, studio }) => {
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    const root = roots.items.find((r) => r.lab === "first-lab")!;
    await studio.open(`/results/${encodeURIComponent(root.root_id)}`);
    await expect(page.getByTestId("proxy-notice")).toContainText("local proxy");
    await expect(page.getByTestId("leaderboard")).toBeVisible();
    await expect(page.getByTestId("leaderboard-row-fake")).toContainText("[");
    await page.getByTestId("suite-chip-synthetic").click();
    await expect(page).toHaveURL(/suite=synthetic/);
    await page.getByTestId("scores-toggle-both").click();
    await expect(page.getByTestId("leaderboard-row-fake-cal")).toBeVisible();
    await page.getByTestId("scores-toggle-raw").click();
    await expect(page.getByTestId("leaderboard-row-fake-cal")).toBeHidden();
    await expect(page.getByRole("img", { name: /Intelligence \(local proxy\)/ })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await page.getByTestId("leaderboard-row-fake").click();
    await expect(page.getByTestId("page-run")).toBeVisible();
    await expect(page.getByTestId("run-kpis")).toContainText("Intelligence (proxy)");
    await expect(page.getByTestId("predictions-table")).toBeVisible();
    await page.getByTestId("predictions-filter").getByText("Wrong").click();
    await expect(page.getByTestId("provenance")).toContainText("suite_sha256");
  });

  test("every analysis tab renders", async ({ page, studio }) => {
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    const root = roots.items.find((r) => r.lab === "first-lab")!;
    await studio.open(`/results/${encodeURIComponent(root.root_id)}?suite=synthetic`);
    await page.getByTestId("results-tab-baseline").click();
    await expect(page.getByTestId("forest-plot")).toBeVisible();
    await expect(page.getByTestId("verdict-fake")).toContainText("paired rows");
    await page.getByTestId("results-tab-families").click();
    await expect(page.getByTestId("family-heatmap")).toBeVisible();
    await page.getByTestId("results-tab-calibration").click();
    await expect(page.getByTestId("reliability-fake")).toBeVisible();
    await expect(page.getByTestId("calibration-delta-fake")).toBeVisible();
    await page.getByTestId("results-tab-latency").click();
    await expect(page.getByTestId("latency-chart")).toBeVisible();
    await page.getByTestId("results-tab-provenance").click();
    await expect(page.getByTestId("provenance")).toContainText("fake");
  });

  test("row explorer filters and opens a row with every model's answer", async ({ page, studio }) => {
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    const root = roots.items.find((r) => r.lab === "first-lab")!;
    await studio.open(`/results/${encodeURIComponent(root.root_id)}?suite=synthetic&tab=rows`);
    const table = page.getByTestId("rows-table");
    await expect(table).toBeVisible();
    await page.getByTestId("rows-show-wrong").click();
    await expect(table.locator("[data-testid^='row-']").first()).toBeVisible();
    await table.locator("[data-testid^='row-']").first().click();
    const detail = page.getByTestId("row-detail");
    await expect(detail).toBeVisible();
    await expect(detail.getByTestId("row-answer-fake")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(detail).toBeHidden();
  });

  test("compare two runs from the leaderboard selection", async ({ page, studio }) => {
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    const root = roots.items.find((r) => r.lab === "first-lab")!;
    await studio.open(`/results/${encodeURIComponent(root.root_id)}?suite=synthetic`);
    await expect(page.getByRole("img", { name: /Intelligence \(local proxy\)/ })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await page.getByTestId("leaderboard-select-fake").check();
    await page.getByTestId("leaderboard-select-majority").check();
    await page.getByTestId("leaderboard-compare").click();
    await expect(page.getByTestId("page-compare")).toBeVisible();
    const res = page.getByTestId("compare-result");
    await expect(res).toBeVisible();
    await expect(res.getByTestId("compare-intelligence")).toContainText("95% CI");
    await page.getByTestId("compare-swap").click();
    await expect(res.getByTestId("compare-intelligence")).toContainText("95% CI");
  });

  test("export links point at the report files", async ({ page, studio }) => {
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    const root = roots.items.find((r) => r.lab === "first-lab")!;
    await studio.open(`/results/${encodeURIComponent(root.root_id)}`);
    await expect(page.getByTestId("export-report-json")).toHaveAttribute("href", /report\.json/);
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-report-json").click()]);
    expect(dl.suggestedFilename()).toContain("report");
  });

  test("results pages pass axe", async ({ page, studio }) => {
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    const root = roots.items.find((r) => r.lab === "first-lab")!;
    await studio.open(`/results/${encodeURIComponent(root.root_id)}`);
    await expect(page.getByTestId("leaderboard")).toBeVisible();
    await studio.axe();
    await studio.open("/results");
    await expect(page.getByTestId("runroot-card-first-lab")).toBeVisible();
    await studio.axe();
  });
});

test("delete a run root through the typed confirmation", async ({ page, studio }) => {
  test.setTimeout(90_000);
  const job = await studio.api<{ job_id: string }>("/api/jobs", { method: "POST", body: {
    kind: "eval", model: { type: "baseline", value: "uniform" }, name: "to-delete", suite: "smoke", split: null, limit: 3, workers: 1, vision: false, out: "scratch/to-delete/smoke" } });
  for (let i = 0; i < 60; i++) {
    const j = await studio.api<{ status: string }>(`/api/jobs/${job.job_id}`);
    if (j.status === "succeeded") break;
    await page.waitForTimeout(1000);
  }
  const roots = await studio.api<{ items: { root_id: string; lab: string; path: string }[] }>("/api/runs");
  const r = roots.items.find((x) => x.path === "scratch")!;
  expect(r, "the eval wrote its own run root").toBeTruthy();
  const full = await studio.api<{ title: string }>(`/api/runs/${encodeURIComponent(r.root_id)}`);
  await studio.open(`/results/${encodeURIComponent(r.root_id)}`);
  await page.getByTestId("results-delete").click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toBeVisible();
  await dlg.getByRole("textbox").fill("wrong phrase");
  await expect(dlg.getByRole("button", { name: "Delete run root" })).toBeDisabled();
  await dlg.getByRole("textbox").fill(`delete ${full.title}`);
  await dlg.getByRole("button", { name: "Delete run root" }).click();
  await expect(page.getByTestId("page-results")).toBeVisible();
  await expect(page.getByTestId(`runroot-card-${r.lab}`)).toHaveCount(0);
});

test.describe("settings", () => {
  test("shows workspace, env names without values, and saves preferences", async ({ page, studio }) => {
    await studio.open("/settings");
    await expect(page.getByTestId("settings-workspace")).toBeVisible();
    const env = page.getByTestId("settings-env");
    await expect(env).toBeVisible();
    await expect(page.locator("body")).not.toContainText("sk-e2e-secret-value");
    await page.getByTestId("settings-backend").selectOption("vast");
    await expect(page.getByTestId("toast").filter({ hasText: "Saved" })).toBeVisible();
    const s = await studio.api<{ default_backend: string }>("/api/settings");
    expect(s.default_backend).toBe("vast");
    await page.getByTestId("settings-backend").selectOption("local");
    await page.getByTestId("settings-theme").getByText("Light").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await studio.axe();
  });
});

test.describe("labs to results", () => {
  test("run a lab locally, follow the job, open the results", async ({ page, studio }) => {
    test.setTimeout(120_000);
    await studio.open("/labs");
    await page.getByTestId("lab-row-second-lab").click();
    await expect(page.getByTestId("page-lab")).toBeVisible();
    await page.getByTestId("lab-run").click();
    const dlg = page.getByTestId("run-dialog");
    await expect(dlg).toBeVisible();
    await dlg.getByTestId("run-backend-local").click();
    await expect(dlg.getByTestId("run-command")).toContainText("decider-lab run");
    await dlg.getByTestId("run-start").click();
    await expect(page.getByTestId("page-job")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("job-status")).toContainText(/succeeded/i, { timeout: 90_000 });
    await page.getByTestId("job-tab-logs").click();
    await expect(page.getByTestId("log-viewer")).toContainText("decider-lab");
    await page.getByTestId("job-tab-progress").click();
    await page.getByTestId("job-result-link").click();
    await expect(page.getByTestId("page-runroot")).toBeVisible();
    await expect(page.getByTestId("leaderboard-row-random")).toBeVisible();
    // leave the shared workspace as the other specs expect it: delete the run root this test made
    const roots = await studio.api<{ items: { root_id: string; lab: string }[] }>("/api/runs");
    for (const r of roots.items.filter((x) => x.lab === "second-lab")) {
      const full = await studio.api<{ title: string }>(`/api/runs/${encodeURIComponent(r.root_id)}`);
      await studio.api(`/api/runs/${encodeURIComponent(r.root_id)}`, { method: "DELETE", body: { confirm: `delete ${full.title}` } });
    }
  });
});
