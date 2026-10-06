// Compute page (DESIGN.md 4.7, 12): doctor, vast.ai, AWS, SSH hosts, against the real Studio server with
// DECIDER_LAB_FAKE_CLOUD=1. Destroy/terminate tests put the fixtures back with POST /api/compute/fake/reset
// so later specs (safety, overview) still see the idle fixture machines.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const VAST_ID = "9876543";
const AWS_ID = "i-0abc123def4567890";

async function noHorizontalScroll(page: Page) {
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(page.viewportSize()!.width);
}

test.describe("compute", () => {
  test.afterEach(async ({ studio }) => {
    await studio.api("/api/compute/fake/reset", { method: "POST", body: {} });
  });

  test("local tab lists doctor checks and this machine", async ({ page, studio }) => {
    await studio.open("/compute");
    await expect(page.getByRole("heading", { level: 1, name: "Compute" })).toBeVisible();
    await expect(page.getByTestId("compute-tab-local")).toHaveAttribute("aria-selected", "true");
    const list = page.getByTestId("doctor-list");
    await expect(list).toBeVisible();
    await expect(page.getByTestId("doctor-row-decider-lab")).toHaveAttribute("data-status", "ok");
    await expect(page.getByTestId("doctor-row-vast.ai")).toContainText("(fixtures)");
    await expect(page.getByTestId("doctor-row-aws")).toContainText("(fixtures)");
    await expect(page.getByTestId("doctor-row-torch")).toBeVisible();
    await expect(page.getByTestId("doctor-row-strands-decider")).toBeVisible();
    await expect(page.getByTestId("doctor-counts")).toContainText("ok");
    await expect(page.getByTestId("machine-card")).toContainText("CPUs");
    await expect(page.getByTestId("machine-accelerator")).not.toBeEmpty();

    const recheck = page.waitForResponse((r) => r.url().includes("/api/compute/doctor") && r.ok());
    await page.getByTestId("doctor-recheck").click();
    await recheck;
    await expect(list).toBeVisible();
    await noHorizontalScroll(page);
    await studio.axe();
  });

  test("navigation and tabs keep the tab in the URL", async ({ page, studio }) => {
    await studio.open("/");
    await studio.nav("compute");
    await expect(page.getByTestId("page-compute")).toBeVisible();
    for (const tab of ["vast", "aws", "ssh", "local"] as const) {
      await page.getByTestId(`compute-tab-${tab}`).click();
      await expect(page.getByTestId(`compute-tab-${tab}`)).toHaveAttribute("aria-selected", "true");
      if (tab === "local") await expect(page).not.toHaveURL(/tab=/);
      else await expect(page).toHaveURL(new RegExp(`tab=${tab}`));
    }
    await page.goto("/compute?tab=aws");
    await expect(page.getByTestId("compute-tab-aws")).toHaveAttribute("aria-selected", "true");
  });

  test("vast.ai shows credit, burn rate and the idle fixture instance", async ({ page, studio }) => {
    await studio.open("/compute?tab=vast");
    await expect(page.getByTestId("vast-credit")).toContainText("$25.40");
    await expect(page.getByTestId("vast-burn")).toContainText("$0.612/h");
    await expect(page.getByTestId("vast-running")).toContainText("1");
    await expect(page.getByTestId("fixtures-badge").first()).toBeVisible();
    const row = page.getByTestId(`vast-instance-${VAST_ID}`);
    await expect(row).toContainText("1x A100_SXM4");
    await expect(row).toContainText("so far (estimate)");
    await expect(page.getByTestId(`vast-idle-${VAST_ID}`)).toHaveText(/idle · billing/);
    await noHorizontalScroll(page);
    await studio.axe();
  });

  test("vast.ai offers search, sort and copy a lab spec", async ({ page, studio }) => {
    await studio.open("/compute?tab=vast");
    const table = page.getByTestId("vast-offers");
    await expect(table.getByTestId("vast-offer-1234501")).toBeVisible();
    await expect(table.getByTestId("vast-offer-1234502")).toBeVisible();
    await expect(table.getByTestId("vast-offer-1234567")).toHaveCount(0);

    await page.getByTestId("vast-offers-gpu").selectOption("A100_SXM4");
    await page.getByTestId("vast-offers-max").fill("1");
    await page.getByTestId("vast-offers-disk").fill("100");
    await page.getByTestId("vast-offers-search").click();
    await expect(page).toHaveURL(/gpu=A100_SXM4/);
    await expect(page).toHaveURL(/max=1/);
    await expect(table.getByTestId("vast-offer-1234567")).toBeVisible();
    await expect(table.getByTestId("vast-offer-1234568")).toBeVisible();
    await expect(table.getByTestId("vast-offer-1234501")).toHaveCount(0);

    if (!studio.isMobile) {
      // cheapest first (ascending), then the header cycles: unsorted, descending
      const ids = async () => (await table.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-testid"))));
      const rate = table.getByRole("columnheader", { name: "$/h" });
      await expect(rate).toHaveAttribute("aria-sort", "ascending");
      expect(await ids()).toEqual(["vast-offer-1234567", "vast-offer-1234568"]);
      await table.getByRole("button", { name: "$/h" }).click();
      await expect(rate).toHaveAttribute("aria-sort", "none");
      await table.getByRole("button", { name: "$/h" }).click();
      await expect(rate).toHaveAttribute("aria-sort", "descending");
      expect(await ids()).toEqual(["vast-offer-1234568", "vast-offer-1234567"]);
      await table.getByRole("button", { name: "Reliability" }).click();
      await expect(table.getByRole("columnheader", { name: "Reliability" })).toHaveAttribute("aria-sort", "descending");
      expect(await ids()).toEqual(["vast-offer-1234568", "vast-offer-1234567"]);
    }

    await page.getByTestId("vast-offer-copy-1234567").getByRole("button").click();
    await expect(page.getByTestId("vast-offer-copy-1234567").getByRole("button", { name: "Copied" })).toBeVisible();

    // no match → empty state with the CLI equivalent
    await page.getByTestId("vast-offers-gpu").selectOption("H100_SXM");
    await page.getByTestId("vast-offers-max").fill("0.5");
    await page.getByTestId("vast-offers-search").click();
    await expect(table.getByTestId("empty-state")).toContainText("No offers match");
    await expect(table.getByTestId("empty-state")).toContainText("decider-lab compute offers --on vast --gpu H100_SXM");

    // client-side validation
    await page.getByTestId("vast-offers-num").fill("0");
    await page.getByTestId("vast-offers-search").click();
    await expect(page.getByTestId("vast-offers-error")).toContainText("GPUs must be");
  });

  test("vast.ai destroy needs the typed instance id", async ({ page, studio }) => {
    await studio.open("/compute?tab=vast");
    await page.getByTestId(`vast-destroy-${VAST_ID}`).click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toContainText(`Destroy instance ${VAST_ID}`);
    await expect(dialog.getByTestId("confirm-phrase")).toHaveText(VAST_ID);
    const submit = dialog.getByTestId("confirm-submit");
    await expect(submit).toBeDisabled();
    await dialog.getByTestId("confirm-input").fill("987654");
    await expect(submit).toBeDisabled();
    await dialog.getByRole("button", { name: "Keep it" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId(`vast-instance-${VAST_ID}`)).toBeVisible();

    await page.getByTestId(`vast-destroy-${VAST_ID}`).click();
    await dialog.getByTestId("confirm-input").fill(VAST_ID);
    await expect(dialog).toContainText("Confirmation matches");
    await submit.click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("toast")).toContainText(`Destroyed instance ${VAST_ID}`);
    await expect(page.getByTestId(`vast-instance-${VAST_ID}`)).toHaveCount(0);
    await expect(page.getByTestId("vast-instances").getByTestId("empty-state")).toContainText("No decider-lab instances");
    const after = await studio.api<{ items: unknown[] }>("/api/compute/vast/instances");
    expect(after.items).toEqual([]);
  });

  test("vast.ai destroy all needs `destroy all`", async ({ page, studio }) => {
    await studio.open("/compute?tab=vast");
    await page.getByTestId("vast-destroy-all").click();
    const dialog = page.getByTestId("confirm-dialog");
    await dialog.getByTestId("confirm-input").fill("destroy");
    await expect(dialog.getByTestId("confirm-submit")).toBeDisabled();
    await dialog.getByTestId("confirm-input").fill("destroy all");
    await dialog.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("toast")).toContainText("Destroyed 1 instance");
    await expect(page.getByTestId(`vast-instance-${VAST_ID}`)).toHaveCount(0);
  });

  test("vast.ai without the CLI shows how to set it up", async ({ page, studio }) => {
    await page.route("**/api/compute/vast/status", (route) =>
      route.fulfill({ json: { fake: false, cli: false, api_key: false, credit_usd: null, as_of: null, error: "The vastai CLI is not installed: pip install 'decider-lab[vast]'" } }),
    );
    await studio.open("/compute?tab=vast");
    const empty = page.getByTestId("vast-not-configured");
    await expect(empty).toContainText("The vastai CLI is not installed or has no API key.");
    await expect(empty).toContainText("pip install 'decider-lab[vast]'");
    await expect(empty).toContainText("vastai set api-key <key>");
    await expect(page.getByTestId("vast-offers")).toHaveCount(0);
  });

  test("AWS profile, region, identity and quotas", async ({ page, studio }) => {
    await studio.open("/compute?tab=aws");
    const profile = page.getByTestId("aws-profile");
    await expect(profile.locator("option", { hasText: "heisenberg" })).toHaveCount(1);
    await profile.selectOption("heisenberg");
    await expect(page).toHaveURL(/profile=heisenberg/);
    await page.getByTestId("aws-region").selectOption("us-west-2");
    await expect(page).toHaveURL(/region=us-west-2/);

    const identity = page.getByTestId("aws-identity");
    await expect(identity).toContainText("credentials valid");
    await expect(page.getByTestId("aws-account-value")).toHaveText("1234••••9012");
    await expect(page.getByTestId("aws-arn")).not.toContainText("123456789012");
    await page.getByTestId("aws-account-reveal").click();
    await expect(page.getByTestId("aws-account-value")).toHaveText("123456789012");
    await page.getByTestId("aws-account-reveal").click();
    await expect(page.getByTestId("aws-account-value")).toHaveText("1234••••9012");

    await expect(page.getByTestId("aws-quota-g")).toContainText("8 vCPU");
    await expect(page.getByTestId("aws-quota-g")).toContainText("4 used");
    await expect(page.getByTestId("aws-quota-g")).toContainText("L-DB2E81BA");
    await expect(page.getByTestId("aws-quota-standard")).toContainText("64 vCPU");
    const hint = page.getByTestId("aws-quota-hint-p");
    await expect(hint).toContainText("Request an increase of L-417A185B");
    await expect(hint).toContainText("--quota-code L-417A185B");
    await expect(hint).toContainText("--region us-west-2 --profile heisenberg");
    await expect(page.getByTestId("aws-instance-types")).toContainText("g6e.xlarge");

    // the choice survives a reload (URL is state)
    await page.reload();
    await expect(page.getByTestId("aws-region")).toHaveValue("us-west-2");
    await expect(page.getByTestId("aws-profile")).toHaveValue("heisenberg");
    await noHorizontalScroll(page);
    await studio.axe();
  });

  test("AWS tagged instances and typed terminate", async ({ page, studio }) => {
    await studio.open("/compute?tab=aws&profile=heisenberg&region=us-east-1");
    const row = page.getByTestId(`aws-instance-${AWS_ID}`);
    await expect(row).toContainText("g6e.xlarge");
    await expect(row).toContainText("$1.861/h");
    await expect(row).toContainText("3.91.x.x");
    await expect(page.getByTestId(`aws-idle-${AWS_ID}`)).toHaveText(/idle · billing/);
    await expect(page.getByTestId("aws-burn")).toHaveText("$1.861/h");

    await page.getByTestId(`aws-terminate-${AWS_ID}`).click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog.getByTestId("confirm-phrase")).toHaveText(AWS_ID);
    await dialog.getByTestId("confirm-input").fill("i-0abc");
    await expect(dialog.getByTestId("confirm-submit")).toBeDisabled();
    await dialog.getByTestId("confirm-input").fill(AWS_ID);
    await dialog.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("toast")).toContainText(`Terminating ${AWS_ID}`);
    await expect(row).toHaveCount(0);
    await expect(page.getByTestId("aws-instances").getByTestId("empty-state")).toContainText("No decider-lab instances in this region");
  });

  test("AWS terminate all needs `terminate all`", async ({ page, studio }) => {
    await studio.open("/compute?tab=aws");
    await page.getByTestId("aws-terminate-all").click();
    const dialog = page.getByTestId("confirm-dialog");
    await dialog.getByTestId("confirm-input").fill("terminate");
    await expect(dialog.getByTestId("confirm-submit")).toBeDisabled();
    await dialog.getByTestId("confirm-input").fill("terminate all");
    await dialog.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("toast")).toContainText("Terminating 1 instance");
    await expect(page.getByTestId(`aws-instance-${AWS_ID}`)).toHaveCount(0);
  });

  test("Bedrock models: search, copy spec, add to a lab", async ({ page, studio }) => {
    await studio.open("/compute?tab=aws&region=us-east-1");
    const panel = page.getByTestId("bedrock-models");
    await expect(panel.getByTestId("bedrock-model-amazon.nova-pro-v1:0")).toContainText("us.amazon.nova-pro-v1:0");
    await expect(panel.getByTestId("bedrock-model-meta.llama3-3-70b-instruct-v1:0")).toBeVisible();

    await page.getByTestId("bedrock-search").fill("nova");
    await expect(panel.getByTestId("bedrock-model-meta.llama3-3-70b-instruct-v1:0")).toHaveCount(0);
    await expect(panel.getByTestId("bedrock-model-amazon.nova-lite-v1:0")).toBeVisible();

    await page.getByTestId("bedrock-copy-amazon.nova-pro-v1:0").click();
    await expect(page.getByTestId("toast")).toContainText("{bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}");

    await page.getByTestId("bedrock-add-amazon.nova-pro-v1:0").click();
    const dialog = page.getByTestId("bedrock-add-dialog");
    await expect(dialog.getByTestId("bedrock-snippet")).toContainText("models:");
    await expect(dialog.getByTestId("bedrock-snippet")).toContainText("nova-pro: {bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}");
    await expect(dialog).toContainText("Each row is a billed request to this provider.");
    await dialog.getByRole("button", { name: "Close" }).first().click();
    await expect(dialog).toHaveCount(0);

    await page.getByTestId("bedrock-search").fill("no-such-model-xyz");
    await expect(panel.getByTestId("empty-state")).toContainText("No Bedrock models match");
  });

  test("AWS failures render as panel states, not crashes", async ({ page, studio }) => {
    await page.route("**/api/compute/aws/identity**", (route) =>
      route.fulfill({
        json: { fake: false, profile: "heisenberg", region: "us-east-1", account: null, arn: null, ok: false,
                error: "ExpiredToken: the credentials or SSO session for heisenberg expired or are not valid; run aws sso login --profile heisenberg",
                fix: "aws sso login --profile heisenberg" },
      }),
    );
    await page.route("**/api/compute/aws/instances**", (route) =>
      route.fulfill({ status: 424, json: { error: { code: "backend_unavailable", message: "boto3 is not installed, so Studio cannot read AWS.",
                                                       hint: "Install it with pip install 'decider-lab[aws]' and restart Studio.",
                                                       detail: { install: "pip install 'decider-lab[aws]'" }, request_id: "x" } } }),
    );
    await page.route("**/api/compute/aws/bedrock-models**", (route) =>
      route.fulfill({ json: { fake: false, items: [], error: "AccessDeniedException: this identity is not allowed bedrock:ListFoundationModels." } }),
    );
    await page.route("**/api/compute/aws/quotas**", (route) =>
      route.fulfill({ status: 502, json: { error: { code: "cloud_error", message: "AccessDenied: this identity is not allowed servicequotas:GetServiceQuota.", request_id: "y" } } }),
    );
    await studio.open("/compute?tab=aws&profile=heisenberg");
    await expect(page.getByTestId("aws-identity-error")).toContainText("ExpiredToken");
    await expect(page.getByTestId("aws-identity-error")).toContainText("aws sso login --profile heisenberg");
    await expect(page.getByTestId("aws-instances").getByTestId("empty-state")).toContainText("pip install 'decider-lab[aws]'");
    await expect(page.getByTestId("bedrock-error")).toContainText("bedrock:ListFoundationModels");
    const quotaErr = page.getByTestId("aws-quotas").getByTestId("error-state");
    await expect(quotaErr).toContainText("servicequotas:GetServiceQuota");
    await expect(quotaErr.getByRole("button", { name: "Retry" })).toBeVisible();
    await expect(page.getByTestId("page-compute")).toBeVisible();
  });

  test("SSH hosts: add, validate, test ok and failing, edit, delete", async ({ page, studio }, info) => {
    const sfx = info.project.name;
    const good = `gpu-box-${sfx}`;
    const bad = `old-rig-${sfx}`;
    await studio.open("/compute?tab=ssh");

    // add with an invalid target first
    await page.getByTestId("ssh-add").click();
    const dialog = page.getByTestId("ssh-dialog");
    await dialog.getByTestId("ssh-field-name").fill(good);
    await dialog.getByTestId("ssh-field-target").fill("ubuntu@10.0.0.5;reboot");
    await dialog.getByTestId("ssh-save").click();
    await expect(dialog).toContainText("Use user@host or user@host:port.");
    await expect(dialog.getByTestId("ssh-field-target")).toHaveAttribute("aria-invalid", "true");
    await dialog.getByTestId("ssh-field-target").fill("ubuntu@10.0.0.5:22");
    await dialog.getByTestId("ssh-field-key").fill("~/.ssh/id_ed25519");
    await dialog.getByTestId("ssh-save").click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("toast")).toContainText(`Added ${good}`);
    const row = page.getByTestId(`ssh-host-${good}`);
    await expect(row).toContainText("ubuntu@10.0.0.5:22");
    await expect(row).toContainText("not tested");

    await page.getByTestId(`ssh-test-${good}`).click();
    await expect(page.getByTestId(`ssh-result-${good}`)).toHaveAttribute("data-ok", "true");
    await expect(page.getByTestId(`ssh-result-${good}`)).toContainText("42 ms");
    await expect(page.getByTestId(`ssh-result-${good}`)).toContainText("NVIDIA L40S");

    // a duplicate name is refused by the server
    await page.getByTestId("ssh-add").click();
    await dialog.getByTestId("ssh-field-name").fill(good);
    await dialog.getByTestId("ssh-field-target").fill("root@other.local");
    await dialog.getByTestId("ssh-save").click();
    await expect(dialog.getByTestId("ssh-error")).toContainText("already exists");
    await dialog.getByTestId("ssh-field-name").fill(bad);
    await dialog.getByTestId("ssh-field-target").fill("root@rig.invalid");
    await dialog.getByTestId("ssh-field-key").fill("");
    await dialog.getByTestId("ssh-save").click();
    await expect(dialog).toHaveCount(0);

    await page.getByTestId(`ssh-test-${bad}`).click();
    await expect(page.getByTestId(`ssh-result-${bad}`)).toHaveAttribute("data-ok", "false");
    await expect(page.getByTestId(`ssh-result-${bad}`)).toContainText("Operation timed out");

    // results persist across a reload
    await page.reload();
    await expect(page.getByTestId(`ssh-result-${good}`)).toContainText("42 ms");

    // edit: changing the target clears the last test
    await page.getByTestId(`ssh-edit-${good}`).click();
    await expect(dialog.getByTestId("ssh-field-name")).toHaveValue(good);
    await dialog.getByTestId("ssh-field-target").fill("ubuntu@10.0.0.6:2222");
    await dialog.getByTestId("ssh-field-workdir").fill("/data/work");
    await dialog.getByTestId("ssh-save").click();
    await expect(page.getByTestId("toast").last()).toContainText(`Saved ${good}`);
    await expect(row).toContainText("ubuntu@10.0.0.6:2222");
    await expect(row).toContainText("not tested");

    for (const name of [good, bad]) {
      await page.getByTestId(`ssh-delete-${name}`).click();
      const confirm = page.getByTestId("confirm-dialog");
      await expect(confirm).toContainText("removes the bookmark");
      await confirm.getByTestId("confirm-submit").click();
      await expect(page.getByTestId(`ssh-host-${name}`)).toHaveCount(0);
    }
    await noHorizontalScroll(page);
  });

  test("the palette's add-host command opens the dialog", async ({ page, studio }) => {
    await studio.open("/compute?tab=ssh&add=1");
    await expect(page.getByTestId("ssh-dialog")).toBeVisible();
    await expect(page).not.toHaveURL(/add=1/);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("ssh-dialog")).toHaveCount(0);
    await studio.axe();
  });

  test("every tab passes axe in the light theme", async ({ page, studio }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await studio.open("/compute?tab=vast");
    await expect(page.getByTestId(`vast-instance-${VAST_ID}`)).toBeVisible();
    await expect(page.getByTestId("vast-offers").getByTestId("vast-offer-1234501")).toBeVisible();
    await studio.axe();
    await page.getByTestId("compute-tab-aws").click();
    await expect(page.getByTestId("aws-quota-p")).toBeVisible();
    await expect(page.getByTestId("bedrock-model-amazon.nova-pro-v1:0")).toBeVisible();
    await studio.axe();
    await page.getByTestId("compute-tab-local").click();
    await expect(page.getByTestId("doctor-list")).toBeVisible();
    await studio.axe();
  });

  test("vast and SSH tabs pass axe", async ({ page, studio }) => {
    await studio.open("/compute?tab=ssh");
    await expect(page.getByTestId("ssh-add")).toBeVisible();
    await studio.axe();
    await page.getByTestId("compute-tab-aws").click();
    await expect(page.getByTestId("bedrock-models")).toBeVisible();
    await expect(page.getByTestId(`aws-instance-${AWS_ID}`)).toBeVisible();
    await studio.axe();
  });
});
