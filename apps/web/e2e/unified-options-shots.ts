/**
 * Every ring (providers, models, voices, effort) uses the one option primitive, so every option has the
 * same computed height and padding. Opens all four rings, measures every visible option, exits non-zero
 * if any ring differs, and saves one screenshot per ring. Server data is stubbed; nothing reaches a provider.
 *   OSMO_URL=http://127.0.0.1:8787 npx tsx apps/web/e2e/unified-options-shots.ts <outDir>
 */
import { writeFileSync } from "node:fs";
import { type Page, chromium } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:8787";
const OUT = process.argv[2] ?? ".";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const RINGS = ["Providers", "Models", "Voices", "Reasoning effort"] as const;
const options = (ring: string) => `[role=listbox][aria-label="${ring}"] [role=option]`;

interface Registry {
  models: { locked?: boolean }[];
}
interface Metrics {
  className: string;
  height: string;
  paddingLeft: string;
  paddingRight: string;
  paddingTop: string;
  paddingBottom: string;
  borderRadius: string;
  hit: number;
}

async function center(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`missing ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function main() {
  const registry = (await (await fetch(`${BASE}/voice-models`)).json()) as Registry;
  for (const model of registry.models) model.locked = false;
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1024, height: 760 } });
  await page.route("**/voice-models", (route) => route.fulfill({ json: registry }));
  await page.route("**/providers", (route) =>
    route.fulfill({ json: { providers: [{ id: "xai", label: "xAI", authKinds: ["api_key"], connected: true }] } }),
  );
  await page.routeWebSocket("**/ws", () => {});
  const checks: string[] = [];
  const check = (name: string, ok: boolean) => checks.push(`${ok ? "PASS" : "FAIL"} ${name}`);

  await page.goto(BASE);
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector(".dock .rp-orb");
  const orb = await center(page, ".dock .rp-orb");
  await page.mouse.move(orb.x, orb.y, { steps: 4 });
  const xai = await center(page, options("Providers"));
  await page.mouse.move(xai.x, xai.y, { steps: 6 });
  const model = await center(page, options("Models"));
  await page.mouse.move(model.x, model.y, { steps: 6 });
  await page.waitForSelector(options("Voices"));
  await page.waitForSelector(options("Reasoning effort"));
  await pause(600);

  const measured: Record<string, Metrics[]> = {};
  for (const ring of RINGS) {
    // A plain string, so the bundler adds no helpers to code that runs in the page.
    measured[ring] = await page.evaluate<Metrics[]>(`[...document.querySelectorAll(${JSON.stringify(options(ring))})]
      .filter((el) => Number(getComputedStyle(el).opacity) > 0.05)
      .map((el) => {
        const s = getComputedStyle(el);
        return { className: el.className, height: s.height, paddingLeft: s.paddingLeft, paddingRight: s.paddingRight,
          paddingTop: s.paddingTop, paddingBottom: s.paddingBottom, borderRadius: s.borderRadius,
          hit: parseFloat(getComputedStyle(el, "::before").height) };
      })`);
  }
  const key = (m: Metrics) => `${m.height} ${m.paddingTop} ${m.paddingRight} ${m.paddingBottom} ${m.paddingLeft} r${m.borderRadius}`;
  const reference = measured.Providers?.[0];
  for (const ring of RINGS) {
    const list = measured[ring] ?? [];
    check(`${ring}: ${list.length} visible options`, list.length > 0);
    check(`${ring}: all use .rp-option (${[...new Set(list.map((m) => m.className))].join(" | ")})`, list.every((m) => m.className === "rp-option"));
    const keys = [...new Set(list.map(key))];
    check(`${ring}: height/padding/radius ${keys.join(" | ")} equals providers' ${reference ? key(reference) : "?"}`, reference !== undefined && keys.length === 1 && keys[0] === key(reference));
    check(`${ring}: hit area ≥ 44px (${Math.min(...list.map((m) => m.hit))}px)`, list.every((m) => m.hit >= 44));
  }
  writeFileSync(`${OUT}/unified-metrics.json`, `${JSON.stringify(measured, null, 2)}\n`);

  for (const ring of RINGS) {
    const boxes = await page.locator(options(ring)).evaluateAll((els) =>
      els.filter((el) => Number(getComputedStyle(el).opacity) > 0.05).map((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      }),
    );
    const x0 = Math.min(...boxes.map((b) => b.x)) - 24;
    const y0 = Math.min(...boxes.map((b) => b.y)) - 24;
    const x1 = Math.max(...boxes.map((b) => b.x + b.w)) + 24;
    const y1 = Math.max(...boxes.map((b) => b.y + b.h)) + 24;
    await page.screenshot({ path: `${OUT}/unified-${ring === "Reasoning effort" ? "effort" : ring.toLowerCase()}.png`, clip: { x: x0, y: y0, width: x1 - x0, height: y1 - y0 } });
  }
  const c = orb;
  await page.screenshot({ path: `${OUT}/unified-all.png`, clip: { x: c.x - 340, y: c.y - 340, width: 680, height: 430 } });

  writeFileSync(`${OUT}/unified-checks.txt`, `${checks.join("\n")}\n`);
  console.log(checks.join("\n"));
  await browser.close();
  if (checks.some((line) => line.startsWith("FAIL"))) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
