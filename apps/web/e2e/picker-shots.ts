/**
 * Radial picker screenshots and a trackpad-scroll video against a scripted socket (no server, no key).
 *
 *   pnpm --filter @osmo/web build && pnpm --filter @osmo/web preview --port 4173 &
 *   node --experimental-strip-types apps/web/e2e/picker-shots.ts <outDir>
 *
 * The `voice.configured` event for the voice-command shot is simulated here, as Symphony would
 * send it after the model calls `set_voice`; previews answer with a synthetic 2 s tone.
 */
import { type Browser, type Page, type WebSocketRoute, chromium } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:4173";
const OUT = process.argv[2] ?? "/workspace/osmo-shots-radial";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const SERVER_MODELS = [
  {
    id: "grok-voice-latest",
    provider: "xai",
    label: "Grok Voice",
    capabilities: { tools: true, interrupt: true, customVoices: false },
    voices: [
      { id: "eve", label: "Eve", traits: ["warm", "bright", "upbeat"] },
      { id: "ara", label: "Ara", traits: ["calm", "soft", "friendly"] },
      { id: "sal", label: "Sal", traits: ["deep", "smooth", "steady"] },
    ],
    defaultVoice: "eve",
  },
];

function toneBase64(seconds: number): string {
  const rate = 24000;
  const buffer = Buffer.alloc(rate * seconds * 2);
  for (let i = 0; i < rate * seconds; i++) buffer.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 180 * i) / rate) * 0.2 * 32767), i * 2);
  return buffer.toString("base64");
}

interface Harness {
  page: Page;
  sockets: WebSocketRoute[];
  center: { x: number; y: number };
}

async function openPage(browser: Browser, video?: string): Promise<Harness & { close: () => Promise<void> }> {
  const context = await browser.newContext({
    viewport: { width: 1024, height: 720 },
    deviceScaleFactor: 1,
    ...(video ? { recordVideo: { dir: video, size: { width: 1024, height: 720 } } } : {}),
  });
  const page = await context.newPage();
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws$/, (ws) => {
    sockets.push(ws);
    ws.send(JSON.stringify({ type: "models", models: SERVER_MODELS, selection: { model: "grok-voice-latest", voice: "eve" } }));
    ws.onMessage((message) => {
      if (typeof message !== "string") return;
      const event = JSON.parse(message) as { type: string; requestId?: string; model?: string; voice?: string; effort?: string };
      if (event.type === "session.start") ws.send(JSON.stringify({ type: "state", state: "listening" }));
      if (event.type === "session.configure")
        ws.send(JSON.stringify({ type: "voice.configured", selection: { model: event.model, voice: event.voice, effort: event.effort }, source: "client" }));
      if (event.type === "voice.preview")
        ws.send(JSON.stringify({ type: "voice.preview", requestId: event.requestId, model: event.model, voice: event.voice, audio: toneBase64(2) }));
    });
  });
  await page.goto(`${BASE}/dev/picker`);
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  const box = await page.locator(".rp").boundingBox();
  if (!box) throw new Error("no picker");
  return { page, sockets, center: { x: box.x + 300, y: box.y + 300 }, close: () => context.close() };
}

async function hoverOption(page: Page, ring: string, label: string) {
  const box = await page.locator(`[role=listbox][aria-label="${ring}"] [role=option][aria-label^="${label}"]`).boundingBox();
  if (!box) throw new Error(`no ${ring} option ${label}`);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 });
}

async function shot(h: Harness, name: string, wait = 450) {
  await pause(wait);
  await h.page.screenshot({ path: `${OUT}/${name}.png`, clip: { x: h.center.x - 310, y: h.center.y - 310, width: 620, height: 440 } });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const h = await openPage(browser);
  const { page, center } = h;
  const checks: string[] = [];
  const check = (name: string, ok: boolean) => checks.push(`${ok ? "PASS" : "FAIL"} ${name}`);

  // Connect to the scripted Symphony so commits and previews go over the socket.
  await page.getByRole("button", { name: "Connect to Symphony" }).click();
  await page.waitForSelector("text=Disconnect");

  await page.mouse.move(center.x, center.y, { steps: 4 });
  await shot(h, "radial-1-r1-providers");
  check("orb hover opens R1", (await page.locator("[role=listbox]").count()) === 1);

  await hoverOption(page, "Providers", "OpenAI");
  await shot(h, "radial-2-r2-models");
  check("provider hover opens R2", (await page.locator("[role=listbox]").count()) === 2);

  await hoverOption(page, "Providers", "Anthropic");
  await hoverOption(page, "Models", "claude-voice-1");
  await shot(h, "radial-3-r3-voices-effort");
  check("model with efforts shows voices + effort", (await page.locator("[role=listbox]").count()) === 4);

  await hoverOption(page, "Providers", "xAI");
  await hoverOption(page, "Models", "grok-voice-latest");
  await shot(h, "radial-4-r3-no-effort");
  check("no reasoningEffort hides the effort side", (await page.locator('[role=listbox][aria-label="Reasoning effort"]').count()) === 0);

  // Preview: hover a voice ~400 ms, the scripted server answers, the pill shows it playing.
  await hoverOption(page, "Voices", "Ara");
  await page.waitForSelector(".rp-playing", { timeout: 3000 });
  await shot(h, "radial-6-voice-preview-playing", 100);
  check("hovered voice previews", true);

  // Commit a voice: onChange fires once and the summary updates.
  const before = await page.locator(".picker-commits li").count();
  await page.locator('[role=listbox][aria-label="Voices"] [role=option][aria-label^="Sal"]').click();
  await pause(100);
  check("onChange fires exactly once per commit", (await page.locator(".picker-commits li").count()) === before + 1);
  check("summary updates", (await page.locator(".rp-summary").innerText()).includes("Sal"));

  // Leave the dial: closes after the 250 ms grace.
  await page.mouse.move(10, 10, { steps: 2 });
  await pause(150);
  const stillOpen = (await page.locator("[role=listbox]").count()) > 0;
  await pause(250);
  check("closes ~250 ms after leaving", stillOpen && (await page.locator("[role=listbox]").count()) === 0);

  // Voice command: Symphony's set_voice -> voice.configured (simulated here).
  const ws = h.sockets[0];
  if (!ws) throw new Error("no socket");
  ws.send(JSON.stringify({ type: "voice.configured", selection: { model: "grok-voice-latest", voice: "ara" }, source: "tool" }));
  await pause(200);
  check("voice command updates summary", (await page.locator(".rp-summary").innerText()).includes("Ara"));
  await shot(h, "radial-7-summary-after-voice-command", 100);

  // Persistence: reload restores the pick.
  await page.reload();
  check("pick persists across reload", (await page.locator(".rp-summary").innerText()).includes("Ara"));

  // Keyboard: focus the orb, Tab into rings, arrows, Enter commits, Escape returns focus.
  await page.locator(".rp-orb").focus();
  check("orb focus opens R1", (await page.locator(".rp-orb").getAttribute("aria-expanded")) === "true");
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  const commitsBefore = await page.locator(".picker-commits li").count();
  await page.keyboard.press("Enter");
  await pause(100);
  check("keyboard Enter commits", (await page.locator(".picker-commits li").count()) === commitsBefore + 1);
  check("keyboard picked an OpenAI model", (await page.locator(".rp-summary").innerText()).startsWith("OpenAI"));
  await page.keyboard.press("Escape");
  check("Escape closes and focuses orb", (await page.evaluate(() => document.activeElement?.className)) === "rp-orb");
  await h.close();

  // R2 trackpad scroll, recorded.
  const v = await openPage(browser, `${OUT}/video`);
  await v.page.mouse.move(v.center.x, v.center.y, { steps: 4 });
  await pause(300);
  await hoverOption(v.page, "Providers", "OpenAI");
  await pause(500);
  // Straight out from the OpenAI provider (120°) into R2, so the pointer never crosses another provider.
  const rad = (120 * Math.PI) / 180;
  const r2 = { x: v.center.x + 160 * Math.cos(rad), y: v.center.y - 160 * Math.sin(rad) };
  await v.page.mouse.move(r2.x, r2.y, { steps: 8 });
  await pause(500);
  // Close R3 opened by the hover? Scroll anyway: R3 must not switch until R2 settles.
  let r3Switched = false;
  const r3Before = await v.page.locator('[role=listbox][aria-label="Voices"]').count();
  for (let i = 0; i < 24; i++) {
    await v.page.mouse.wheel(0, 9);
    await pause(16);
    if (i > 2 && (await v.page.locator('[role=listbox][aria-label="Voices"]').count()) !== r3Before) r3Switched = true;
  }
  await pause(260);
  checks.push(`${r3Switched ? "FAIL" : "PASS"} R2 scroll never opens/switches R3 mid-scroll`);
  await shot(v, "radial-5-r2-scrolled-more-marker", 0);
  check("… marker shows both ends mid-list", (await v.page.locator(".rp-more").count()) >= 1);
  await pause(600);
  for (let i = 0; i < 30; i++) {
    await v.page.mouse.wheel(0, 10);
    await pause(16);
  }
  await pause(900);
  for (let i = 0; i < 30; i++) {
    await v.page.mouse.wheel(0, -12);
    await pause(16);
  }
  await pause(900);
  const video = v.page.video();
  await v.close();
  if (video) console.log(`VIDEO ${await video.path()}`);
  await browser.close();
  console.log(checks.join("\n"));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
