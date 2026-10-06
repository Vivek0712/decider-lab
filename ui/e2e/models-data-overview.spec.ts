// Models, Data and Overview: every control of the three pages, against the real server on the seeded
// workspace (first-lab already run on smoke and synthetic:per_kind=40 with +cal; data/eval.jsonl holds
// 30 smoke rows; DECIDER_LAB_FAKE_CLOUD=1). Objects a test creates carry the project name so the
// desktop and mobile runs do not collide on the shared server.
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test } from "./fixtures";

const SHA_A = "bb282d786bc251fd4e3068de3ada9ddbb38127cd";

type OverviewBody = { kpis: Record<string, unknown> & { best: { root_id: string } | null }; recent_results: { root_id: string; model: string; suite: string }[] };

// ---- Overview ------------------------------------------------------------------------------------

test.describe("overview", () => {
  test("KPIs, recent results and run roots reflect the seeded workspace", async ({ page, studio }) => {
    await studio.open("/");
    await expect(page.getByTestId("page-overview")).toBeVisible();
    await expect(page.getByTestId("kpi-labs")).toContainText("3");
    await expect(page.getByTestId("kpi-labs")).toContainText("1 invalid");
    await expect(page.getByTestId("kpi-runs")).toContainText(/run root/);
    const top = page.getByTestId("kpi-top");
    await expect(top).toContainText("Top on synthetic");
    await expect(top).toContainText("(proxy)");
    await expect(top).toContainText("fake");
    await expect(page.getByTestId("kpi-spend")).toContainText("$2.473/h");
    await expect(page.getByTestId("kpi-spend")).toContainText("2 idle");
    await expect(page.getByTestId("kpi-vast")).toContainText("$25.40");
    const aws = page.getByTestId("kpi-aws");
    await expect(aws).toContainText("••••9012");
    await expect(aws).not.toContainText("123456789012");
    await page.getByTestId("kpi-aws-reveal").click();
    await expect(aws).toContainText("123456789012");
    await page.getByTestId("kpi-aws-reveal").click();
    await expect(aws).not.toContainText("123456789012");
    await expect(page.getByTestId("kpi-models")).toBeVisible();
    await expect(page.getByTestId("kpi-jobs")).toBeVisible();

    await expect(page.getByTestId("recent-results")).toBeVisible();
    await expect(page.getByTestId("recent-row-majority-smoke")).toContainText("baseline");
    await expect(page.getByTestId("proxy-notice")).toContainText("it is not a JevBench board score");
    await expect(page.getByTestId("recent-root-first-lab")).toContainText("synthetic");
    // the Overview never draws a shared CI bar across suites
    await expect(page.locator("[data-testid=recent-results] svg[data-chart=cibar]")).toHaveCount(0);

    const body = await studio.api<OverviewBody>("/api/overview");
    const row = body.recent_results.find((r) => r.model === "fake" && r.suite === "synthetic")!;
    await page.getByTestId("recent-row-fake-synthetic").click();
    await expect(page).toHaveURL(new RegExp(`/results/${row.root_id}/fake/synthetic$`));
  });

  test("KPI cards link to their pages", async ({ page, studio }) => {
    await studio.open("/");
    await page.getByTestId("kpi-labs").click();
    await expect(page).toHaveURL(/\/labs\?invalid=1$/);
    await studio.open("/");
    await page.getByTestId("kpi-top").click();
    await expect(page).toHaveURL(/\/results\/[A-Za-z0-9_-]+$/);
    await studio.open("/");
    await page.getByTestId("kpi-spend").click();
    await expect(page).toHaveURL(/\/compute\?idle=1$/);
    await studio.open("/");
    await page.getByTestId("kpi-jobs").click();
    await expect(page).toHaveURL(/\/jobs\?status=active$/);
    await studio.open("/");
    await page.getByTestId("kpi-models").click();
    await expect(page).toHaveURL(/\/models$/);
  });

  test("quick actions navigate or open their dialogs", async ({ page, studio }) => {
    const cases: [string, RegExp][] = [
      ["qa-new-lab", /\/labs\/new$/],
      ["qa-pull", /\/models\?tab=pull$/],
      ["qa-csv", /\/data\?tab=csv$/],
      ["qa-compare", /\/results\/compare$/],
      ["qa-doctor", /\/compute\?tab=local$/],
      ["qa-models", /\/models$/],
    ];
    for (const [id, url] of cases) {
      await studio.open("/");
      await page.getByTestId(id).click();
      await expect(page).toHaveURL(url);
    }
    await studio.open("/");
    await page.getByTestId("overview-new-lab").click();
    await expect(page).toHaveURL(/\/labs\/new$/);

    await studio.open("/");
    await page.getByTestId("qa-run-lab").click();
    const dlg = page.getByTestId("run-lab-dialog");
    await expect(dlg).toBeVisible();
    const select = dlg.getByTestId("run-lab-select");
    await expect(select.locator("option")).toHaveCount(2); // first-lab and second-lab (broken-lab is invalid)
    await select.selectOption({ label: "second-lab (labs/second/lab.yaml)" });
    await expect(dlg).toContainText("decider-lab run labs/second/lab.yaml");
    await dlg.getByTestId("run-lab-open").click();
    await expect(page).toHaveURL(/\/labs\/[A-Za-z0-9_-]+\?run=1$/);
  });

  test("quick eval runs a baseline on smoke and the result appears", async ({ page, studio }, info) => {
    const name = `qe-${info.project.name}`;
    await studio.open("/?quick-eval=1");
    const dlg = page.getByTestId("quick-eval-dialog");
    await expect(dlg).toBeVisible();
    // each model type shows its own field
    await dlg.getByTestId("quick-eval-type-url").click();
    await dlg.getByTestId("quick-eval-value").fill("not a url");
    await expect(dlg).toContainText("The http(s) URL of a System One server.");
    await expect(dlg.getByTestId("quick-eval-start")).toBeDisabled();
    await dlg.getByTestId("quick-eval-type-python").click();
    await dlg.getByTestId("quick-eval-value").fill("my_model:Heuristic");
    await expect(dlg.getByTestId("quick-eval-command")).toContainText("--model python:my_model:Heuristic");
    await dlg.getByTestId("quick-eval-type-serve").click();
    await expect(dlg.getByTestId("quick-eval-vision")).toBeVisible();
    await dlg.getByTestId("quick-eval-type-baseline").click();
    await dlg.getByTestId("quick-eval-value").selectOption("majority");
    await dlg.getByTestId("quick-eval-name").fill(name);
    await dlg.getByTestId("quick-eval-suite").selectOption("smoke");
    await dlg.getByTestId("quick-eval-split").selectOption("all");
    await dlg.getByTestId("quick-eval-limit").fill("5");
    await dlg.getByTestId("quick-eval-workers").fill("2");
    await dlg.getByTestId("quick-eval-out").fill("");
    const cmd = dlg.getByTestId("quick-eval-command");
    await expect(cmd).toContainText(`decider-lab eval --model majority --suite smoke --name ${name} --workers 2 --out runs/${name}/smoke --limit 5 --split all`);
    await dlg.getByTestId("quick-eval-start").click();
    await expect(dlg).toBeHidden();
    await expect(page.getByTestId("toast").filter({ hasText: `Started: eval ${name} on smoke` })).toBeVisible();
    await expect(page).not.toHaveURL(/quick-eval/);
    await expect(page.getByTestId(`recent-row-${name}-smoke`)).toBeVisible({ timeout: 45_000 });
  });

  test("the e shortcut opens quick eval and Escape closes it", async ({ page, studio }) => {
    test.skip(studio.isMobile, "keyboard shortcut");
    await studio.open("/");
    await page.locator("body").press("e");
    await expect(page.getByTestId("quick-eval-dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("quick-eval-dialog")).toBeHidden();
    await page.getByTestId("quick-eval").click();
    await expect(page.getByTestId("quick-eval-dialog")).toBeVisible();
  });

  test("onboarding shows on an empty workspace, checks steps off and can be dismissed", async ({ page, studio }) => {
    let dismissed = false;
    await page.route("**/api/overview", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.kpis.labs = 0;
      body.kpis.run_roots = 0;
      body.labs = [];
      body.recent_results = [];
      body.recent_roots = [];
      body.onboarding = { doctor_seen: true, has_lab: false, has_run: false, has_results: false, dismissed };
      await route.fulfill({ response: res, json: body });
    });
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "PUT") {
        dismissed = (route.request().postDataJSON() as { onboarding_dismissed?: boolean }).onboarding_dismissed === true;
        await route.fulfill({ json: { onboarding_dismissed: dismissed } });
      } else await route.continue();
    });
    await studio.open("/");
    const ob = page.getByTestId("onboarding");
    await expect(ob).toBeVisible();
    await expect(page.getByTestId("kpis")).toHaveCount(0);
    await expect(page.getByTestId("onboarding-step-1")).toHaveAttribute("data-done", "true");
    await expect(page.getByTestId("onboarding-step-2")).toHaveAttribute("data-done", "false");
    await expect(page.getByTestId("onboarding-step-3")).toContainText("create a lab first");
    await expect(page.getByTestId("recent-results").getByTestId("empty-state")).toContainText("No scored runs yet");
    await page.getByTestId("onboarding-step-2").getByRole("link", { name: "New lab" }).click();
    await expect(page).toHaveURL(/\/labs\/new$/);
    await page.goto("/");
    await page.getByTestId("onboarding-dismiss").click();
    await expect(page.getByTestId("onboarding")).toBeHidden();
    await expect(page.getByTestId("kpis")).toBeVisible();
  });

  test("overview passes axe in both themes", async ({ page, studio }) => {
    await studio.open("/");
    await expect(page.getByTestId("kpi-labs")).toBeVisible();
    await studio.axe();
    await page.emulateMedia({ colorScheme: "light" });
    await page.reload();
    await expect(page.getByTestId("kpi-labs")).toBeVisible();
    await studio.axe();
  });
});

// ---- Models --------------------------------------------------------------------------------------

function archiveFixture(project: string): { dir: string; file: string; sha: string } {
  const dir = mkdtempSync(path.join(tmpdir(), "dl-e2e-model-"));
  const ck = path.join(dir, "ckpt");
  mkdirSync(ck);
  writeFileSync(path.join(ck, "strands_decider_config.json"), JSON.stringify({ project }));
  writeFileSync(path.join(ck, "model.bin"), randomBytes(8192));
  const file = path.join(dir, "model.tar.gz");
  execFileSync("tar", ["-czf", file, "-C", dir, "ckpt"]);
  const sha = createHash("sha256").update(readFileSync(file)).digest("hex");
  return { dir, file, sha };
}

function serveFile(file: string): Promise<{ url: string; server: Server }> {
  return new Promise((resolve) => {
    const server = createServer((req, res) => {
      if (req.url === "/model.tar.gz") {
        const data = readFileSync(file);
        res.writeHead(200, { "Content-Type": "application/gzip", "Content-Length": data.length });
        res.end(data);
      } else {
        res.writeHead(404);
        res.end();
      }
    });
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address() as { port: number };
      resolve({ url: `http://127.0.0.1:${addr.port}/model.tar.gz`, server });
    });
  });
}

test.describe("models", () => {
  test("the cache page lists the decider-lab cache and the HF cache", async ({ page, studio }) => {
    await studio.open("/models");
    await expect(page.getByTestId("page-models")).toBeVisible();
    await expect(page.getByTestId("models-summary")).toContainText("models");
    await expect(page.getByTestId("models-table")).toBeVisible();
    await expect(page.getByTestId("models-hf-table")).toBeVisible();
    await page.getByTestId("models-tab-pull").click();
    await expect(page).toHaveURL(/tab=pull/);
    await page.getByTestId("models-tab-cache").click();
    await expect(page).not.toHaveURL(/tab=pull/);
    await page.getByTestId("pull-open").click();
    await expect(page.getByTestId("pull-form")).toBeVisible();
  });

  test("inspect classifies sources and enforces the pinned-revision policy", async ({ page, studio }) => {
    await studio.open("/models?tab=pull");
    const src = page.getByTestId("pull-source");
    const inspect = page.getByTestId("pull-inspect");
    const start = page.getByTestId("pull-start");
    const pinned = page.getByTestId("pull-require-pinned");
    await expect(pinned).toBeChecked();
    await src.fill("hf://StrandsAgents/strands-decider-2B-hobson-v19@main");
    await expect(inspect).toContainText("Hugging Face");
    await expect(inspect).toContainText("not pinned");
    await expect(page.getByTestId("pull-problems")).toContainText("Pinned revisions are required: use a full 40-character commit.");
    await expect(start).toBeDisabled();
    await expect(page.getByTestId("pull-token")).toContainText("HF_TOKEN");
    await pinned.uncheck();
    await expect(page.getByTestId("pull-problems")).toContainText("Not pinned to a commit");
    await expect(page.getByTestId("pull-command")).not.toContainText("--require-pinned");
    await expect(start).toBeEnabled();
    await pinned.check();
    await src.fill(`hf://StrandsAgents/strands-decider-2B-hobson-v19@${SHA_A}`);
    await expect(inspect).toContainText("pinned to a full commit");
    await expect(page.getByTestId("pull-command")).toContainText(`@${SHA_A} --require-pinned`);
    await expect(page.getByTestId("pull-problems")).toHaveCount(0);
    // revision field (hf only) and sha256 disabled for hf
    await expect(page.getByTestId("pull-sha256")).toBeDisabled();
    await src.fill("hf://StrandsAgents/strands-decider-2B-hobson-v19");
    await page.getByTestId("pull-revision").fill(SHA_A);
    await expect(inspect).toContainText("pinned to a full commit");
    await page.getByTestId("pull-revision").fill("");
    // s3 shows profile and region and asks for a sha256
    await src.fill("s3://my-bucket/weights/model.tar");
    await expect(inspect).toContainText("Amazon S3");
    await expect(page.getByTestId("pull-profile")).toBeVisible();
    await page.getByTestId("pull-profile").fill("heisenberg");
    await page.getByTestId("pull-region").fill("us-east-1");
    await expect(page.getByTestId("pull-command")).toContainText("--profile heisenberg --region us-east-1");
    await expect(page.getByTestId("pull-problems")).toContainText("cannot be verified");
    // credentialed URLs are refused and never echoed
    await src.fill("https://user:pw-e2e-secret@example.com/m.tar");
    await expect(page.getByTestId("pull-problems")).toContainText("This URL carries a credential.");
    await expect(start).toBeDisabled();
    await expect(page.locator("body")).not.toContainText("pw-e2e-secret@");
    await src.fill("https://bucket.s3.amazonaws.com/m.tar?X-Amz-Signature=abcdef0123&X-Amz-Credential=x");
    await expect(page.getByTestId("pull-problems")).toContainText("carries a credential");
    // a missing local directory
    await src.fill("./no/such/dir");
    await expect(page.getByTestId("pull-problems")).toContainText("No such directory");
    await expect(start).toBeDisabled();
  });

  test("pull a local directory: the job succeeds", async ({ page, studio }) => {
    await studio.open("/models?tab=pull");
    await page.getByTestId("pull-source").fill("labs/first");
    await expect(page.getByTestId("pull-inspect")).toContainText("Local directory");
    await expect(page.getByTestId("pull-start")).toBeEnabled();
    await page.getByTestId("pull-start").click();
    await expect(page.getByTestId("toast").filter({ hasText: "Started: pull labs/first" })).toBeVisible();
    const card = page.getByTestId("pull-jobs").locator("[data-testid^=job-card-j_]").first();
    await expect(card.getByTestId("job-card-status")).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
    await expect(card.getByTestId("job-card-link")).toHaveAttribute("href", /\/jobs\/j_[0-9a-f]{12}$/);
  });

  test("pull an https archive with sha256, see it pinned in the cache, then delete it with the typed ref", async ({ page, studio, context }, info) => {
    const fx = archiveFixture(info.project.name);
    const { url, server } = await serveFile(fx.file);
    try {
      await studio.open("/models?tab=pull");
      await page.getByTestId("pull-source").fill(url);
      await expect(page.getByTestId("pull-problems")).toContainText("No sha256");
      await page.getByTestId("pull-sha256").fill(fx.sha);
      await expect(page.getByTestId("pull-inspect")).toContainText("sha256 given");
      await expect(page.getByTestId("pull-command")).toContainText(`--sha256 ${fx.sha}`);
      await page.getByTestId("pull-start").click();
      const card = page.getByTestId("pull-jobs").locator("[data-testid^=job-card-j_]").first();
      await expect(card.getByTestId("job-card-status")).toHaveAttribute("data-status", "succeeded", { timeout: 45_000 });
      await expect(card.getByTestId("job-stage-verify")).toBeVisible();
      await expect(page.getByTestId("toast").filter({ hasText: "Pulled · sha256 verified" })).toBeVisible();
      // inspecting again says it is cached
      await page.getByTestId("pull-source").fill(url + " ");
      await expect(page.getByTestId("pull-cached")).toContainText("Already in the cache");
      await page.getByTestId("pull-cached").getByRole("button", { name: "Show in cache" }).click();

      const ref8 = fx.sha.slice(0, 8);
      const row = page.getByTestId(`model-row-${ref8}`);
      await expect(row).toContainText("pinned");
      await expect(row).toContainText("url");
      await context.grantPermissions(["clipboard-read", "clipboard-write"]);
      await page.getByTestId(`model-snippet-${ref8}`).click();
      await expect(page.getByTestId("toast").filter({ hasText: "Lab snippet copied" })).toBeVisible();
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      expect(clip).toContain(`serve: ${url}`);
      expect(clip).toContain(`sha256: ${fx.sha}`);
      await page.getByTestId(`model-copy-${ref8}`).click();
      await expect(page.getByTestId("toast").filter({ hasText: "Source copied" })).toBeVisible();

      await page.getByTestId(`model-delete-${ref8}`).click();
      const dlg = page.getByTestId("confirm-dialog");
      await expect(dlg).toBeVisible();
      await expect(dlg.getByTestId("confirm-phrase")).toHaveText(ref8);
      await dlg.getByTestId("confirm-input").fill("nope");
      await expect(dlg.getByTestId("confirm-submit")).toBeDisabled();
      await dlg.getByTestId("confirm-input").fill(ref8);
      await dlg.getByTestId("confirm-submit").click();
      await expect(dlg).toBeHidden();
      await expect(page.getByTestId("toast").filter({ hasText: "Model deleted" })).toBeVisible();
      await expect(row).toHaveCount(0);
    } finally {
      server.close();
    }
  });

  test("a sha256 mismatch fails the pull with both hashes named", async ({ page, studio }, info) => {
    const fx = archiveFixture(info.project.name + "-bad");
    const { url, server } = await serveFile(fx.file);
    try {
      await studio.open("/models?tab=pull");
      await page.getByTestId("pull-source").fill(url);
      await page.getByTestId("pull-sha256").fill("0".repeat(64));
      await page.getByTestId("pull-start").click();
      const card = page.getByTestId("pull-jobs").locator("[data-testid^=job-card-j_]").first();
      await expect(card.getByTestId("job-card-status")).toHaveAttribute("data-status", "failed", { timeout: 45_000 });
      await expect(card.getByTestId("job-card-failures")).toContainText("does not match");
      await expect(card.getByTestId("job-card-failures")).toContainText(fx.sha);
    } finally {
      server.close();
    }
  });

  test("models page passes axe", async ({ page, studio }) => {
    await studio.open("/models");
    await expect(page.getByTestId("models-table")).toBeVisible();
    await studio.axe();
    await page.getByTestId("models-tab-pull").click();
    await page.getByTestId("pull-source").fill("hf://StrandsAgents/x@main");
    await expect(page.getByTestId("pull-problems")).toBeVisible();
    await studio.axe();
  });
});

// ---- Data ----------------------------------------------------------------------------------------

const CSV_OK = [
  "state,question,answer,options,kind,notes",
  "The sky is green.,Is this true?,no,,,n1",
  "Pick a fruit,Which is a fruit?,apple,apple|rock|car,,",
  "Review: great product,How positive?,good,bad|ok|good,score,",
].join("\n");

test.describe("data", () => {
  test("suites tab and the inspector: stats, label balance, families, paging, filters, row detail, export", async ({ page, studio }, info) => {
    await studio.open("/data");
    await expect(page.getByTestId("page-data")).toBeVisible();
    await expect(page.getByTestId("suite-card-smoke")).toContainText("90 rows");
    await expect(page.getByTestId("suite-card-synthetic")).toBeVisible();
    await expect(page.getByTestId("suite-card-heldout")).toBeVisible();
    await expect(page.getByTestId("suite-file-data-eval-jsonl")).toContainText("valid");
    await expect(page.getByTestId("suite-file-data-eval-jsonl")).toContainText("30 rows");

    await page.getByTestId("suite-inspect-smoke").click();
    await expect(page).toHaveURL(/inspect=smoke/);
    const dr = page.getByTestId("suite-inspector");
    await expect(dr).toBeVisible();
    await expect(dr.getByTestId("suite-stats-rows")).toContainText("90");
    await expect(dr.getByTestId("suite-stats-kind-noul")).toContainText("30");
    await expect(dr.getByTestId("label-balance-noul")).toBeVisible();
    await expect(dr.getByTestId("suite-families")).toContainText("gen/arithmetic");
    await expect(dr.getByTestId("rows-count")).toHaveText("1–25 of 90");
    await dr.getByTestId("rows-next").click();
    await expect(dr.getByTestId("rows-count")).toHaveText("26–50 of 90");
    await dr.getByTestId("rows-prev").click();
    await expect(dr.getByTestId("rows-count")).toHaveText("1–25 of 90");
    await dr.getByTestId("rows-filter-kind").selectOption("noul");
    await expect(dr.getByTestId("rows-count")).toHaveText("1–25 of 30");
    await dr.getByTestId("rows-filter-split").selectOption("dev");
    await expect(dr.getByTestId("rows-count")).toHaveText("1–12 of 12");
    await dr.getByTestId("rows-filter-split").selectOption("");
    await dr.getByTestId("rows-filter-task").selectOption("gen/calendar");
    await expect(dr.getByTestId("rows-count")).toHaveText("1–10 of 10");
    await dr.getByTestId("rows-filter-task").selectOption("");
    await dr.getByTestId("rows-filter-kind").selectOption("");
    await dr.getByTestId("rows-search").fill("zzzz-no-such-text");
    await expect(dr.getByTestId("rows-count")).toHaveText("No rows match.");
    await dr.getByTestId("rows-search").fill("");
    await expect(dr.getByTestId("rows-count")).toHaveText("1–25 of 90");
    await dr.getByTestId("row-0").getByRole("button").click();
    await expect(dr.getByTestId("row-detail-0")).toContainText("★");
    const out = `data/e2e-smoke-${info.project.name}.jsonl`;
    await dr.getByTestId("suite-export-out").fill(out);
    await dr.getByTestId("suite-export-save").click();
    await expect(page.getByTestId("toast").filter({ hasText: "Saved 90 rows" })).toBeVisible();
    await dr.getByTestId("suite-export-save").click();
    await expect(dr.getByRole("alert")).toContainText("exists");
    await dr.getByTestId("suite-export-overwrite").check();
    await dr.getByTestId("suite-export-save").click();
    await expect(page.getByTestId("toast").filter({ hasText: "Saved 90 rows" }).first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dr).toBeHidden();
    await expect(page).not.toHaveURL(/inspect=/);
    await expect(page.getByTestId(`suite-file-data-e2e-smoke-${info.project.name}-jsonl`)).toContainText("90 rows");
  });

  test("synthetic parameters build the ref and inspect it", async ({ page, studio }) => {
    await studio.open("/data");
    const card = page.getByTestId("suite-card-synthetic");
    await card.getByTestId("synthetic-per-kind").fill("5");
    await card.getByTestId("synthetic-family-seating").uncheck();
    await card.getByTestId("synthetic-seed").fill("3");
    await expect(card.getByTestId("synthetic-ref")).toHaveText("synthetic:per_kind=5,seed=3,families=arithmetic+calendar");
    await expect(card.getByTestId("synthetic-rows")).toHaveText("30 rows");
    await card.getByTestId("synthetic-family-arithmetic").uncheck();
    await card.getByTestId("synthetic-family-calendar").uncheck();
    await expect(card.getByTestId("suite-inspect-synthetic")).toBeDisabled();
    await card.getByTestId("synthetic-family-calendar").check();
    await card.getByTestId("synthetic-per-kind").fill("0");
    await expect(card.getByTestId("suite-inspect-synthetic")).toBeDisabled();
    await card.getByTestId("synthetic-per-kind").fill("5");
    await card.getByTestId("suite-inspect-synthetic").click();
    await expect(page.getByTestId("suite-inspector").getByTestId("suite-stats-rows")).toContainText("15");
  });

  test("a workspace file opens in the inspector and its Leakcheck button prefills the leakcheck tab", async ({ page, studio }) => {
    await studio.open("/data");
    await page.getByTestId("suite-inspect-data-eval-jsonl").click();
    await expect(page.getByTestId("suite-inspector").getByTestId("suite-stats-rows")).toContainText("30");
    await page.keyboard.press("Escape");
    await page.getByTestId("suite-leakcheck-data-eval-jsonl").click();
    await expect(page).toHaveURL(/tab=leakcheck/);
    await expect(page.getByTestId("leakcheck-train")).toHaveValue(/.+/);
    await page.getByTestId("leakcheck-against-smoke").check();
    const drop = "data/eval-clean-" + Date.now() + ".jsonl";
    await page.getByTestId("leakcheck-drop-to").fill(drop);
    await page.getByTestId("leakcheck-overwrite").check();
    await page.getByTestId("leakcheck-run").click();
    const res = page.getByTestId("leakcheck-result");
    await expect(res).toHaveAttribute("data-passed", "false");
    await expect(page.getByTestId("leakcheck-fail")).toContainText("30 of 30 training rows overlap");
    await expect(page.getByTestId("leakcheck-overlapping")).toContainText("30");
    await expect(page.getByTestId("leakcheck-clean")).toContainText(`0 rows → ${drop}`);
  });

  test("upload a CSV, preview it, convert it, inspect the result; errors block converting", async ({ page, studio }, info) => {
    await studio.open("/data");
    await page.getByTestId("data-tab-csv").click();
    await expect(page).toHaveURL(/tab=csv/);
    await page.getByTestId("csv-task").fill("e2e");
    await page.getByTestId("csv-delimiter").selectOption(",");
    await page.getByTestId("csv-file").setInputFiles({ name: "my rows.csv", mimeType: "text/csv", buffer: Buffer.from(CSV_OK) });
    const pv = page.getByTestId("csv-preview");
    await expect(pv).toBeVisible();
    await expect(page.getByTestId("csv-columns")).toContainText("state");
    await expect(page.getByTestId("csv-columns")).toContainText("notes");
    await expect(page.getByTestId("csv-out")).toHaveValue("data/my-rows.jsonl");
    await expect(pv).toContainText("3 rows");
    const out = `data/csv-${info.project.name}-${Date.now()}.jsonl`;
    await page.getByTestId("csv-out").fill(out);
    await page.getByTestId("csv-overwrite").check();
    await page.getByTestId("csv-convert").click();
    await expect(page.getByTestId("csv-done")).toContainText(`3 rows → ${out}`);
    await page.getByTestId("csv-done-inspect").click();
    await expect(page.getByTestId("suite-inspector").getByTestId("suite-stats-rows")).toContainText("3");
    await page.keyboard.press("Escape");

    const bad = CSV_OK + "\ns,q,maybe,,,\n,q,yes,,,\n";
    await page.getByTestId("csv-file").setInputFiles({ name: "bad.csv", mimeType: "text/csv", buffer: Buffer.from(bad) });
    await expect(page.getByTestId("csv-errors")).toContainText("2 rows are not valid");
    await expect(page.getByTestId("csv-errors")).toContainText("line 5");
    await expect(page.getByTestId("csv-convert")).toBeDisabled();

    await page.getByTestId("csv-delimiter").selectOption(";");
    await page.getByTestId("csv-file").setInputFiles({ name: "semi.csv", mimeType: "text/csv", buffer: Buffer.from(CSV_OK) });
    await expect(page.getByTestId("csv-preview")).toContainText("Required columns are missing");
    await expect(page.getByTestId("csv-convert")).toBeDisabled();
    await page.getByTestId("csv-choose").click(); // opens the file chooser (keyboard path); nothing else changes
  });

  test("generate rows with exclusions, then leakcheck them", async ({ page, studio }, info) => {
    await studio.open("/data?tab=generate");
    const form = page.getByTestId("generate-form");
    await expect(form.getByTestId("generate-exclude-smoke")).toBeChecked();
    await expect(form.getByTestId("generate-exclude-synthetic-per-kind-40")).toBeChecked();
    await form.getByTestId("generate-family-calendar").uncheck();
    await form.getByTestId("generate-family-seating").uncheck();
    await form.getByTestId("generate-per-kind").fill("0");
    await expect(page.getByTestId("generate-run")).toBeDisabled();
    await form.getByTestId("generate-per-kind").fill("12");
    await form.getByTestId("generate-seed").fill("7");
    await expect(form.getByTestId("generate-total")).toContainText("36 rows before exclusions");
    const out = `data/train-${info.project.name}-${Date.now()}.jsonl`;
    await form.getByTestId("generate-out").fill(out);
    await page.getByTestId("generate-run").click();
    const res = page.getByTestId("generate-result");
    await expect(res).toContainText(`→ ${out}`);
    await expect(res).toContainText("dropped as overlapping");
    await expect(res).toContainText("--exclude-suite smoke");
    await page.getByTestId("generate-run").click();
    await expect(page.getByTestId("generate-error")).toContainText("exists");
    await page.getByTestId("generate-overwrite").check();
    await page.getByTestId("generate-run").click();
    await expect(page.getByTestId("generate-result")).toBeVisible();
    await page.getByTestId("generate-inspect").click();
    await expect(page.getByTestId("suite-inspector").getByTestId("suite-stats-rows")).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByTestId("generate-leakcheck").click();
    await expect(page).toHaveURL(/tab=leakcheck/);
    await expect(page.getByTestId("leakcheck-against-smoke")).toBeChecked();
    await page.getByTestId("leakcheck-run").click();
    const lr = page.getByTestId("leakcheck-result");
    await expect(lr).toBeVisible();
    const passed = await lr.getAttribute("data-passed");
    if (passed === "true") await expect(page.getByTestId("leakcheck-pass")).toContainText("No training row shares an input with these suites");
    else await expect(page.getByTestId("leakcheck-fail")).toBeVisible();
  });

  test("leakcheck passes for rows that share nothing with the suites", async ({ page, studio }, info) => {
    const out = `data/uniq-${info.project.name}-${Date.now()}.jsonl`;
    await studio.open("/data?tab=csv");
    const unique = `state,question,answer\nA unique state ${Date.now()},Is it unique?,yes\n`;
    await page.getByTestId("csv-file").setInputFiles({ name: "u.csv", mimeType: "text/csv", buffer: Buffer.from(unique) });
    await page.getByTestId("csv-out").fill(out);
    await page.getByTestId("csv-convert").click();
    await expect(page.getByTestId("csv-done")).toBeVisible();
    await page.getByTestId("data-tab-leakcheck").click();
    await page.getByTestId("leakcheck-train").selectOption({ label: `${out} (1 rows)` });
    await page.getByTestId("leakcheck-against-smoke").check();
    await page.getByTestId("leakcheck-against-data-eval-jsonl").check();
    await page.getByTestId("leakcheck-run").click();
    await expect(page.getByTestId("leakcheck-result")).toHaveAttribute("data-passed", "true");
    await expect(page.getByTestId("leakcheck-pass")).toContainText("No training row shares an input with these suites");
  });

  test("split a file into dev and test with projected dev rows", async ({ page, studio }, info) => {
    await studio.open("/data?tab=split");
    const form = page.getByTestId("split-form");
    await expect(page.getByTestId("split-run")).toBeDisabled();
    await form.getByTestId("split-file").selectOption({ label: "data/eval.jsonl (30 rows)" });
    await expect(form.getByTestId("split-out")).toHaveValue("data/eval-split.jsonl");
    await expect(form.getByTestId("split-projected")).toContainText("yes/no 12");
    await expect(form.getByTestId("split-projected")).toContainText("at least 30 dev rows");
    await form.getByTestId("split-fraction").fill("1.5");
    await expect(page.getByTestId("split-run")).toBeDisabled();
    await form.getByTestId("split-fraction").fill("0.5");
    await form.getByTestId("split-seed").fill("2");
    const out = `data/eval-split-${info.project.name}-${Date.now()}.jsonl`;
    await form.getByTestId("split-out").fill(out);
    await form.getByTestId("split-overwrite").check();
    await page.getByTestId("split-run").click();
    const res = page.getByTestId("split-result");
    await expect(res).toContainText(`30 rows → ${out}`);
    await expect(res).toContainText("dev 15");
    await expect(res).toContainText(`- ${out}`);
    await page.getByTestId("split-inspect").click();
    await expect(page.getByTestId("suite-inspector").getByTestId("suite-stats-rows")).toContainText("30");
  });

  test("data tabs pass axe", async ({ page, studio }) => {
    for (const tab of ["suites", "csv", "generate", "split", "leakcheck"]) {
      await studio.open(`/data?tab=${tab}`);
      await expect(page.getByTestId("page-data")).toBeVisible();
      await expect(page.getByRole("tabpanel")).toBeVisible();
      await studio.axe();
    }
    await studio.open("/data?inspect=smoke");
    await expect(page.getByTestId("suite-stats")).toBeVisible();
    await studio.axe();
  });

  test("no horizontal page scroll on the three pages", async ({ page, studio }) => {
    for (const p of ["/", "/models", "/models?tab=pull", "/data", "/data?tab=generate", "/data?tab=leakcheck"]) {
      await studio.open(p);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const w = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(w, p).toBeLessThanOrEqual(page.viewportSize()!.width);
    }
  });
});
