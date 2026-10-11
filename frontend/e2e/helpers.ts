import { readFileSync } from "node:fs";
import { expect, type Page } from "@playwright/test";

export function adminFor(project: string): { login: string; password: string } {
  return JSON.parse(readFileSync(`../.local-data/e2e/admin-${project}.json`, "utf-8"));
}
export const NEW_PASSWORD = "E2e-pass-2026";

export async function login(page: Page, loginName: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("登录名").fill(loginName);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
}

export async function loginAndWait(page: Page, loginName: string, password: string) {
  await login(page, loginName, password);
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

export async function firstLogin(page: Page, loginName: string, temp: string, next = NEW_PASSWORD) {
  await login(page, loginName, temp);
  await expect(page.getByRole("heading", { name: "设置新密码" })).toBeVisible();
  await page.getByLabel("新密码").fill(next);
  await page.getByLabel("再次输入").fill(next);
  await page.getByRole("button", { name: "保存并进入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

export async function noHorizontalOverflow(page: Page) {
  const [scroll, client] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(scroll, "页面整体不应横向溢出").toBeLessThanOrEqual(client + 1);
}

export async function shot(page: Page, name: string, project: string) {
  // 只在显式要求时更新仓库中的截图证据（SAVE_SCREENSHOTS=1）；CI 不改工作区，避免构建门禁判为未提交修改
  if (process.env.SAVE_SCREENSHOTS !== "1") return;
  await page.screenshot({ path: `../verification/evidence/d2-screens/${name}-${project}.png`, fullPage: true });
}

/** 手机对话框不应出现内部滚动：内容区放得下，且对话框不超出视口。DIALOG_SHOTS_DIR 设置时顺便截图。 */
export async function dialogFitsViewport(page: Page, name: string) {
  const m = await page.evaluate(() => {
    const d = document.querySelector("#record-dialog")!;
    const body = d.querySelector(".dialog-body")!;
    const r = d.getBoundingClientRect();
    return { scroll: body.scrollHeight, client: body.clientHeight, top: r.top, bottom: r.bottom, vh: window.innerHeight };
  });
  if (process.env.DIALOG_SHOTS_DIR) await page.screenshot({ path: `${process.env.DIALOG_SHOTS_DIR}/${name}.png` });
  expect(m.scroll, `${name}：对话框内容区不应需要滚动`).toBeLessThanOrEqual(m.client + 1);
  expect(m.top, `${name}：对话框顶部不应超出视口`).toBeGreaterThanOrEqual(0);
  expect(m.bottom).toBeLessThanOrEqual(m.vh + 1);
}
