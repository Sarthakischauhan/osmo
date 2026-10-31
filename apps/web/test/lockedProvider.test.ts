import { describe, expect, it } from "vitest";
import { type ProviderInfo, lockedProviderAction } from "../src/ConnectProvider";

const PROVIDERS: ProviderInfo[] = [
  { id: "xai", label: "xAI", authKinds: ["api_key"], connected: true, capabilities: ["Voice"] },
  { id: "openai", label: "OpenAI", authKinds: ["api_key"], connected: false, capabilities: ["Vision"] },
  { id: "openrouter", label: "OpenRouter", authKinds: ["api_key", "oauth"], connected: true, capabilities: ["Text models"] },
];

describe("tapping a locked provider chip", () => {
  it("opens Connect provider for an unconnected provider", () => {
    expect(lockedProviderAction(PROVIDERS, "openai")).toEqual({ kind: "connect", provider: PROVIDERS[1] });
  });
  it("explains a connected provider with no voice models instead of reconnecting it", () => {
    expect(lockedProviderAction(PROVIDERS, "openrouter")).toEqual({ kind: "explain", message: "OpenRouter is connected; it has no voice models yet" });
  });
  it("explains a provider the server does not list", () => {
    expect(lockedProviderAction(PROVIDERS, "acme")).toEqual({ kind: "explain", message: "Osmo cannot connect acme yet" });
  });
});
