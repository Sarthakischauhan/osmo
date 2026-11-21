/**
 * Renders the real web app against a scripted Symphony socket (no server, no
 * key) and saves screenshots comparable with the design mock.
 *
 *   bun run --filter @osmo/web build && bun run --filter @osmo/web preview --port 4173 &
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

const TIP_CALCULATOR = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tip calculator</title>
<style>
:root{color-scheme:dark;font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#ededed;background:#0a0a0a}
*{box-sizing:border-box}body{margin:0;padding:24px;max-width:720px;margin-inline:auto}.stack{display:grid;gap:20px}
header{display:grid;gap:4px}h1{font-size:20px;line-height:28px;margin:0;font-weight:600}.muted{color:#a1a1a1;font-size:12px;margin:0}
.panel{border:1px solid rgba(255,255,255,.1);background:#111;padding:20px;display:grid;gap:20px;border-radius:4px}
.result{display:grid;gap:4px}.result strong{font-size:32px;line-height:40px;font-variant-numeric:tabular-nums;font-weight:600}.result span{color:#a1a1a1;font-size:12px}
.controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}label{display:grid;gap:8px;font-size:12px;color:#a1a1a1}input,select{width:100%;min-height:40px;padding:8px 10px;border:1px solid rgba(255,255,255,.14);border-radius:3px;background:#171717;color:#ededed;font:inherit;font-variant-numeric:tabular-nums}
input:focus-visible,select:focus-visible{outline:2px solid #ededed;outline-offset:2px}.range-row{display:flex;align-items:center;gap:12px}.range-row input{padding:0;accent-color:#ededed}.range-row output{min-width:42px;text-align:right;color:#ededed;font-variant-numeric:tabular-nums}
.split{border-top:1px solid rgba(255,255,255,.1);padding-top:16px;display:flex;justify-content:space-between;gap:12px;color:#a1a1a1;font-size:12px}.split strong{color:#ededed;font-variant-numeric:tabular-nums}
@media(max-width:480px){body{padding:16px}.panel{padding:16px}.controls{grid-template-columns:1fr}.result strong{font-size:28px}}
</style><body><main class="stack"><header><h1>Tip calculator</h1><p class="muted">Split the bill and choose a tip.</p></header><section class="panel" aria-label="Calculator">
<div class="result"><span>Total per person</span><strong id="per-person" aria-live="polite">$0.00</strong><span id="summary">Including tip</span></div>
<div class="controls"><label for="bill">Bill amount<input id="bill" type="number" inputmode="decimal" min="0" step="0.01" value="84"></label><label for="people">People<select id="people"><option>1</option><option>2</option><option>3</option><option>4</option></select></label>
<label for="tip">Tip percentage<span class="range-row"><input id="tip" type="range" min="0" max="30" step="1" value="20"><output id="tip-value" for="tip">20%</output></span></label><div class="split"><span>Tip amount</span><strong id="tip-total">$0.00</strong></div></div></section></main>
<script>const bill=document.querySelector('#bill'),people=document.querySelector('#people'),tip=document.querySelector('#tip');function update(){const amount=Number(bill.value),count=Number(people.value),rate=Number(tip.value);document.querySelector('#tip-value').value=rate+'%';document.querySelector('#tip-value').textContent=rate+'%';if(!Number.isFinite(amount)||amount<0){document.querySelector('#per-person').textContent='—';document.querySelector('#summary').textContent='Enter a valid bill amount';document.querySelector('#tip-total').textContent='—';return}const gratuity=amount*rate/100,total=amount+gratuity;document.querySelector('#per-person').textContent=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(total/count);document.querySelector('#summary').textContent=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(total)+' total · '+count+' '+(count===1?'person':'people');document.querySelector('#tip-total').textContent=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(gratuity)}document.querySelectorAll('input,select').forEach(control=>control.addEventListener('input',update));update();</script></body></html>`;

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
