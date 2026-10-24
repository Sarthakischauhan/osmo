/**
 * Screenshots of the model ring against a running Symphony (its real registry):
 * default short labels, then the full model id tooltip on mouse hover and on keyboard focus.
 * Usage: OSMO_URL=http://127.0.0.1:8787 npx tsx apps/web/e2e/model-ring-shots.ts <outDir>
 */
import { writeFileSync } from "node:fs";
import { type Page, chromium } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:8787";
const OUT = process.argv[2] ?? ".";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function center(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`missing ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function main() {
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1024, height: 760 } });
  const checks: string[] = [];
  const check = (name: string, ok: boolean) => checks.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  const shot = async (name: string) => {
    await pause(450);
    const c = await center(page, ".dock .rp-orb");
    await page.screenshot({ path: `${OUT}/${name}.png`, clip: { x: c.x - 320, y: c.y - 330, width: 640, height: 420 } });
  };
  const models = '[role=listbox][aria-label="Models"] [role=option]';

  await page.goto(BASE);
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector(".dock .rp-orb");
  const orb = await center(page, ".dock .rp-orb");
  await page.mouse.move(orb.x, orb.y, { steps: 4 });
  const xai = await center(page, '[role=listbox][aria-label="Providers"] [role=option]');
  await page.mouse.move(xai.x, xai.y, { steps: 6 });
  await page.waitForSelector(models);
  const texts = await page.locator(models).allTextContents();
  check(`model ring shows distinct short labels (${texts.join(" | ")})`, texts.join("|") === "Latest|3.0");
  // The text's own box must sit inside the pill (the ::before hit area is excluded).
  const clipped = await page.locator(models).evaluateAll((els) =>
    els.some((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      const text = range.getBoundingClientRect();
      const pill = el.getBoundingClientRect();
      return text.left < pill.left || text.right > pill.right || getComputedStyle(el).textOverflow === "ellipsis";
    }),
  );
  check("model labels are not truncated", !clipped);
  await page.mouse.move(orb.x, orb.y - 120, { steps: 2 });
  await page.mouse.move(xai.x, xai.y, { steps: 6 });
  await shot("model-ring-default");

  const boxes = await page.locator(models).evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON()));
  const target = boxes[1];
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 6 });
  await page.waitForSelector(".rp-tip");
  const hoverTip = await page.locator(".rp-tip").textContent();
  check(`hover shows the full id (${hoverTip})`, hoverTip === "grok-voice-3.0");
  const tipStyle = await page.locator(".rp-tip").evaluate((el) => {
    const s = getComputedStyle(el);
    return `${s.borderTopWidth} ${s.backgroundColor}`;
  });
  check(`tooltip has no border, white-alpha fill (${tipStyle})`, tipStyle.startsWith("0px rgba(255, 255, 255"));
  await shot("model-ring-hover-id");

  // Keyboard: reload, Tab onto the orb (focus-visible opens the dial), then walk to the model ring.
  await page.mouse.move(5, 5);
  await page.reload();
  await page.waitForSelector(".dock .rp-orb");
  let focused = "";
  for (let i = 0; i < 12 && !focused; i++) {
    await page.keyboard.press("Tab");
    await pause(150);
    focused = await page.evaluate(() => (document.activeElement?.getAttribute("aria-label") === "Models" ? "models" : ""));
  }
  if (focused) {
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowLeft");
  }
  const keyTip = await page.locator(".rp-tip").textContent({ timeout: 2000 }).catch(() => null);
  check(`keyboard focus shows the full id (${keyTip})`, keyTip !== null && keyTip.startsWith("grok-voice-"));
  await shot("model-ring-focus-id");

  writeFileSync(`${OUT}/model-ring-checks.txt`, `${checks.join("\n")}\n`);
  console.log(checks.join("\n"));
  await browser.close();
  if (checks.some((c) => c.startsWith("FAIL"))) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
