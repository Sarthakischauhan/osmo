import { describe, expect, it } from "vitest";
import { type CatalogOutcome, type CatalogState, type Fetcher, loadCatalog, nextCatalogState, retryDelay } from "../src/serverCatalog";

const MODELS = {
  type: "models",
  models: [
    {
      id: "grok-voice-latest",
      provider: "xai",
      label: "Grok Voice",
      shortLabel: "Latest",
      capabilities: { tools: true, interrupt: true, customVoices: false, reasoningEffort: ["none", "high"] },
      voices: [{ id: "eve", label: "Eve", traits: [] }],
      defaultVoice: "eve",
      locked: true,
    },
  ],
  selection: { model: "grok-voice-latest", voice: "eve", effort: "high" },
};
const PROVIDERS = {
  providers: [
    { id: "xai", label: "xAI", authKinds: ["api_key"], connected: false },
    { id: "openai", label: "OpenAI", authKinds: ["api_key"], connected: false },
  ],
};

/** The server is down (the dev proxy answers 500) for the first `downFor` attempts, then serves the catalog. */
function serverUpAfter(downFor: number): { fetcher: Fetcher; calls: string[] } {
  const calls: string[] = [];
  const fetcher: Fetcher = async (url) => {
    calls.push(url);
    if (calls.length <= downFor * 2) return new Response("proxy error", { status: 500 });
    return Response.json(url === "/voice-models" ? MODELS : PROVIDERS);
  };
  return { fetcher, calls };
}

describe("loading the picker's models and providers", () => {
  it("keeps trying while the server is down and fills in once it answers", async () => {
    const { fetcher, calls } = serverUpAfter(2);
    const waits: number[] = [];
    const outcomes: CatalogOutcome[] = [];
    const catalog = await loadCatalog(
      fetcher,
      new AbortController().signal,
      async (ms) => {
        waits.push(ms);
      },
      (outcome) => outcomes.push(outcome),
    );
    expect(outcomes.map((o) => o.kind)).toEqual(["failed", "failed", "loaded"]);
    expect(outcomes[0]).toEqual({ kind: "failed", error: "Can't reach symphony-server (/voice-models answered 500); retrying…" });
    expect(catalog?.models.map((m) => m.id)).toEqual(["grok-voice-latest"]);
    expect(catalog?.providers.map((p) => p.id)).toEqual(["xai", "openai"]);
    expect(waits).toEqual([1000, 2000]);
    expect(calls).toHaveLength(6);
  });

  it("stops when aborted (the page unmounts)", async () => {
    const controller = new AbortController();
    const catalog = await loadCatalog(
      serverUpAfter(Number.POSITIVE_INFINITY).fetcher,
      controller.signal,
      async () => {
        controller.abort();
      },
      () => undefined,
    );
    expect(catalog).toBeUndefined();
  });

  it("shows the latest failure while retrying and clears it once both requests succeed", async () => {
    let state: CatalogState = { catalog: undefined, error: undefined };
    const seen: (string | undefined)[] = [];
    await loadCatalog(serverUpAfter(1).fetcher, new AbortController().signal, async () => undefined, (outcome) => {
      state = nextCatalogState(state, outcome);
      seen.push(state.error);
    });
    expect(seen).toEqual(["Can't reach symphony-server (/voice-models answered 500); retrying…", undefined]);
    expect(state.catalog?.providers).toHaveLength(2);
  });

  it("keeps the loaded catalog when a later refresh fails", () => {
    const loaded = nextCatalogState({ catalog: undefined, error: undefined }, { kind: "loaded", catalog: { models: [], providers: [] } });
    const failed = nextCatalogState(loaded, { kind: "failed", error: "down" });
    expect(failed).toEqual({ catalog: { models: [], providers: [] }, error: "down" });
  });

  it("backs off up to 10 s", () => {
    expect([0, 1, 2, 3, 4, 9].map(retryDelay)).toEqual([1000, 2000, 4000, 8000, 10_000, 10_000]);
  });
});
