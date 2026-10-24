/**
 * Renders the real web app against a scripted Symphony socket (no server, no
 * key) and saves screenshots comparable with the design mock.
 *
 *   pnpm --filter @osmo/web build && pnpm --filter @osmo/web preview --port 4173 &
 *   node apps/web/e2e/screenshots.ts [outDir]
 */
import { chromium, type WebSocketRoute } from "playwright-core";

const BASE = process.env.OSMO_URL ?? "http://127.0.0.1:4173";
const OUT = process.argv[2] ?? "/workspace/osmo-shots";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";

type Json = Record<string, unknown>;

const send = (ws: WebSocketRoute, event: Json) => ws.send(JSON.stringify(event));
const silence = (seconds: number) => Buffer.alloc(24000 * 2 * seconds);
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const TIP_CALCULATOR = `<!doctype html><html><body style="margin:0;background:#111;color:#eee;font:14px system-ui;padding:16px">
<h3 style="margin:0 0 12px">Tip calculator</h3>
<label>Bill <input id="b" type="number" value="84" style="width:90px"></label>
<label style="margin-left:12px">Tip % <input id="t" type="range" min="10" max="30" value="20"></label>
<p id="o" style="font-size:22px;margin:14px 0 0"></p>
<script>const f=()=>{const b=+document.getElementById('b').value,t=+document.getElementById('t').value;
document.getElementById('o').textContent='Tip $'+(b*t/100).toFixed(2)+' · Total $'+(b*(1+t/100)).toFixed(2)+' ('+t+'%)'};
document.querySelectorAll('input').forEach(i=>i.oninput=f);f();</script></body></html>`;

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const page = await browser.newPage({ viewport: { width: 1024, height: 640 }, deviceScaleFactor: 1 });
  let socket: WebSocketRoute | undefined;
  const received: string[] = [];
  await page.routeWebSocket(/\/ws$/, (ws) => {
    socket = ws;
    ws.onMessage((message) => {
      if (typeof message === "string") received.push(message);
    });
  });

  await page.goto(BASE);
  await page.screenshot({ path: `${OUT}/01-idle.png` });

  await page.click("button.orb");
  await page.waitForFunction(() => document.querySelector('.orb[data-connection="connected"]') !== null);
  const ws = socket;
  if (!ws) throw new Error("app never opened /ws");
  send(ws, { type: "state", state: "listening" });

  // Turn 1: weather card.
  send(ws, { type: "transcript.delta", role: "user", text: "What's the weather in Palo Alto this weekend?" });
  send(ws, { type: "state", state: "working", detail: "Checking weather · Palo Alto" });
  await pause(150);
  send(ws, {
    type: "widget.render",
    widget: { id: "w1", type: "card", props: { title: "Palo Alto · this weekend", body: "**72°** Sat · sunny\n\n**68°** Sun · partly cloudy" } },
  });
  send(ws, { type: "transcript.delta", role: "assistant", text: "Sunny Saturday, a little cooler on Sunday." });
  send(ws, { type: "state", state: "listening" });

  // Turn 2: options, while speaking.
  send(ws, { type: "transcript.delta", role: "user", text: "Find me three dinner spots near there." });
  send(ws, {
    type: "widget.render",
    widget: {
      id: "w2",
      type: "options",
      props: {
        prompt: "Pick a dinner spot",
        choices: [
          { id: "italian", label: "Italian, wood-fired", detail: "0.4 mi" },
          { id: "ramen", label: "Ramen counter", detail: "0.8 mi" },
          { id: "thai", label: "Thai, patio seating", detail: "1.1 mi" },
        ],
      },
    },
  });
  send(ws, { type: "transcript.delta", role: "assistant", text: "Here are three near there. The ramen place is the closest one open late" });
  ws.send(silence(8));
  await page.waitForSelector('.orb[data-state="speaking"]');
  await pause(400);
  await page.screenshot({ path: `${OUT}/02-speaking-options.png` });

  // Pick an option and confirm (chat-sdk QuestionWidget): it must go out as widget.action.
  await page.click("text=Ramen counter");
  await page.click(".chat-question button[type=submit]");
  await pause(100);
  const action = received.map((m) => JSON.parse(m) as Json).find((m) => m.type === "widget.action");
  if (!action || action.value !== "ramen") throw new Error(`widget.action not sent: ${JSON.stringify(action)}`);

  // Turn 3: barge-in, then a chart.
  ws.send(silence(8));
  await page.waitForSelector('.orb[data-state="speaking"]');
  send(ws, { type: "state", state: "listening", detail: "barge-in" });
  await page.waitForSelector('.orb[data-state="listening"]', { timeout: 1000 });
  send(ws, { type: "transcript.delta", role: "user", text: "Actually, show me Tesla's stock this month." });
  send(ws, { type: "state", state: "working", detail: "Fetching prices · TSLA" });
  const closes = [238.4, 241.1, 236.9, 244.2, 249.8, 247.3, 252.6, 258.1, 255.4, 261.9, 259.2, 264.7, 262.0, 268.3, 271.5];
  send(ws, {
    type: "widget.render",
    widget: {
      id: "w3",
      type: "chart",
      props: { kind: "line", series: [{ name: "TSLA", points: closes.map((y, i) => ({ x: `Oct ${i + 1}`, y })) }] },
    },
  });
  send(ws, { type: "transcript.delta", role: "assistant", text: "Tesla's up about fourteen percent this month." });
  send(ws, { type: "state", state: "listening" });
  await pause(500);
  await page.screenshot({ path: `${OUT}/03-barge-in-chart.png` });

  // Turn 4: sandboxed html.
  send(ws, { type: "transcript.delta", role: "user", text: "Build me a tip calculator." });
  send(ws, { type: "widget.render", widget: { id: "w4", type: "html", props: { html: TIP_CALCULATOR, title: "Tip calculator" } } });
  send(ws, { type: "transcript.delta", role: "assistant", text: "Here's a tip calculator. Drag the slider." });
  send(ws, { type: "state", state: "thinking" });
  await pause(700);
  await page.screenshot({ path: `${OUT}/04-html-widget.png` });

  // Fullscreen modal: same header bar with a close control; Esc closes it.
  await page.click('button[aria-label="Zoom in"]');
  await page.click('button[aria-label="Open fullscreen"]');
  await page.waitForSelector('[role="dialog"] iframe');
  await pause(300);
  await page.screenshot({ path: `${OUT}/05-html-modal.png` });
  await page.keyboard.press("Escape");
  await page.waitForSelector('[role="dialog"]', { state: "detached", timeout: 1000 });

  await browser.close();
  console.log(`saved screenshots to ${OUT}; widget.action=${JSON.stringify(action)}`);
}

await main();
