import { APIRequestContext, expect, Page, test } from "@playwright/test";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * T29 整体验收：按 WIREFRAME §2 的三条核心流程走一遍控制台 + Open API。
 * 环境由 e2e/run.sh 准备（独立 e2e 库、真实 arq Worker、FastAPI 托管前端构建产物）。
 */
const DATA = process.env.CF_E2E_DATA || join(__dirname, ".data");
const BIZ_DB = process.env.CF_E2E_BIZ_DB || "cellflow_e2e_biz";
const TOKEN = "e2e-op-token";
const APP_SECRET = "e2e-app-secret";
const file = (n: string) => join(DATA, n);

test.describe.configure({ mode: "serial" });

let page: Page;
let appKey = "";
let pipelineId = 0;

async function opConfirm(p: Page, reason = "e2e 验收") {
  const dlg = p.locator(".ant-modal").filter({ has: p.locator("#op-token") });
  await expect(dlg).toBeVisible();
  const r = dlg.locator("#op-reason");
  if (await r.isVisible()) await r.fill(reason);
  await dlg.locator("#op-token").fill(TOKEN);
  await dlg.locator("#op-confirm").click();
  await expect(dlg).toBeHidden();
}

async function nav(p: Page, label: string) {
  await p.locator(".cf-header .nav a", { hasText: label }).click();
}

async function openCall(api: APIRequestContext, method: string, path: string, multipart?: { file: string; meta: any }) {
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = randomUUID().replace(/-/g, "");
  let body: Buffer = Buffer.alloc(0);
  let contentType: string | undefined;
  if (multipart) {
    const boundary = "cfb" + randomUUID().replace(/-/g, "");
    const parts = [
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="meta"\r\n\r\n${JSON.stringify(multipart.meta)}\r\n`),
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="upload.xlsx"\r\nContent-Type: application/octet-stream\r\n\r\n`),
      readFileSync(multipart.file),
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ];
    body = Buffer.concat(parts);
    contentType = `multipart/form-data; boundary=${boundary}`;
  }
  const msg = [method, path, ts, nonce, createHash("sha256").update(body).digest("hex")].join("\n");
  const headers: Record<string, string> = {
    "X-CF-AppKey": appKey, "X-CF-Timestamp": ts, "X-CF-Nonce": nonce,
    "X-CF-Signature": createHmac("sha256", APP_SECRET).update(msg).digest("hex"),
  };
  if (contentType) headers["Content-Type"] = contentType;
  const r = await api.fetch(path, { method, headers, data: multipart ? body : undefined });
  return { status: r.status(), body: await r.json() };
}

async function submit(api: APIRequestContext, name: string, mode = "EXECUTE") {
  const r = await openCall(api, "POST", "/open/v1/jobs", { file: file(name), meta: { pipelineCode: "hero_config", mode, idempotencyKey: randomUUID(), operator: "planner_zhang" } });
  expect(r.status, JSON.stringify(r.body)).toBe(202);
  return r.body.data.jobId as number;
}

async function waitJob(api: APIRequestContext, id: number) {
  for (let i = 0; i < 120; i++) {
    const r = await openCall(api, "GET", `/open/v1/jobs/${id}`);
    if (!["SUBMITTED", "QUEUED", "RUNNING"].includes(r.body.data.status)) return r.body.data;
    await new Promise((res) => setTimeout(res, 500));
  }
  throw new Error(`任务 #${id} 超时未完成`);
}

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
});

test("控制台首次进入：设置操作人", async () => {
  await page.goto("/");
  await expect(page.locator("#operator-input")).toBeVisible();
  await page.locator("#operator-input").fill("验收员");
  await page.getByRole("button", { name: /^保\s*存$/ }).click();
  await expect(page.locator("#operator-name")).toContainText("验收员");
});

test("P9 新建数据源（口令错误提示 → 正确口令）并测试连接", async () => {
  await nav(page, "数据源");
  await page.locator("#new-datasource").click();
  const m = page.locator(".ant-modal", { hasText: "新建数据源" });
  await m.getByText("环境变量引用（生产推荐）").click();
  await m.getByLabel("名称").fill("业务库");
  await m.getByLabel("库名").fill(BIZ_DB);
  await m.getByLabel("连接地址引用名").fill("E2E_BIZ");
  await m.getByRole("button", { name: /保存/ }).click();
  const dlg = page.locator(".ant-modal").filter({ has: page.locator("#op-token") });
  await dlg.locator("#op-token").fill("wrong-token");
  await dlg.locator("#op-confirm").click();
  await expect(dlg).toContainText("口令错误");
  await opConfirm(page);
  await expect(page.locator(".ant-table-row", { hasText: "业务库" })).toBeVisible();
  // 页面不出现真实连接串
  await expect(page.locator("body")).not.toContainText("mysql+pymysql");
  await page.locator(".ant-table-row", { hasText: "业务库" }).getByText("测试连接").click();
  for (const k of ["connect", "CREATE", "DROP", "ALTER"]) {
    await expect(page.locator(".ant-table-row .ant-tag", { hasText: new RegExp(`^${k}$`) })).toHaveClass(/green/);
  }
});

test("P9 直接填写连接信息新建第二个数据源（口令加密、不回显）", async ({ request }) => {
  const u = new URL((process.env.CF_E2E_MYSQL_URL || "mysql://cellflow:cellflow_dev@127.0.0.1:3306").replace(/^mysql\+pymysql/, "mysql"));
  await page.locator("#new-datasource").click();
  const m = page.locator(".ant-modal", { hasText: "新建数据源" });
  await m.getByLabel("名称").fill("直连库");
  await m.getByLabel("库名").fill(BIZ_DB);
  await m.getByLabel("主机").fill(u.hostname);
  await m.getByLabel("端口").fill(u.port || "3306");
  await m.getByLabel("账号").fill(decodeURIComponent(u.username));
  await m.getByLabel("口令").fill(decodeURIComponent(u.password));
  await m.getByRole("button", { name: /保存/ }).click();
  await opConfirm(page);
  const row = page.locator(".ant-table-row", { hasText: "直连库" });
  await expect(row).toContainText("直接填写");
  await row.getByText("测试连接").click();
  await expect(row.locator(".ant-tag", { hasText: /^CREATE$/ })).toHaveClass(/green/);
  const list = await (await request.get("/api/datasources")).text();
  expect(list).not.toContain(decodeURIComponent(u.password));
});

test("P2 新建方案并上传样例文件，进入工作台", async () => {
  await nav(page, "方案");
  await page.locator("#new-pipeline").click();
  await page.locator("#new-code").fill("hero_config");
  await page.locator("#new-name").fill("角色配置");
  await page.locator("#new-ds").click();
  await page.locator(".ant-select-item-option", { hasText: "业务库" }).click();
  await page.locator(".ant-modal input[type=file]").setInputFiles(file("hero.xlsx"));
  await expect(page.locator(".ant-upload-list-item-name", { hasText: "hero.xlsx" })).toBeVisible();
  await page.getByRole("button", { name: /^创\s*建$/ }).click();
  await expect(page).toHaveURL(/#\/pipelines\/\d+\/canvas/);
  pipelineId = Number(page.url().match(/pipelines\/(\d+)/)![1]);
  await expect(page.locator("#sample-file-name")).toContainText("hero.xlsx");
});

test("P3 工作台：载入示例方案、完整试跑、查看结果与变更预判", async ({ request }) => {
  const draft = await (await request.get(`/api/pipelines/${pipelineId}/draft`)).json();
  const dsl = JSON.parse(readFileSync(file("hero_dsl.json"), "utf8"));
  const saved = await request.put(`/api/pipelines/${pipelineId}/draft`, { data: { dsl, draftVersion: draft.data.draftVersion }, headers: { "X-CF-Operator": encodeURIComponent("验收员") } });
  expect((await saved.json()).data.analysis.ok).toBeTruthy();
  await page.reload();
  await expect(page.locator(".cf-node")).toHaveCount(dsl.nodes.length);
  await expect(page.locator(".cf-node.error")).toHaveCount(0);
  await page.locator("#run-full").click();
  await page.locator(".ant-dropdown-menu-item", { hasText: "用样例文件" }).click();
  await expect(page.locator(".cf-result")).toContainText("完整试跑", { timeout: 60_000 });
  await page.locator(".cf-result .ant-tabs-tab", { hasText: "变更摘要" }).click();
  await expect(page.locator(".cf-result")).toContainText("cfg_level_reward");
});

test("P4-1 发布首个版本（无历史文件可回归）", async () => {
  await page.locator("#goto-publish").click();
  await expect(page.locator("#publish-title")).toContainText("v1");
  await expect(page.getByText("没有可回归的历史文件")).toBeVisible({ timeout: 60_000 });
  await page.locator("#publish-note").fill("首版");
  await page.locator("#do-publish").click();
  await opConfirm(page);
  await expect(page).toHaveURL(/versions/);
  await expect(page.locator(".ant-table-row", { hasText: "v1" })).toContainText("生效中");
});

test("P10 新建调用方并授权方案", async ({ request }) => {
  await nav(page, "调用方");
  await page.locator("#new-client").click();
  const m = page.locator(".ant-modal", { hasText: "新建调用方" });
  await m.getByLabel("名称").fill("业务服务A");
  await m.getByLabel("签名密钥引用名").fill("E2E_APP_SECRET");
  await m.getByLabel("授权方案").click();
  await page.locator(".ant-select-item-option", { hasText: "hero_config" }).click();
  await m.getByRole("button", { name: /保存/ }).click();
  await opConfirm(page);
  await expect(page.locator(".ant-table-row", { hasText: "业务服务A" })).toBeVisible();
  const apps = await (await request.get("/api/client-apps")).json();
  appKey = apps.data[0].appKey;
  expect(appKey).toBeTruthy();
});

let firstJob = 0;
test("Open API 提交 → Worker 执行 → 写入业务表；P5/P6 查看", async ({ request }) => {
  firstJob = await submit(request, "hero.xlsx");
  const j = await waitJob(request, firstJob);
  expect(j.status).toBe("PUBLISHED");
  await nav(page, "任务");
  await page.locator(".ant-tabs-tab", { hasText: "全部" }).click();
  await page.locator(".ant-table-row", { hasText: `#${firstJob}` }).click();
  await expect(page.locator(".cf-page h2")).toContainText(`任务 #${firstJob}`);
  await expect(page.locator(".cf-page")).toContainText("已写入");
  await page.locator(".ant-tabs-tab", { hasText: "变更明细" }).click();
  await expect(page.locator(".ant-table-row .ant-tag", { hasText: "新增" }).first()).toBeVisible();
});

test("Open API 签名错误被拒绝", async ({ request }) => {
  const r = await request.post("/open/v1/jobs", { headers: { "X-CF-AppKey": appKey, "X-CF-Timestamp": String(Math.floor(Date.now() / 1000)), "X-CF-Nonce": "n1", "X-CF-Signature": "00" }, multipart: { meta: "{}" } });
  expect(r.status()).toBe(401);
});

let blocked = 0;
test("安全闸拦截 → 待处理角标 → P6 放行", async ({ request }) => {
  // 奖励明细 5 → 15 行：行数波动超过 50%（G3），被拦截等待人工确认
  blocked = await submit(request, "hero_more.xlsx");
  const j = await waitJob(request, blocked);
  expect(j.status).toBe("FAILED_GUARD");
  expect(j.blockedBy).toEqual(["G3"]);
  await page.reload();
  await expect(page.locator(".cf-header .ant-badge-count")).toBeVisible({ timeout: 20_000 });
  await nav(page, "任务");
  await page.locator(".ant-tabs-tab", { hasText: "待处理" }).click();
  await page.locator(".ant-table-row", { hasText: `#${blocked}` }).click();
  await page.locator(".ant-tabs-tab", { hasText: "安全闸结果" }).click();
  await expect(page.locator("#guard-table .ant-table-row", { hasText: "G3" }).filter({ hasText: "cfg_level_reward" })).toContainText("未通过");
  await page.locator("#force-publish").click();
  await opConfirm(page, "确认新增奖励行");
  await expect(page.locator(".cf-page")).toContainText("查看对应发布");
});

test("只校验模式：整体下移 3 行仍能按锚点定位", async ({ request }) => {
  const id = await submit(request, "hero_shifted.xlsx", "VALIDATE_ONLY");
  const j = await waitJob(request, id);
  expect(j.status).toBe("VALIDATED");
  await page.goto(`/#/jobs/${id}`);
  await expect(page.locator(".cf-page")).toContainText("校验通过");
  await page.locator(".ant-tabs-tab", { hasText: "文件与指标" }).click();
  await expect(page.getByRole("heading", { name: "定位报告" })).toBeVisible();
  await expect(page.locator(".ant-table-row", { hasText: "角色配置" }).first()).toBeVisible();
});

test("校验失败的文件不写表，问题可读", async ({ request }) => {
  const id = await submit(request, "hero_newcol.xlsx");
  const j = await waitJob(request, id);
  expect(j.status).toBe("FAILED_VALIDATION");
  expect(j.releaseId).toBeNull();
  await page.goto(`/#/jobs/${id}`);
  await expect(page.locator("#job-issues")).toContainText("找不到表头「数量」");
  await expect(page.locator("#job-issues")).toContainText("发现未配置的新列：备注");
});

test("P7 发布历史 → P7-1 回滚并冻结 → 冻结期间拒绝提交 → 解冻", async ({ request }) => {
  await page.goto(`/#/pipelines/${pipelineId}/releases`);
  const rows = page.locator(".ant-table-row");
  await expect(rows.first()).toBeVisible();
  const normal = rows.filter({ hasText: "正常" }).last();
  await normal.getByRole("button", { name: "回滚到此版本" }).click();
  await expect(page.locator("#rollback-preview")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("#rollback-freeze")).toBeChecked();
  await page.locator("#do-rollback").click();
  await opConfirm(page, "线上数据有误，回退");
  await expect(page.locator("#frozen-banner")).toBeVisible();
  await expect(rows.filter({ hasText: "回滚" }).first()).toContainText("生效中");
  const r = await openCall(request, "POST", "/open/v1/jobs", { file: file("hero.xlsx"), meta: { pipelineCode: "hero_config", mode: "EXECUTE" } });
  expect(r.body.code).toBe("PIPELINE_FROZEN");
  await page.locator("#freeze-toggle").click();
  await opConfirm(page, "问题已修复");
  await expect(page.locator("#frozen-banner")).toBeHidden();
});

test("P11 修改系统设置（比例校验）→ P12 审计日志可查", async ({ request }) => {
  const bad = await request.put("/api/settings", { data: { "guard.maxDeleteRatio": 1.5 }, headers: { "X-CF-Op-Token": TOKEN, "X-CF-Operator": "e2e" } });
  expect((await bad.json()).code).toBe("SETTING_INVALID");
  await nav(page, "系统设置");
  await page.locator(".ant-tabs-tab", { hasText: "安全闸" }).click();
  const input = page.locator(".ant-table-row", { hasText: "guard.maxDeleteRatio" }).locator("input");
  await input.fill("0.4");
  await input.blur();
  await page.locator("#save-settings").click();
  await opConfirm(page);
  await expect(page.locator(".ant-table-row", { hasText: "guard.maxDeleteRatio" })).toContainText("验收员");
  await nav(page, "审计日志");
  for (const a of ["EDIT_SETTING", "FREEZE", "UNFREEZE", "ROLLBACK", "FORCE_PUBLISH", "PUBLISH_REVISION", "EDIT_CLIENT", "EDIT_DATASOURCE"]) {
    await expect(page.locator(".ant-table-row", { hasText: a }).first()).toBeVisible();
  }
});
