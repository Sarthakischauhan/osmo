/**
 * Locked providers in the radial ring and the "Connect provider" dialog.
 *
 *   OSMO_URL=http://127.0.0.1:8787 node --experimental-strip-types apps/web/e2e/provider-auth-shots.ts <outDir>
 *
 * `/voice-models` and `/providers` are served from fixtures (OpenAI and OpenRouter not connected) and
 * `POST /providers/openai/key` answers like a provider that rejects the key, so no real key is needed.
 * Keyboard only: Tab to the ring, Enter on a locked provider, Tab through the dialog, Escape to close.
 */
import { writeFileSync } from "node:fs";
import { type Page, chromium } from "playwright-core";
import { LOCKED_PROVIDERS, LOCKED_REGISTRY } from "../test/fixtures/mockRegistry.ts";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:8787";
const OUT = process.argv[2] ?? "/workspace/osmo-shots-auth";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const PROVIDERS = [
  { id: "xai", label: "xAI", authKinds: ["api_key"], connected: true },
  { id: "openai", label: "OpenAI", authKinds: ["api_key"], connected: false },
  { id: "openrouter", label: "OpenRouter", authKinds: ["api_key", "oauth"], connected: false },
];

async function orbCenter(page: Page) {
  const box = await page.locator(".dock .rp-orb").boundingBox();
  if (!box) throw new Error("no orb");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function main() {
  const browser = await chromium.launch({ executablePath: CHROME });
  const checks: string[] = [];
  const check = (name: string, ok: boolean) => checks.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  const context = await browser.newContext({ viewport: { width: 1024, height: 760 } });
  const page = await context.newPage();
  await page.route("**/voice-models", (route) =>
    route.fulfill({ json: { type: "models", models: LOCKED_REGISTRY, selection: { model: "grok-voice-latest", voice: "eve" } } }),
  );
  await page.route("**/providers", (route) => route.fulfill({ json: { providers: PROVIDERS } }));
  await page.route("**/providers/openai/key", (route) => route.fulfill({ status: 400, json: { detail: "OpenAI rejected this key" } }));
  await page.goto(BASE);
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector(".dock .rp-orb canvas.orb-canvas");

  // Locked provider in the ring.
  const c = await orbCenter(page);
  await page.mouse.move(c.x, c.y, { steps: 4 });
  await page.waitForSelector('[role=listbox][aria-label="Providers"]');
  const locked = page.locator('[role=option][data-locked="true"]');
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLImageElement>(".rp-logo")].every((img) => img.complete));
  const openai = page.locator('[role=option][aria-label="OpenAI · not connected"]');
  const box = await openai.boundingBox();
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 });
  await pause(450);
  await page.screenshot({ path: `${OUT}/auth-3-locked-provider-ring.png`, clip: { x: c.x - 320, y: c.y - 330, width: 640, height: 420 } });
  check(`locked providers are marked (${await locked.count()} visible)`, (await locked.count()) >= 1);
  check("hovering a locked provider keeps its models ring closed", (await page.locator('[role=listbox][aria-label="Models"]').count()) === 0);

  // Keyboard: focus the providers ring, move to OpenAI, Enter opens the dialog.
  await page.mouse.move(5, 5);
  await pause(600);
  await page.locator(".dock .rp-orb").focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  check(`Enter on locked OpenAI opens "${await dialog.getByRole("heading").textContent()}"`, (await dialog.getByRole("heading").textContent()) === "Connect OpenAI");
  const input = dialog.getByLabel("OpenAI API key");
  check("API key input is labelled", (await input.count()) === 1);
  await input.focus();
  await page.keyboard.type("sk-wrong");
  await page.keyboard.press("Enter");
  await dialog.getByText("OpenAI rejected this key").waitFor();
  check("a rejected key shows the server's message", true);
  await pause(300);
  await page.screenshot({ path: `${OUT}/auth-1-dialog-api-key.png` });
  for (let i = 0; i < 8; i++) await page.keyboard.press("Tab");
  check("focus stays trapped in the dialog", await page.evaluate(() => document.activeElement?.closest("[role=dialog]") !== null));
  await page.keyboard.press("Escape");
  await pause(300);
  check("Escape closes the dialog", (await dialog.count()) === 0);

  // OpenRouter: the OAuth tab, reached by keyboard.
  await page.mouse.move(c.x, c.y, { steps: 4 });
  await page.waitForSelector('[role=listbox][aria-label="Providers"]');
  await page.mouse.move(5, 5);
  await pause(600);
  await page.locator(".dock .rp-orb").focus();
  await page.keyboard.press("Tab");
  const providers = LOCKED_REGISTRY.map((m) => m.provider).filter((p, i, all) => all.indexOf(p) === i);
  for (let i = 0; i < providers.indexOf("openrouter"); i++) await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await dialog.waitFor();
  await dialog.getByRole("tab", { name: "API key" }).focus();
  await page.keyboard.press("ArrowRight");
  await pause(300);
  check("OAuth tab offers OpenRouter sign-in", (await dialog.getByRole("button", { name: "Continue with OpenRouter" }).count()) === 1);
  await page.screenshot({ path: `${OUT}/auth-2-dialog-oauth.png` });
  const noBorders = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[role=dialog], [role=dialog] *")].every((el) => {
      const style = getComputedStyle(el);
      return ["Top", "Right", "Bottom", "Left"].every((side) => style.getPropertyValue(`border-${side.toLowerCase()}-width`) === "0px" || style.getPropertyValue(`border-${side.toLowerCase()}-style`) === "none");
    }),
  );
  check("dialog has no borders", noBorders);
  check(`fixture locks ${LOCKED_PROVIDERS.join(", ")}`, true);
  await browser.close();
  writeFileSync(`${OUT}/provider-auth-checks.txt`, `${checks.join("\n")}\n`);
  console.log(checks.join("\n"));
}

void main();
