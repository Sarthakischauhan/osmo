/**
 * Two widgets rendered in a row, then an update for the second one, against a scripted socket
 * (no server, no key). The second is an html widget zoomed to 125% before the update arrives together
 * with the assistant's line. Exits non-zero if the update touched the first widget, missed the second,
 * or remounted it (its zoom state reset), which is what index-keyed widget rendering did.
 *
 *   bun run --filter @osmo/web build && bun run --filter @osmo/web preview --port 4173 &
 *   node --experimental-strip-types apps/web/e2e/widget-id-shots.ts <outDir> [name]
 */
import { chromium, type WebSocketRoute } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:4173";
const OUT = process.argv[2] ?? "/workspace/osmo-shots-widget-id";
const NAME = process.argv[3] ?? "after";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";

const send = (ws: WebSocketRoute, event: Record<string, unknown>) => ws.send(JSON.stringify(event));
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const page = await browser.newPage({ viewport: { width: 1024, height: 760 }, deviceScaleFactor: 1 });
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws$/, (ws) => {
    sockets.push(ws);
  });
  await page.goto(BASE);
  await page.click("button.orb");
  await page.waitForFunction(() => document.querySelector('.orb[data-connection="connected"]') !== null);
  const ws = sockets[0];
  if (!ws) throw new Error("app never opened /ws");

  send(ws, { type: "state", state: "listening" });
  send(ws, { type: "transcript.delta", role: "user", text: "Weather in Palo Alto, and how is TSLA doing?" });
  send(ws, { type: "widget.render", widget: { id: "w1-aaaa", type: "card", props: { title: "Palo Alto weather", body: "**72°** sunny" } } });
  const ticker = (price: string) =>
    `<html><body style="font:600 28px system-ui;padding:16px;margin:0">TSLA <span style="color:#16a34a">${price}</span></body></html>`;
  send(ws, { type: "widget.render", widget: { id: "w2-bbbb", type: "html", props: { title: "TSLA", html: ticker("loading…") } } });
  await page.waitForSelector('[aria-label="TSLA controls"]');
  await page.click('[aria-label="TSLA controls"] [aria-label="Zoom in"]');
  send(ws, { type: "transcript.delta", role: "assistant", text: "Here is the weather; pulling the price." });
  send(ws, { type: "widget.update", id: "w2-bbbb", patch: { title: "TSLA · $251.40", html: ticker("$251.40 +2.1%") } });
  await pause(800);
  await page.screenshot({ path: `${OUT}/${NAME}.png` });

  const widgets = await page.locator(".chat-widget").allInnerTexts();
  console.log(JSON.stringify(widgets));
  const first = widgets[0] ?? "";
  const second = widgets[1] ?? "";
  const ok =
    widgets.length === 2 &&
    first.includes("Palo Alto weather") &&
    first.includes("72°") &&
    second.includes("TSLA · $251.40") &&
    second.includes("125%");
  console.log(ok ? "PASS second widget updated in place (zoom kept), first unchanged" : "FAIL update did not land on its own widget instance");
  await browser.close();
  if (!ok) process.exit(1);
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
