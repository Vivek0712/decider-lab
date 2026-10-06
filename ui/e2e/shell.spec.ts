// Foundation smoke test: the shell renders, navigation works, the theme toggles and persists,
// the command palette opens and runs commands, auth works both ways.
import { expect, test, TOKEN } from "./fixtures";

const PAGES = ["overview", "labs", "jobs", "results", "models", "data", "compute", "settings"] as const;
const PATHS: Record<(typeof PAGES)[number], string> = {
  overview: "/", labs: "/labs", jobs: "/jobs", results: "/results", models: "/models", data: "/data", compute: "/compute", settings: "/settings",
};

test("without the token the server shows the open-the-link page", async ({ page }) => {
  const res = await page.goto("/labs");
  expect(res?.status()).toBe(401);
  await expect(page.getByTestId("login-page")).toContainText("printed in your terminal");
  const api = await page.request.get("/api/meta");
  expect(api.status()).toBe(401);
});

test("the token link logs in and leaves the address bar", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page).toHaveURL(/\/$/);
  expect(page.url()).not.toContain("token");
  await expect(page.getByTestId("page-overview")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
});

test("shell renders with workspace, fake cloud pill and jobs badge", async ({ page, studio }) => {
  await studio.open();
  await expect(page.getByTestId("topbar-palette")).toBeVisible();
  await expect(page.getByTestId("topbar-jobs-badge")).toBeVisible();
  await expect(page.getByTestId("topbar-theme")).toBeVisible();
  if (!studio.isMobile) {
    await expect(page.getByTestId("fake-cloud-pill")).toHaveText(/fake cloud/i);
    await expect(page.getByTestId("topbar-workspace")).toContainText("ws");
  } else {
    await page.getByTestId("topbar-menu").click();
    await expect(page.getByTestId("nav-drawer").getByTestId("fake-cloud-pill")).toBeVisible();
    await page.keyboard.press("Escape");
  }
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(page.viewportSize()!.width);
});

test("navigation reaches every page", async ({ page, studio }) => {
  await studio.open();
  for (const id of [...PAGES.slice(1), "overview" as const]) {
    await studio.nav(id);
    await expect(page).toHaveURL(new RegExp(`${PATHS[id].replace("/", "\\/")}$`));
    await expect(page.getByTestId(`page-${id}`)).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  }
});

test("deep links reload into the SPA and unknown paths show not found", async ({ page, studio }) => {
  await studio.open("/results/compare");
  await expect(page.getByTestId("page-compare")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("page-compare")).toBeVisible();
  await page.goto("/no/such/page");
  await expect(page.getByTestId("page-not-found")).toBeVisible();
});

test("theme toggle cycles and persists after reload", async ({ page, studio }) => {
  await studio.open();
  const html = page.locator("html");
  const toggle = page.getByTestId("topbar-theme");
  await expect(toggle).toHaveAttribute("data-theme-pref", "system");
  await expect(html).toHaveAttribute("data-theme", "dark"); // colorScheme: dark
  await toggle.click(); // system -> dark
  await expect(toggle).toHaveAttribute("data-theme-pref", "dark");
  await toggle.click(); // dark -> light
  await expect(html).toHaveAttribute("data-theme", "light");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).toBe("rgb(245, 248, 244)"); // #F5F8F4
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "light");
  await expect(page.getByTestId("topbar-theme")).toHaveAttribute("data-theme-pref", "light");
  await page.getByTestId("topbar-theme").click(); // light -> system
  await expect(html).toHaveAttribute("data-theme", "dark");
});

test("command palette opens with Mod+K, filters and navigates", async ({ page, studio }) => {
  await studio.open();
  await page.keyboard.press("ControlOrMeta+k");
  const input = page.getByTestId("palette-input");
  await expect(input).toBeFocused();
  await input.fill("results");
  await expect(page.getByTestId("palette-item-nav.results")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("command-palette")).toHaveCount(0);
  await expect(page).toHaveURL(/\/results$/);
  await expect(page.getByTestId("page-results")).toBeVisible();

  // the topbar button opens it too; `>` limits to commands; Esc closes and restores focus
  await page.getByTestId("topbar-palette").click();
  await input.fill(">theme light");
  await page.getByTestId("palette-item-theme.light").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByTestId("topbar-palette").click();
  await expect(page.getByTestId("palette-item-theme.light")).toBeVisible(); // in Recent
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("command-palette")).toHaveCount(0);
  await expect(page.getByTestId("topbar-palette")).toBeFocused();
});

test("g-chords navigate and ? opens shortcut help", async ({ page, studio }) => {
  test.skip(studio.isMobile, "keyboard chords are a desktop feature");
  await studio.open();
  await page.locator("body").click();
  await page.keyboard.press("g");
  await page.keyboard.press("c");
  await expect(page).toHaveURL(/\/compute$/);
  await page.keyboard.press("Shift+?");
  await expect(page.getByTestId("shortcut-help")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("shortcut-help")).toHaveCount(0);
});

test("the shell has no serious accessibility violations in either theme", async ({ page, studio }) => {
  await studio.open();
  await studio.axe();
  await page.getByTestId("topbar-theme").click();
  await page.getByTestId("topbar-theme").click(); // light
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await studio.axe();
});
