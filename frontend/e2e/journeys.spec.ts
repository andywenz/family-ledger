import { expect, test } from "@playwright/test";
import { NEW_PASSWORD, adminFor, firstLogin, loginAndWait, noHorizontalOverflow, shot } from "./helpers";

// 串行旅程：同一个测试管理员，桌面与手机各跑一遍（各自新建家庭）。
test.describe.configure({ mode: "serial" });

let changed = false;

/** 桌面用侧边栏，手机用“更多”菜单切换家庭并进入二级页面。 */
async function openFamilyPage(page: import("@playwright/test").Page, familyName: string, linkName: string) {
  if (test.info().project.name === "mobile") {
    // 以“打开菜单→点链接”整体重试，容忍页面仍在加载
    if (!(await page.locator(".breadcrumb").textContent())?.includes(familyName)) {
      await page.getByRole("button", { name: "更多" }).click();
      await page.getByLabel("手机切换家庭").selectOption({ label: familyName });
      await expect(page.getByRole("dialog")).toBeHidden(); // 切换家庭会关闭菜单
      await expect(page.locator(".breadcrumb")).toContainText(familyName);
    }
    await expect(async () => {
      if (!(await page.getByRole("dialog").isVisible())) {
        await page.getByRole("button", { name: "更多" }).click();
      }
      await page.getByRole("dialog").getByRole("link", { name: linkName }).click({ timeout: 3000 });
    }).toPass({ timeout: 20000 });
  } else {
    await page.getByLabel("切换家庭").selectOption({ label: familyName });
    await page.getByRole("link", { name: linkName }).click();
  }
}

async function signInAdmin(page: import("@playwright/test").Page) {
  const admin = adminFor(test.info().project.name);
  if (!changed) {
    await firstLogin(page, admin.login, admin.password);
    changed = true;
  } else {
    await loginAndWait(page, admin.login, NEW_PASSWORD);
  }
}

test("首次登录→创建家庭→记一笔→编辑→退款→删除恢复", async ({ page }, info) => {
  const project = info.project.name;
  await signInAdmin(page);
  await expect(page).toHaveURL(/\/(families|f\/)/);
  await page.goto("/families");
  await page.getByLabel("新家庭名称").fill(`家-${project}`);
  await page.getByRole("button", { name: "创建家庭" }).click();
  await expect(page.getByRole("heading", { name: "月度总览" })).toBeVisible();
  await expect(page.getByText("还没有符合条件的记录。")).toBeVisible();
  await noHorizontalOverflow(page);
  await shot(page, "01-dashboard-empty", project);

  // 记一笔：服务端折算预览
  await page.getByRole("link", { name: "记一笔" }).first().click();
  await page.getByLabel("原始金额", { exact: true }).fill("5");
  await expect(page.locator(".conversion-strip")).toContainText("合 CNY");
  await expect(page.locator(".conversion-strip strong").nth(0)).not.toHaveText("—");
  await page.getByLabel("备注").fill("=SUM(A1) 超市");
  await noHorizontalOverflow(page);
  await shot(page, "02-new-entry", project);
  await page.getByRole("button", { name: "保存这笔记录" }).click();
  await expect(page.getByRole("cell", { name: "=SUM(A1) 超市", exact: true })).toBeVisible();
  await expect(page.locator(".spending-hero .number")).toContainText("5.00");
  await shot(page, "03-dashboard-one", project);
  // 导出：一个“导出”按钮，展开两种格式；点 Excel 触发下载
  await page.getByText("导出", { exact: true }).click();
  await expect(page.getByRole("menuitem")).toHaveCount(2);
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: /Excel/ }).click();
  expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);

  // 编辑：取消不变，保存更新同一行（ACC-12 浏览器旅程）
  await page.getByRole("button", { name: /查看或编辑/ }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("原始金额", { exact: true }).fill("18");
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "关闭", exact: true }).last().click();
  await expect(page.locator(".spending-hero .number")).toContainText("5.00");
  await page.getByRole("button", { name: /查看或编辑/ }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("原始金额", { exact: true }).fill("18");
  await shot(page, "04-edit-dialog", project);
  await page.getByRole("dialog").getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.locator(".spending-hero .number")).toContainText("18.00");
  await expect(page.locator("tbody tr")).toHaveCount(1);

  // 退款：关联原消费
  await page.getByRole("button", { name: /查看或编辑/ }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "退款", exact: true }).click();
  await expect(page.getByText(/尚可退款 NZD 18\.00/)).toBeVisible();
  await page.getByLabel("原始金额", { exact: true }).fill("3");
  await page.getByRole("dialog").getByRole("button", { name: "保存退款", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(page.locator(".spending-hero .number")).toContainText("15.00");

  // 删除有退款的消费：明确提示一并删除
  await page.getByRole("row", { name: /支出/ }).getByRole("button", { name: /查看或编辑/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByText(/关联退款，将一并移入回收站/)).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByText("还没有符合条件的记录。")).toBeVisible();

  if (project === "mobile") {
    await page.getByRole("button", { name: "更多" }).click();
    await page.getByRole("dialog").getByRole("link", { name: "回收站" }).click();
  } else {
    await page.getByRole("link", { name: "回收站" }).click();
  }
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await shot(page, "05-trash", project);
  await page.getByRole("row", { name: /支出/ }).getByRole("button", { name: "恢复" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "月度总览" }).click();
  await expect(page.locator(".spending-hero .number")).toContainText("18.00");
});

test("系统管理员建账号→成员首次登录→邀请加入→成员只能查看他人账目", async ({ page, browser }, info) => {
  const project = info.project.name;
  await signInAdmin(page);
  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "系统账号" })).toBeVisible();
  const memberLogin = `m${Date.now() % 1e8}`;
  await page.getByLabel("登录名").fill(memberLogin);
  await page.getByLabel("显示名").fill("成员乙");
  await page.getByRole("button", { name: "创建" }).click();
  const temp = (await page.locator(".secret-notice code").textContent())!.trim();
  expect(temp.length).toBeGreaterThanOrEqual(16);
  await shot(page, "06-admin-users", project);
  await page.getByRole("button", { name: "我已记下" }).click();

  // 邀请到管理员的第一个家庭
  await openFamilyPage(page, `家-${project}`, "家庭与设置");
  await page.getByLabel("登录名").fill(memberLogin);
  await page.getByRole("button", { name: "发送邀请" }).click();
  await expect(page.getByText("待接受")).toBeVisible();
  await shot(page, "07-family-settings", project);

  const ctx2 = await browser.newContext(info.project.use);
  const member = await ctx2.newPage();
  await firstLogin(member, memberLogin, temp, "Member-pass-2026");
  await expect(member.getByRole("heading", { name: "选择家庭" })).toBeVisible();
  await member.getByRole("button", { name: "加入" }).click();
  await expect(member.getByRole("heading", { name: "月度总览" })).toBeVisible();
  // 管理员录入的账目：成员只能“查看”，没有编辑按钮
  await expect(member.getByRole("button", { name: /查看或编辑/ }).first()).toContainText("查看");
  await member.getByRole("button", { name: /查看或编辑/ }).first().click();
  await expect(member.getByRole("dialog").getByRole("button", { name: "编辑", exact: true })).toHaveCount(0);
  await noHorizontalOverflow(member);
  await ctx2.close();
});

test("分类改名同步到历史账目", async ({ page }, info) => {
  await signInAdmin(page);
  await openFamilyPage(page, `家-${info.project.name}`, "分类管理");
  await page.getByRole("button", { name: "修改食品与餐饮" }).click();
  await page.getByRole("dialog").getByLabel("名称").fill("吃喝");
  await page.getByRole("dialog").getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("heading", { name: "吃喝" })).toBeVisible();
  await shot(page, "08-categories", info.project.name);
  await page.getByRole("link", { name: "月度总览" }).click();
  await expect(page.getByRole("cell", { name: "吃喝", exact: true }).first()).toBeVisible();
});

test("AI 识别→修改候选→确认入账（离线替身模型）", async ({ page }, info) => {
  const project = info.project.name;
  await signInAdmin(page);
  await openFamilyPage(page, `家-${project}`, "家庭与设置");
  await page.getByRole("link", { name: "AI 帮我记" }).click();
  await page.getByLabel("今天有什么开销？").fill("超市45纽币，停车8纽币");
  await page.getByRole("button", { name: "开始识别" }).click();
  await expect(page.getByRole("heading", { name: "待你确认" })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".candidate-card")).toHaveCount(2);
  await expect(page.locator(".field-tag", { hasText: "默认值" }).first()).toBeVisible();
  const amount = page.locator(".candidate-card").first().getByLabel(/金额/);
  await amount.fill("46.5");
  await amount.blur();
  await expect(page.locator(".candidate-card").first().locator(".candidate-header strong")).toContainText("46.50");
  await shot(page, "09-ai-candidates", project);
  await page.getByRole("button", { name: /确认并记账/ }).click();
  await expect(page.getByText("已入账 2 笔")).toBeVisible();
  // 确认后直接回到输入页，无需再点“再记一条”
  await expect(page.getByLabel("今天有什么开销？")).toHaveValue("");
  await expect(page.getByRole("heading", { name: "待你确认" })).toHaveCount(0);
  await page.getByRole("link", { name: "月度总览" }).click();
  await expect(page.getByRole("cell", { name: "46.50", exact: true }).first()).toBeVisible();
  // Dashboard 不再显示收入／结余卡片；净支出、分类、消费方式在同一区域
  await expect(page.getByText("本月收入")).toHaveCount(0);
  await expect(page.getByText("本月结余")).toHaveCount(0);
  await expect(page.locator(".dashboard-top > .card")).toHaveCount(3);
  await noHorizontalOverflow(page);
});

test("我的账号：生成飞书绑定码", async ({ page }, info) => {
  await signInAdmin(page);
  if (info.project.name === "mobile") {
    await page.getByRole("button", { name: "更多" }).click();
    await page.getByRole("dialog").getByRole("link", { name: /我的账号/ }).click();
  } else {
    await page.getByRole("link", { name: "我的账号" }).click();
  }
  await expect(page.getByRole("heading", { name: "飞书记账" })).toBeVisible();
  await page.getByRole("button", { name: "生成绑定码" }).click();
  await expect(page.locator(".secret-notice code")).toHaveText(/^绑定 [A-Z0-9]{8}$/);
  await shot(page, "10-feishu-binding", info.project.name);
  await noHorizontalOverflow(page);
});

test("界面风格：三种可切换并在刷新后保持", async ({ page }, info) => {
  const project = info.project.name;
  await signInAdmin(page);
  await openFamilyPage(page, `家-${project}`, "分类管理");
  await page.getByRole("link", { name: "月度总览" }).click();
  const dir = process.env.THEME_SHOTS_DIR;
  for (const [id, label] of [["juicy", "果汁色块"], ["night", "夜光"], ["pop", "手账波普"]] as const) {
    await page.getByTitle(label).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", id);
    await noHorizontalOverflow(page);
    if (dir) {
      // 等数据加载完、颜色过渡结束再截图
      const settle = async () => { await page.waitForLoadState("networkidle", { timeout: 3000 }).catch(() => undefined); await page.waitForTimeout(600); };
      await expect(page.getByText("本月明细")).toBeVisible();
      await settle();
      await page.screenshot({ path: `${dir}/${id}-dashboard-${project}.png`, fullPage: true });
      await page.getByRole("link", { name: "记一笔" }).first().click();
      await expect(page.getByLabel("原始金额")).toBeVisible();
      await settle();
      await page.screenshot({ path: `${dir}/${id}-new-${project}.png`, fullPage: true });
      await page.getByRole("link", { name: "AI 帮我记" }).first().click();
      await expect(page.getByLabel("今天有什么开销？")).toBeVisible();
      await settle();
      await page.screenshot({ path: `${dir}/${id}-ai-${project}.png`, fullPage: true });
      await page.getByRole("link", { name: "月度总览" }).first().click();
      if (project === "desktop") {
        await expect(page.getByText("本月明细")).toBeVisible();
        const dashboardUrl = page.url();
        await page.getByRole("button", { name: /查看或编辑/ }).first().click();
        await settle();
        await page.screenshot({ path: `${dir}/${id}-dialog-${project}.png` });
        await page.keyboard.press("Escape");
        for (const [link, name] of [["分类管理", "categories"], ["家庭与设置", "settings"], ["回收站", "trash"], ["我的账号", "account"], ["系统账号", "admin"], ["费用与告警", "costs"]] as const) {
          await page.getByRole("link", { name: link }).first().click();
          await settle();
          await page.screenshot({ path: `${dir}/${id}-${name}-${project}.png`, fullPage: true });
        }
        await page.goto(dashboardUrl); // 系统页面不在家庭内，侧栏没有“月度总览”
      }
    }
  }
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "pop"); // 刷新后保持上次选择
  await page.getByTitle("果汁色块").click();
});
