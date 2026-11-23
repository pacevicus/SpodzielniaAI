/**
 * Scenario 17 (US-09): AI usage monitoring. A member sees their own usage at `/me/usage`, and the
 * administrator sees every member and the system at `/admin/usage` (ADMIN + MFA). A member is not
 * shown the administrator's page or its menu entry. A reversed period is refused on the page
 * before any request. The screens fit a phone without sideways scrolling and pass axe.
 * Desktop in `chromium`; the phone checks in `mobile`.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { session, watchCsp } from "./support/stack";

const MEMBER = "member2";
const ADMIN = "admin1";
const project = () => test.info().project.name;

/** Serious or critical axe findings. */
async function axe(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
}

const menu = (page: Page) => page.locator("aside[data-sidebar]").first();

test.describe("desktop", () => {
  test.beforeEach(() => test.skip(project() !== "chromium", "desktop only"));

  test("a member reads their own usage and cannot open the administrator's view", async ({ browser }) => {
    const page = await session(browser, MEMBER, "/me/usage");
    const violations = await watchCsp(page);
    try {
      await expect(page.getByRole("heading", { level: 1, name: "Zużycie AI" })).toBeVisible();
      // The four figures and the period form are on the page.
      await expect(page.getByText("Wywołania", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("Koszt szacunkowy", { exact: true }).first()).toBeVisible();
      await expect(page.getByLabel("Od", { exact: true })).toHaveValue(/^\d{4}-\d{2}-\d{2}$/);

      // A reversed period is explained on the page and not sent.
      await page.getByLabel("Od", { exact: true }).fill("2030-01-01");
      // Scoped to the form: the framework also renders its own (empty) alert region.
      await expect(page.locator("form").getByRole("alert")).toHaveText("Data „do” jest wcześniej niż data „od”.");
      await expect(page.getByRole("button", { name: "Pokaż" })).toBeDisabled();

      // The administrator's menu entry is not offered to a member.
      await expect(menu(page).getByRole("link", { name: "Zużycie AI w spółdzielni" })).toHaveCount(0);
      await page.goto("/admin/usage");
      await expect(page.getByRole("heading", { level: 1, name: "Zużycie AI w spółdzielni" })).toHaveCount(0);

      expect(await violations()).toEqual([]);
      expect(await axe(page)).toEqual([]);
    } finally {
      await page.context().close();
    }
  });

  test("the administrator sees every member and the system's own work", async ({ browser }) => {
    const page = await session(browser, ADMIN, "/admin/usage");
    const violations = await watchCsp(page);
    try {
      await page.reload(); // the CSP watcher is installed on navigation, so load the page again
      await expect(page.getByRole("heading", { level: 1, name: "Zużycie AI w spółdzielni" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 2, name: "Członkowie" })).toBeVisible();
      // The system row is always listed, even with no work in the period.
      await expect(page.getByRole("cell", { name: "Praca systemowa (bez członka)" })).toBeVisible();
      await expect(page.getByText("Ceny obowiązują od aktywacji wersji konfiguracji")).toBeVisible();

      expect(await violations()).toEqual([]);
      expect(await axe(page)).toEqual([]);
    } finally {
      await page.context().close();
    }
  });
});

test.describe("phone", () => {
  test.beforeEach(() => test.skip(project() !== "mobile", "mobile only"));

  test("the usage pages fit the phone width", async ({ browser }) => {
    const member = await session(browser, MEMBER, "/me/usage");
    try {
      await expect(member.getByRole("heading", { level: 1, name: "Zużycie AI" })).toBeVisible();
      const overflow = await member.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    } finally {
      await member.context().close();
    }
    const admin = await session(browser, ADMIN, "/admin/usage");
    try {
      await expect(admin.getByRole("heading", { level: 1, name: "Zużycie AI w spółdzielni" })).toBeVisible();
      const overflow = await admin.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    } finally {
      await admin.context().close();
    }
  });
});
