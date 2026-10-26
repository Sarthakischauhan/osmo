/**
 * The voice ring with xAI's full documented roster: default view, then scrolled, with the edge fade,
 * the "…" markers and snapping to whole slots. The roster is the server's own `/voice-models`
 * (fetched once, unlocked here so no key is needed); nothing reaches xAI.
 *   OSMO_URL=http://127.0.0.1:8787 npx tsx apps/web/e2e/voice-ring-shots.ts <outDir>
 */
import { writeFileSync } from "node:fs";
import { type Page, chromium } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:8787";
const OUT = process.argv[2] ?? ".";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const VOICES = '[role=listbox][aria-label="Voices"] [role=option]';

interface Registry {
  models: { locked?: boolean; voices: unknown[] }[];
}

async function center(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`missing ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function main() {
  const registry = (await (await fetch(`${BASE}/voice-models`)).json()) as Registry;
  for (const model of registry.models) model.locked = false;
  const total = registry.models[0]?.voices.length ?? 0;
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1024, height: 760 } });
  await page.route("**/voice-models", (route) => route.fulfill({ json: registry }));
  await page.route("**/providers", (route) =>
    route.fulfill({ json: { providers: [{ id: "xai", label: "xAI", authKinds: ["api_key"], connected: true }] } }),
  );
  // No socket: the picker works from `/voice-models` alone, and nothing reaches the server's upstream.
  await page.routeWebSocket("**/ws", () => {});
  const checks: string[] = [];
  const check = (name: string, ok: boolean) => checks.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  const shot = async (name: string) => {
    await pause(500);
    const c = await center(page, ".dock .rp-orb");
    await page.screenshot({ path: `${OUT}/${name}.png`, clip: { x: c.x - 340, y: c.y - 340, width: 680, height: 430 } });
  };
  const visible = () =>
    page.locator(VOICES).evaluateAll((els) =>
      els.filter((el) => Number(getComputedStyle(el).opacity) > 0.05).map((el) => ({ text: el.textContent ?? "", opacity: Number(getComputedStyle(el).opacity) })),
    );

  await page.goto(BASE);
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector(".dock .rp-orb");
  const orb = await center(page, ".dock .rp-orb");
  await page.mouse.move(orb.x, orb.y, { steps: 4 });
  const xai = await center(page, '[role=listbox][aria-label="Providers"] [role=option]');
  await page.mouse.move(xai.x, xai.y, { steps: 6 });
  const latest = await center(page, '[role=listbox][aria-label="Models"] [role=option]');
  await page.mouse.move(latest.x, latest.y, { steps: 6 });
  await page.waitForSelector(VOICES);
  check(`registry carries the whole roster (${total} voices)`, total === 28);
  const before = await visible();
  check(`only a window is shown (${before.map((v) => v.text).join(", ")})`, before.length > 0 && before.length < total);
  check("a … marker shows more voices", (await page.locator(".rp-more").count()) >= 1);
  await shot("voices-1-default");

  const first = await center(page, VOICES);
  await page.mouse.move(first.x, first.y, { steps: 4 });
  // Sample opacities every animation frame through one snap glide: items crossing an end fade.
  // A plain string, so the bundler adds no helpers to code that runs in the page.
  const sampling = page.evaluate<number[]>(`new Promise((resolve) => {
    const seen = [];
    const until = performance.now() + 700;
    const tick = () => {
      for (const el of document.querySelectorAll(${JSON.stringify(VOICES)})) seen.push(Number(getComputedStyle(el).opacity));
      if (performance.now() < until) requestAnimationFrame(tick);
      else resolve(seen);
    };
    tick();
  })`);
  await page.mouse.wheel(0, 120);
  const moving = await sampling;
  const partial = moving.filter((o) => o > 0.02 && o < 0.98);
  check(`items fade at the ring's ends while scrolling (${partial.length} partial frames)`, partial.length > 0);
  for (let i = 0; i < 6; i++) {
    await page.mouse.wheel(0, 120);
    await pause(120);
  }
  await pause(900);
  const after = await visible();
  check(`scrolling moves the window (${after.map((v) => v.text).join(", ")})`, after.map((v) => v.text).join() !== before.map((v) => v.text).join());
  check(`… markers on both ends once scrolled (${await page.locator(".rp-more").count()})`, (await page.locator(".rp-more").count()) === 2);
  const snapped = await page.locator(VOICES).evaluateAll((els) => els.filter((el) => Number(getComputedStyle(el).opacity) >= 0.99).length);
  check(`settles on whole slots (${snapped} fully shown)`, snapped >= 1);
  await shot("voices-2-scrolled");

  writeFileSync(`${OUT}/voices-checks.txt`, `${checks.join("\n")}\n`);
  console.log(checks.join("\n"));
  await browser.close();
  if (checks.some((c) => c.startsWith("FAIL"))) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
