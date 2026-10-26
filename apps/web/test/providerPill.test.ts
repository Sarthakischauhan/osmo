import { describe, expect, it } from "vitest";
import type { ProviderInfo } from "../src/ConnectProvider";
import { pillState } from "../src/components/home/ProviderPill";

const XAI: ProviderInfo = { id: "xai", label: "xAI", authKinds: ["api_key"], connected: true };
const OFF: ProviderInfo = { ...XAI, connected: false };

describe("pillState", () => {
  it("reports an error before 'No provider' (a 500 from /voice-models leaves no provider listed)", () => {
    expect(pillState([], "disconnected", "voice-models: HTTP 500")).toBe("error");
    expect(pillState([OFF], "disconnected", "voice-models: HTTP 500")).toBe("error");
  });
  it("is none with no connected provider and no error", () => {
    expect(pillState([OFF], "disconnected", undefined)).toBe("none");
  });
  it("is connecting while the session connects, else connected", () => {
    expect(pillState([XAI], "connecting", undefined)).toBe("connecting");
    expect(pillState([XAI], "disconnected", undefined)).toBe("connected");
  });
});
