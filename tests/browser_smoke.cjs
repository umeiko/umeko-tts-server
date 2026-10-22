/* Run against tests/ui_server.py, never a production service. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const token = "local-browser-test-" + "x".repeat(32);
const prefix = "/services/umeko/tts";
const out = path.resolve("tmp/admin-browser");
const run = Date.now().toString(36);
fs.mkdirSync(out, { recursive: true });

(async () => {
  let ready = false;
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      ready = (await fetch("http://127.0.0.1:10991" + prefix + "/healthz")).ok;
      if (ready) break;
    } catch {
      /* Local fixture is still starting. */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  assert.ok(ready, "tests/ui_server.py did not become ready");
  const browser = await chromium.launch({ headless: true });
  const report = [];
  try {
    for (const port of [10991, 10992]) {
      const base = `http://127.0.0.1:${port}${prefix}`;
      const context = await browser.newContext({
        viewport: { width: 1440, height: 1050 },
      });
      const page = await context.newPage();
      const errors = [],
        requests = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => {
        if (new URL(r.url()).host === `127.0.0.1:${port}`)
          requests.push(new URL(r.url()).pathname);
      });
      await page.goto(base + "/console/");
      await page.getByRole("heading", { name: "登录管理控制台" }).waitFor();
      if (port === 10991)
        await page.screenshot({
          path: path.join(out, "login.png"),
          fullPage: true,
        });
      await page.getByLabel("管理员令牌", { exact: true }).fill(token);
      await page.getByRole("button", { name: "进入控制台 →" }).click();
      await page
        .locator("#metricCapacity")
        .filter({ hasText: "个并行名额" })
        .waitFor();
      await page
        .getByRole("button", { name: "用户与密钥", exact: true })
        .click();
      await page
        .getByRole("button", { name: "＋ 新增用户", exact: true })
        .click();
      const name = `Browser_User_${port}_${run}`;
      await page.getByLabel("用户名称", { exact: true }).fill(name);
      await page.getByLabel("优先级（0–10）", { exact: true }).fill("7");
      await page.getByRole("button", { name: "保存用户", exact: true }).click();
      const row = page.locator("#userBody tr").filter({ hasText: name });
      await row.waitFor();
      await row.getByRole("button", { name: "发放密钥", exact: true }).click();
      await page.getByRole("button", { name: "生成密钥", exact: true }).click();
      await page
        .getByRole("heading", { name: "密钥已生成", exact: true })
        .waitFor();
      const key = await page.locator("#newSecret").inputValue();
      assert.ok(key.startsWith("utts_"));
      await page
        .locator("#secretDialog")
        .getByRole("button", { name: "关闭", exact: true })
        .click();
      assert.equal(await page.locator("#newSecret").inputValue(), "");
      const headers = {
        Authorization: "Bearer " + key,
        "Content-Type": "application/json",
      };
      let response = await fetch(base + "/v1/jobs", {
        method: "POST",
        headers,
        body: JSON.stringify({ text: "Browser verification" }),
      });
      assert.equal(response.status, 202);
      const job = await response.json();
      assert.ok(job.status_url.startsWith(prefix + "/v1/jobs/"));
      for (let i = 0; i < 50; i++) {
        response = await fetch(base + "/v1/jobs/" + job.id, { headers });
        const current = await response.json();
        if (current.state === "completed") break;
        assert.ok(["queued", "running"].includes(current.state));
        await new Promise((r) => setTimeout(r, 100));
      }
      response = await fetch(base + "/v1/jobs/" + job.id + "/audio", {
        headers,
      });
      assert.equal(response.status, 200);
      assert.equal(
        Buffer.from(await response.arrayBuffer())
          .subarray(0, 4)
          .toString(),
        "RIFF",
      );
      await page.getByRole("button", { name: "刷新", exact: true }).click();
      await page.locator("#keyBody tr").filter({ hasText: name }).waitFor();
      if (port === 10991)
        await page.screenshot({
          path: path.join(out, "users.png"),
          fullPage: true,
        });
      await page
        .getByRole("button", { name: "音色与试听", exact: true })
        .click();
      await page
        .getByRole("button", { name: "＋ 添加音色", exact: true })
        .click();
      const voice = `Browser_Voice_${port}_${run}`;
      await page.getByLabel("音色名称", { exact: true }).fill(voice);
      await page.getByLabel("GPT 权重", { exact: true }).setInputFiles({
        name: "test.ckpt",
        mimeType: "application/octet-stream",
        buffer: Buffer.from("synthetic gpt fixture"),
      });
      await page.getByLabel("SoVITS 权重", { exact: true }).setInputFiles({
        name: "test.pth",
        mimeType: "application/octet-stream",
        buffer: Buffer.from("synthetic sovits fixture"),
      });
      await page.getByLabel("参考音频", { exact: true }).setInputFiles({
        name: "test.wav",
        mimeType: "audio/wav",
        buffer: Buffer.from("RIFF synthetic reference fixture"),
      });
      await page.getByRole("button", { name: "保存音色", exact: true }).click();
      await page.locator(".voice-card").filter({ hasText: voice }).waitFor();
      await page
        .getByRole("button", { name: "生成语音 →", exact: true })
        .click();
      await page.locator("#audioResult:not(.hidden)").waitFor();
      await page.getByRole("button", { name: "服务总览", exact: true }).click();
      await page.getByRole("button", { name: "刷新", exact: true }).click();
      if (port === 10991)
        await page.screenshot({
          path: path.join(out, "overview.png"),
          fullPage: true,
        });
      await page.setViewportSize({ width: 390, height: 844 });
      if (port === 10991)
        await page.screenshot({
          path: path.join(out, "mobile.png"),
          fullPage: true,
        });
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
      assert.ok(
        await page.evaluate(() => !localStorage.getItem("umeko_admin_token")),
      );
      if (port === 10992)
        await page.getByRole("button", { name: "退出", exact: true }).click();
      else {
        await page.setViewportSize({ width: 1440, height: 1050 });
        await page
          .getByRole("button", { name: "退出登录 ↗", exact: true })
          .click();
      }
      await page.getByRole("heading", { name: "登录管理控制台" }).waitFor();
      assert.equal(
        (await context.request.get(base + "/api/status")).status(),
        401,
      );
      assert.equal(
        (
          await fetch(base + "/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          })
        ).status,
        401,
      );
      assert.deepEqual(errors, []);
      assert.ok(requests.every((p) => p.startsWith(prefix + "/")));
      assert.ok(requests.every((p) => !p.startsWith(prefix + prefix)));
      report.push({
        mode: port === 10991 ? "preserved" : "stripped",
        browser: "chromium",
        status: "passed",
        prefixRequestsVerified: requests.length,
      });
      await context.close();
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(
    path.join(out, "verification.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report, null, 2));
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
