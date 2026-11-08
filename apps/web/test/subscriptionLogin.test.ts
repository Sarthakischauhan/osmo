import { describe, expect, it } from "vitest";
import { providerInfoSchema } from "../src/ConnectProvider";
import { SignInError, subscriptionLogin } from "../src/subscriptionLogin";

type Call = { url: string; init?: RequestInit };

function fakeFetch(responses: Record<string, [number, unknown]>) {
  const calls: Call[] = [];
  const fetcher = async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = `${init?.method ?? "GET"} ${url}`;
    const [status, body] = responses[key] ?? [404, { detail: "not found" }];
    return new Response(body === undefined ? null : JSON.stringify(body), { status });
  };
  return { calls, fetcher };
}

describe("subscription sign-in client", () => {
  it("starts a device login and reads its state", async () => {
    const { calls, fetcher } = fakeFetch({
      "POST /providers/xai/subscription/login": [200, { loginId: "l1", kind: "device", title: "Sign in with xAI", instructions: "Enter the code", url: "https://accounts.x.ai/device/AB", userCode: "AB" }],
      "GET /providers/xai/subscription/login/l1": [200, { status: "connected" }],
    });
    const api = subscriptionLogin("xai", fetcher);
    const started = await api.start();
    expect(started).toMatchObject({ loginId: "l1", kind: "device", userCode: "AB", pasteHint: "" });
    expect(await api.state("l1")).toEqual({ status: "connected", error: "" });
    expect(calls.map((c) => c.init?.method ?? "GET")).toEqual(["POST", "GET"]);
  });

  it("sends a paste as JSON and surfaces the server's refusal", async () => {
    const { calls, fetcher } = fakeFetch({
      "POST /providers/anthropic/subscription/login/l2/paste": [400, { detail: "Expected a setup-token" }],
    });
    const api = subscriptionLogin("anthropic", fetcher);
    await expect(api.paste("l2", "nope")).rejects.toEqual(new SignInError("Expected a setup-token"));
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ value: "nope" });
  });

  it("accepts providers listed with and without a subscription", () => {
    const base = { id: "xai", label: "xAI", authKinds: ["api_key"], connected: false };
    expect(providerInfoSchema.parse({ ...base, subscription: null }).subscription).toBeNull();
    expect(providerInfoSchema.parse({ ...base, subscription: { label: "Sign in with Grok", connected: true, capabilities: ["Background agents"] } }).subscription?.connected).toBe(true);
  });
});
