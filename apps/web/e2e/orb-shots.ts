/**
 * Orb state and widget-update screenshots against a scripted socket (no server, no key).
 *
 *   pnpm --filter @osmo/web build && pnpm --filter @osmo/web preview --port 4173 &
 *   node --experimental-strip-types apps/web/e2e/orb-shots.ts <outDir> [orb|update] [prefix]
 */
import { chromium, type Page, type WebSocketRoute } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:4173";
const OUT = process.argv[2] ?? "/workspace/osmo-shots-orb";
const MODE = process.argv[3] ?? "orb";
const PREFIX = process.argv[4] ?? "";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";

const send = (ws: WebSocketRoute, event: Record<string, unknown>) => ws.send(JSON.stringify(event));
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function connect(page: Page, sockets: WebSocketRoute[]): Promise<WebSocketRoute> {
  const count = sockets.length;
  await page.click("button.orb");
  await page.waitForFunction(() => document.querySelector('.orb[data-connection="connected"]') !== null);
  const ws = sockets[count];
  if (!ws) throw new Error("app never opened /ws");
  return ws;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const page = await browser.newPage({ viewport: { width: 1024, height: 640 }, deviceScaleFactor: 1 });
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws$/, (ws) => {
    sockets.push(ws);
  });
  await page.goto(BASE);

  if (MODE === "orb") {
    const orb = async (name: string) => {
      await pause(900);
      const box = await page.locator(".dock").boundingBox();
      if (!box) throw new Error("no dock");
      await page.screenshot({ path: `${OUT}/${name}.png`, clip: { x: box.x + box.width / 2 - 200, y: box.y - 40, width: 400, height: box.height + 60 } });
    };
    await orb("orb-1-idle-amber");
    const ws = await connect(page, sockets);
    send(ws, { type: "state", state: "listening" });
    await orb("orb-2-waiting-listening-amber");
    send(ws, { type: "state", state: "thinking" });
    await orb("orb-3-thinking");
    send(ws, { type: "state", state: "speaking" });
    await pause(400);
    await orb("orb-4-speaking");
  } else {
    // Session 1 shows a weather card as w1, then the user ends the call; the feed stays on screen.
    const first = await connect(page, sockets);
    send(first, { type: "state", state: "listening" });
    send(first, { type: "transcript.delta", role: "user", text: "Weather in Palo Alto?" });
    send(first, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "Palo Alto weather", body: "**72°** sunny" } } });
    send(first, { type: "transcript.delta", role: "assistant", text: "Sunny and 72." });
    await pause(300);
    await page.click("button.end");
    await page.waitForFunction(() => document.querySelector('.orb[data-connection="disconnected"]') !== null);

    // Session 2 (older server: counter restarts) renders a new card with the same id, then updates it.
    const second = await connect(page, sockets);
    send(second, { type: "state", state: "listening" });
    send(second, { type: "transcript.delta", role: "user", text: "And how is TSLA doing?" });
    send(second, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "TSLA", body: "Loading…" } } });
    send(second, { type: "transcript.delta", role: "assistant", text: "Pulling the latest price." });
    await pause(200);
    send(second, { type: "widget.update", id: "w1", patch: { title: "TSLA · $251.40", body: "**+2.1%** today" } });
    await pause(500);
    await page.screenshot({ path: `${OUT}/${PREFIX}widget-update.png` });
  }
  await browser.close();
  console.log(`saved ${MODE} screenshots to ${OUT}`);
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
