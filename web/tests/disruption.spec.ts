import { expect, test } from "@playwright/test";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);

test("live banner, playable voice, scoped confirmed fix, mobile/dark/keyboard", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/b/lisbon-fixture-demo");
  await expect(page.getByRole("heading", { name: "A few days in Lisbon" })).toBeVisible();
  await expect(page.getByText("Live updates connected", { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Continue as demo traveler" }).click();
  await expect(page.getByRole("button", { name: "Demo traveler signed in" })).toBeVisible();
  const before = await (await page.request.get("/api/boards/lisbon-fixture-demo")).json();
  const minutes = 300 + Math.floor(Date.now() / 1000) % 200;
  const summary = `SIMULATED: TP1351 delayed ${minutes} minutes`;
  const root = path.resolve("..");
  const simulation = run(path.join(root, ".venv", "Scripts", "python.exe"), [
    "scripts/simulate_disruption.py", "--type", "flight_delay", "--minutes", String(minutes),
  ], { cwd: root, timeout: 35_000 });
  await expect(page.getByText(summary, { exact: true })).toBeVisible({ timeout: 30_000 });
  const observedAt = Date.now();
  await simulation;
  const during = await (await page.request.get("/api/boards/lisbon-fixture-demo")).json();
  const disruption = during.disruptions.find((d: { evidence: { summary: string }[] }) => d.evidence[0].summary === summary);
  const boardAlert = during.alerts.find((a: { disruption_id: string; channel: string }) => a.disruption_id === disruption._id && a.channel === "board");
  expect(observedAt - new Date(boardAlert.created_at).getTime()).toBeLessThanOrEqual(2000);
  const banner = page.locator(".astryx-banner-frame").filter({ hasText: summary });
  const audio = banner.locator("audio");
  await expect(audio).toBeVisible();
  const duration = await audio.evaluate(async element => {
    const audio = element as HTMLAudioElement;
    audio.load();
    await new Promise<void>((resolve, reject) => {
      audio.onloadedmetadata = () => resolve();
      audio.onerror = () => reject(new Error("Audio failed to load"));
    });
    return audio.duration;
  });
  expect(duration).toBeGreaterThan(1);
  expect(duration).toBeLessThanOrEqual(20);
  await page.screenshot({ path: "test-results/mobile-light.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Dark mode", exact: true }).click();
  await page.screenshot({ path: "test-results/mobile-dark.png", fullPage: true });
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.tagName !== "BODY")).toBe(true);
  await banner.getByRole("button", { name: "Shift arrival-day plans", exact: true }).click();
  await expect(page.getByText(`Board version ${before.board.version + 1}`, { exact: false })).toBeVisible();
  const after = await (await page.request.get("/api/boards/lisbon-fixture-demo")).json();
  expect(after.disruptions.find((d: { _id: string }) => d._id === disruption._id).status).toBe("resolved");
  expect(after.board.days.slice(1)).toEqual(before.board.days.slice(1));
  expect(after.board.days[0]).not.toEqual(before.board.days[0]);
  await page.goto("/trip/trip_lisbon/disruptions");
  await expect(page.getByRole("heading", { name: "Trip updates", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "test-results/desktop-dark.png", fullPage: true });
  expect(errors).toEqual([]);
});
