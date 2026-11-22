import { z } from "zod";

/** `GET /agent-model`: the background agent's model and what it can be switched to. */
export const modelOptionSchema = z.object({
  id: z.string(),
  label: z.string(),
  provider: z.string(),
  providerLabel: z.string(),
  via: z.enum(["subscription", "api_key"]),
});
export type ModelOption = z.infer<typeof modelOptionSchema>;

export const agentModelStateSchema = z.object({
  chosen: z.string().nullable(),
  effective: z.string(),
  source: z.enum(["chosen", "configured", "automatic"]),
  options: z.array(modelOptionSchema),
  notes: z.array(z.string()).default([]),
});
export type AgentModelState = z.infer<typeof agentModelStateSchema>;

const errorSchema = z.object({ detail: z.string() });

export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

async function parse(response: Response | undefined): Promise<AgentModelState> {
  if (response === undefined) throw new Error("Could not reach the server");
  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = errorSchema.safeParse(body);
    throw new Error(detail.success ? detail.data.detail : `Request failed (HTTP ${response.status})`);
  }
  return agentModelStateSchema.parse(body);
}

export function agentModelApi(fetcher: Fetcher = (input, init) => fetch(input, init)) {
  return {
    get: () => fetcher("/agent-model").catch(() => undefined).then(parse),
    /** Choose `model`; `null` goes back to automatic selection (or OSMO_AGENT_MODEL). */
    set: (model: string | null) =>
      fetcher("/agent-model", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) })
        .catch(() => undefined)
        .then(parse),
  };
}

/** Options grouped by provider, in the server's order. */
export function groupOptions(options: readonly ModelOption[]): { provider: string; label: string; via: ModelOption["via"]; models: ModelOption[] }[] {
  const groups = new Map<string, { provider: string; label: string; via: ModelOption["via"]; models: ModelOption[] }>();
  for (const option of options) {
    const group = groups.get(option.provider) ?? { provider: option.provider, label: option.providerLabel, via: option.via, models: [] };
    group.models.push(option);
    groups.set(option.provider, group);
  }
  return [...groups.values()];
}

/** "gemini:gemini-2.5-pro" → "gemini-2.5-pro". */
export function modelName(id: string): string {
  return id.split(":", 2)[1] ?? id;
}
