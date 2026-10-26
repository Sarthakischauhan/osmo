/**
 * The radial picker around the real orb in the main app, plus a mid-session model and voice switch.
 *
 *   OSMO_URL=http://127.0.0.1:8787 node --experimental-strip-types apps/web/e2e/main-picker-shots.ts <outDir>
 *
 * Part 1 serves `/voice-models` from the dev mock registry (several providers, so R1 shows logos) and
 * opens no socket. Part 2 talks to a running Symphony (its upstream may be a fake realtime server): it
 * starts a session, switches model then voice and effort from the picker, and logs how many browser
 * WebSockets were opened, which must stay 1.
 */
import { writeFileSync } from "node:fs";
import { type Page, chromium } from "playwright-core";
import { MOCK_REGISTRY } from "../test/fixtures/mockRegistry.ts";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:8787";
const OUT = process.argv[2] ?? "/workspace/osmo-shots-radial2";
const OUT_PHONE = process.env.PHONE_OUT ?? OUT;
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function orbCenter(page: Page) {
  const box = await page.locator(".dock .rp-orb").boundingBox();
  if (!box) throw new Error("no orb");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function hoverOption(page: Page, ring: string, label: string) {
  const box = await page.locator(`[role=listbox][aria-label="${ring}"] [role=option][aria-label^="${label}"]`).boundingBox();
  if (!box) throw new Error(`no ${ring} option ${label}`);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const checks: string[] = [];
  const check = (name: string, ok: boolean) => checks.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  const shot = async (page: Page, name: string, full = false) => {
    await pause(450);
    const c = await orbCenter(page);
    await page.screenshot({ path: `${OUT}/${name}.png`, ...(full ? {} : { clip: { x: c.x - 320, y: c.y - 330, width: 640, height: 420 } }) });
  };

  // ---------------------------------------------------------------- 1: picker around the orb, mock registry
  {
    const context = await browser.newContext({ viewport: { width: 1024, height: 760 } });
    const page = await context.newPage();
    await page.route("**/voice-models", (route) =>
      route.fulfill({ json: { type: "models", models: MOCK_REGISTRY, selection: { model: "grok-voice-latest", voice: "eve" } } }),
    );
    await page.goto(BASE);
    await page.evaluate(() => window.localStorage.clear());
    await page.reload();
    await page.waitForSelector(".dock .rp-orb canvas.orb-canvas");
    await shot(page, "main-1-orb-closed", true);
    check("orb is the canvas GlassOrb inside the picker", (await page.locator(".dock .rp-orb canvas.orb-canvas").count()) === 1);

    const c = await orbCenter(page);
    await page.mouse.move(c.x, c.y, { steps: 4 });
    await page.waitForSelector('[role=listbox][aria-label="Providers"]');
    await page.waitForFunction(() => [...document.querySelectorAll<HTMLImageElement>(".rp-logo")].every((img) => img.complete));
    await shot(page, "main-2-r1-provider-logos");
    const logos = await page.locator(".rp-logo").evaluateAll((imgs) => imgs.map((img) => (img as HTMLImageElement).naturalWidth > 0));
    check(`R1 logos loaded (${logos.filter(Boolean).length} loaded)`, logos.length > 0 && logos.every(Boolean));
    const sources = await page.locator(".rp-logo").evaluateAll((imgs) => imgs.map((img) => img.getAttribute("data-source")));
    check(`R1 logos come from models.dev at runtime first (${sources.join(", ")})`, sources.length > 0 && sources.every((s) => s === "runtime"));

    await hoverOption(page, "Providers", "OpenAI");
    await shot(page, "main-3-r2-models");
    await hoverOption(page, "Providers", "Anthropic");
    await hoverOption(page, "Models", "claude-voice-1");
    await shot(page, "main-4-r3-voices-effort");
    check("R3 voices and effort open", (await page.locator("[role=listbox]").count()) === 4);

    // Scroll R1 to its far end: providers models.dev has no logo for fall back to a monogram.
    await hoverOption(page, "Providers", "Anthropic");
    for (let i = 0; i < 12; i++) {
      await page.mouse.wheel(0, 20);
      await pause(16);
    }
    await pause(700);
    await shot(page, "main-2b-r1-scrolled-monograms");
    check("providers without a models.dev logo show a monogram", (await page.locator(".rp-monogram").count()) > 0);
    await context.close();
  }

  // ---------------------------------------------------------------- 2: live switch, one browser socket
  {
    const context = await browser.newContext({ viewport: { width: 1024, height: 760 } });
    const page = await context.newPage();
    const sockets: string[] = [];
    page.on("websocket", (ws) => sockets.push(ws.url()));
    await page.goto(BASE);
    await page.evaluate(() => window.localStorage.clear());
    await page.reload();
    await page.waitForSelector(".dock .rp-orb");
    const stored = () => page.evaluate(() => window.localStorage.getItem("osmo.voice-pick.v2"));
    await page.locator(".dock .rp-orb").click();
    await page.waitForSelector("text=Nice to meet you, Ada", { timeout: 8000 });
    check(`session.start's voice.configured saved the applied pick (${await stored()})`, (await stored())?.includes("grok-voice-latest") === true);
    await page.mouse.move(10, 10);
    await pause(500);

    const c = await orbCenter(page);
    await page.mouse.move(c.x, c.y, { steps: 4 });
    await hoverOption(page, "Providers", "xAI");
    await page.locator('[role=listbox][aria-label="Models"] [role=option][aria-label="grok-voice-3.0"]').click();
    await page.waitForFunction(() => document.querySelector(".rp-summary")?.textContent?.includes("grok-voice-3.0"));
    await pause(600);
    await hoverOption(page, "Models", "grok-voice-3.0");
    await page.locator('[role=listbox][aria-label="Voices"] [role=option][aria-label^="Sal"]').click();
    await page.locator('[role=listbox][aria-label="Reasoning effort"] [role=option][aria-label="Reasoning effort: none"]').click();
    await pause(600);
    await shot(page, "main-5-after-live-switch");
    await page.mouse.move(10, 10);
    await pause(500);
    await shot(page, "main-6-session-still-connected", true);
    const summary = (await page.locator(".rp-summary").textContent()) ?? "";
    check(`summary follows the switch (${summary})`, summary.includes("grok-voice-3.0") && summary.includes("Sal") && summary.includes("none"));
    check("End button still shown (session connected)", (await page.locator("button.end").count()) === 1);
    check(`localStorage holds the confirmed pick (${await stored()})`, (await stored()) === JSON.stringify({ model: "grok-voice-3.0", voice: "sal", effort: "none" }));
    check(`browser WebSocket opened exactly once (${sockets.length})`, sockets.length === 1);
    writeFileSync(`${OUT}/ws-open-count.log`, `browser WebSocket opens: ${sockets.length}\n${sockets.join("\n")}\n`);
    await context.close();
  }

  // ---------------------------------------------------------------- 2b: models.dev unreachable, bundled fallbacks
  {
    const context = await browser.newContext({ viewport: { width: 1024, height: 760 } });
    const page = await context.newPage();
    await page.route("https://models.dev/**", (route) => route.abort());
    await page.route("**/voice-models", (route) =>
      route.fulfill({ json: { type: "models", models: MOCK_REGISTRY, selection: { model: "grok-voice-latest", voice: "eve" } } }),
    );
    await page.goto(BASE);
    await page.waitForSelector(".dock .rp-orb canvas.orb-canvas");
    const c = await orbCenter(page);
    await page.mouse.move(c.x, c.y, { steps: 4 });
    await page.waitForSelector(".rp-logo");
    await pause(500);
    const sources = await page.locator(".rp-logo").evaluateAll((imgs) =>
      imgs.map((img) => `${img.getAttribute("data-source")}:${(img as HTMLImageElement).naturalWidth > 0}`),
    );
    await shot(page, "main-2c-r1-bundled-fallback");
    check(`models.dev blocked: bundled logos used (${sources.join(", ")})`, sources.length > 0 && sources.every((s) => s === "bundled:true"));
    await context.close();
  }

  // ---------------------------------------------------------------- 3: phone, a tap on the orb must not open the dial
  {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
    const page = await context.newPage();
    await page.route("**/voice-models", (route) =>
      route.fulfill({ json: { type: "models", models: MOCK_REGISTRY, selection: { model: "grok-voice-latest", voice: "eve" } } }),
    );
    await page.goto(BASE);
    await page.waitForSelector(".dock .rp-orb canvas.orb-canvas");
    await page.tap(".dock .rp-orb");
    await pause(900);
    const open = await page.locator(".dock .rp").getAttribute("data-open");
    const rings = await page.locator("[role=listbox]").count();
    await page.screenshot({ path: `${OUT_PHONE}/phone-tap-no-dial.png` });
    check(`phone tap on the orb leaves the dial closed (data-open=${open}, rings=${rings})`, open === "false" && rings === 0);
    check("phone tap still activates the orb (session starting/connected)", (await page.locator("button.end").count()) === 1);
    await context.close();
  }

  await browser.close();
  writeFileSync(`${OUT}/main-picker-checks.txt`, `${checks.join("\n")}\n`);
  console.log(checks.join("\n"));
}

void main();
