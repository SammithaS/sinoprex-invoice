import { expect, test, type Page } from "@playwright/test";

/** No element pushes the page wider than the viewport. */
async function noHorizontalScroll(page: Page) {
  const { scroll, width } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: window.innerWidth,
  }));
  expect(scroll, "page scrolls sideways").toBeLessThanOrEqual(width);
}

test.describe.configure({ mode: "serial" });

test("price over contract: review → override → four-eyes approval → ERP → evidence", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run sample: Unit price above the contracted rate" }).click();

  // The live run view pauses with the rules' question.
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "Waiting for your review" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/1,100.*1,250|₹1,250/).first()).toBeVisible();
  await expect(page.getByText("contracts/technova §2.1").first()).toBeVisible();
  const runUrl = page.url();

  // Reviewer approves despite the price variance → the override needs the AP Manager (§7.3).
  await page.getByLabel("Reviewer").fill("Priya Nair");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Waiting for approval" })).toBeVisible({ timeout: 30_000 });

  // Four-eyes: the reviewer can't also be the approver.
  await page.goto("/approvals");
  const approver = page.getByLabel("Approver").first();
  await approver.fill("Priya Nair");
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await expect(page.getByRole("alert").filter({ hasText: /different person must approve/i })).toBeVisible();

  await approver.fill("Arjun Desai");
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "No approvals waiting" })).toBeVisible({ timeout: 30_000 });

  // Back on the case: approved, posted to the mock ERP and verified by read-back.
  await page.goto(runUrl);
  await expect(page.getByText("Approved").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/AP-2026-\d{6}/).first()).toBeVisible();

  await page.goto(`${runUrl.replace("/runs/", "/cases/")}/evidence`);
  await expect(page.getByText("Arjun Desai").first()).toBeVisible();
});

test("dashboard counts the case", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByText("Straight-through").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
  await expect(page.getByText("TN/2026/1042").first()).toBeVisible();
});

test("payments: the posted invoice is in the payables queue", async ({ page }) => {
  await page.goto("/payments");
  await expect(page.getByRole("link", { name: "Set up RazorpayX" })).toBeVisible();
  await expect(page.getByRole("link", { name: "TN/2026/1042" })).toBeVisible();
  await expect(page.getByText("Not due yet").last()).toBeVisible();
});

test("knowledge search returns cited sections", async ({ page }) => {
  await page.goto("/knowledge");
  await expect(page.getByText("contracts/technova").first()).toBeVisible();
  await page.getByLabel("Search the knowledge base").fill("bank account changed on invoice");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText("ap-policy §6.2")).toBeVisible();
});

test("apps: write-only secret, install, error state, activate", async ({ page }) => {
  const secret = "https://hooks.slack.com/services/T0E2E/B0E2E/e2esecretvalueQRST";
  await page.goto("/apps/connections?new=slack");
  await page.getByLabel("Name", { exact: true }).fill("E2E Slack");
  await page.getByLabel("Channel name").fill("#ap-e2e");
  await page.getByLabel("Webhook URL").fill(secret);
  await page.getByRole("button", { name: /Save\s+and test/ }).click();
  await expect(page.getByText("E2E Slack")).toBeVisible();
  await expect(page.getByText("Working")).toBeVisible();
  await expect(page.getByText("••••••••QRST")).toBeVisible();
  expect(await page.content()).not.toContain("e2esecretvalue");

  await page.goto("/apps");
  await page.getByRole("link", { name: /Slack Alerts/ }).click();
  await page.getByRole("button", { name: "Install" }).click();
  await page.getByRole("button", { name: "Activate" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Choose a connection for 'Slack channel'" })).toBeVisible();
  await page.getByRole("combobox").selectOption({ label: "E2E Slack · tested" });
  await page.getByRole("button", { name: "Save and activate" }).click();
  await expect(page.getByText("Active", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
});

test("unknown pages get the app's 404", async ({ page }) => {
  const res = await page.goto("/no-such-page");
  expect(res?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});

test.describe("phone layout", () => {
  test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
  for (const path of ["/", "/dashboard", "/review", "/approvals", "/erp", "/payments", "/procedure", "/knowledge", "/settings", "/apps", "/apps/connections"]) {
    test(`no sideways scroll on ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await noHorizontalScroll(page);
    });
  }
});
