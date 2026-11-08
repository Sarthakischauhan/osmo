import { describe, expect, it } from "vitest";
import { type ModelOption, agentModelApi, groupOptions, modelName } from "../src/agentModel";

const option = (id: string, provider: string, providerLabel: string, via: ModelOption["via"] = "api_key"): ModelOption => ({
  id,
  label: modelName(id),
  provider,
  providerLabel,
  via,
});

describe("agent model picker", () => {
  it("groups options by provider in the server's order", () => {
    const groups = groupOptions([option("openai:gpt-5", "openai", "OpenAI", "subscription"), option("gemini:gemini-2.5-pro", "gemini", "Gemini"), option("openai:gpt-4o", "openai", "OpenAI", "subscription")]);
    expect(groups.map((g) => [g.label, g.via, g.models.map((m) => m.label)])).toEqual([
      ["OpenAI", "subscription", ["gpt-5", "gpt-4o"]],
      ["Gemini", "api_key", ["gemini-2.5-pro"]],
    ]);
  });

  it("puts a choice and surfaces the server's refusal", async () => {
    const sent: unknown[] = [];
    const api = agentModelApi(async (_url, init) => {
      sent.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ detail: "grok:grok-4.7 is not available; connect its provider first" }), { status: 400 });
    });
    await expect(api.set("grok:grok-4.7")).rejects.toThrow("not available");
    expect(sent).toEqual([{ model: "grok:grok-4.7" }]);
  });

  it("parses the state, including automatic selection", async () => {
    const body = { chosen: null, effective: "gemini:gemini-3.7-flash", source: "automatic", options: [], notes: ["xAI: no text models"] };
    const state = await agentModelApi(async () => new Response(JSON.stringify(body))).get();
    expect(modelName(state.effective)).toBe("gemini-3.7-flash");
    expect(state.notes).toHaveLength(1);
  });
});
