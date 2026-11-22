// Browser integration test: API-key-connected users can still start subscription sign-in.
// Run against the Vite dev server: bun apps/web/e2e/agent-settings.mjs
import { chromium } from "playwright-core";
import assert from "node:assert/strict";

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
try {
  const page = await browser.newPage();
  let signedIn = false;
  let started = false;
  let selected = null;
  const provider = () => ({ id: "openai", label: "OpenAI", authKinds: ["api_key"], connected: true, capabilities: ["Voice"], subscription: { label: "Sign in with ChatGPT", connected: signedIn, capabilities: ["Background agents"] } });
  const model = () => ({ chosen: selected, effective: selected || "openai:gpt-5", source: selected ? "chosen" : "automatic", options: [{ id: "openai:gpt-5", label: "gpt-5", provider: "openai", providerLabel: "OpenAI", via: signedIn ? "subscription" : "api_key" }], notes: [] });
  await page.route("**/providers**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/providers") return route.fulfill({ json: { providers: [provider()] } });
    if (path === "/providers/openai/subscription/login" && route.request().method() === "POST") {
      started = true;
      return route.fulfill({ json: { loginId: "test", kind: "browser", title: "Sign in", instructions: "Approve sign-in", url: "https://example.com", userCode: "", pasteHint: "Paste redirect URL" } });
    }
    if (path === "/providers/openai/subscription/login/test/paste") {
      signedIn = true;
      return route.fulfill({ json: { status: "connected", error: "" } });
    }
    if (path === "/providers/openai/subscription/login/test") return route.fulfill({ json: { status: signedIn ? "connected" : "pending", error: "" } });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.route("**/voice-models", (route) => route.fulfill({ json: { type: "models", models: [], selection: { model: "grok-voice-latest", voice: "eve", effort: "high" } } }));
  await page.route("**/agent-model", async (route) => {
    if (route.request().method() === "PUT") selected = route.request().postDataJSON().model;
    return route.fulfill({ json: model() });
  });
  await page.goto(process.env.OSMO_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("heading", { name: "Settings" }).waitFor();
  await page.getByRole("heading", { name: "Computer support" }).waitFor();
  await page.getByRole("heading", { name: "Background agent" }).waitFor();
  await page.getByText("API key connected", { exact: true }).click();
  await page.getByText("API key: connected").waitFor();
  await page.getByRole("button", { name: "Sign in with ChatGPT" }).click();
  assert(started, "OAuth offered even with an API key");
  await page.getByLabel("Paste from the sign-in page").fill("http://localhost:1455/auth/callback?code=test");
  await page.getByRole("button", { name: "Finish sign-in" }).click();
  await page.getByText("Signed in with your OpenAI subscription.", { exact: false }).waitFor({ timeout: 10000 });
  const picker = page.getByRole("button", { name: "Background agent model" });
  await picker.click();
  await page.getByRole("option", { name: /gpt-5/ }).click();
  await page.getByText(/Next task: openai:gpt-5 \(chosen\)/).waitFor();
  assert.equal(selected, "openai:gpt-5");
  await picker.click();
  await page.getByRole("option", { name: /Automatic/ }).click();
  await page.getByText(/Next task: openai:gpt-5 \(automatic\)/).waitFor();
  assert.equal(selected, null);
  await picker.click();
  await page.getByRole("option", { name: /gpt-5/ }).click();
  await page.getByText(/Next task: openai:gpt-5 \(chosen\)/).waitFor();
  console.log("Agent settings: API key + OAuth sign-in + model selection passed");
} finally {
  await browser.close();
}
