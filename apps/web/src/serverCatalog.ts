import { type VoiceModel, serverEventSchema } from "@osmo/osmo-composer";
import { useCallback, useEffect, useRef, useState } from "react";
import { type ProviderInfo, providersResponseSchema } from "./ConnectProvider";

/** What the picker is built from: the server's voice models (`GET /voice-models`) and providers (`GET /providers`). */
export interface ServerCatalog {
  models: VoiceModel[];
  providers: ProviderInfo[];
}

export type Fetcher = (url: string, init?: { signal?: AbortSignal }) => Promise<Response>;

const RETRY_FIRST_MS = 1000;
const RETRY_MAX_MS = 10_000;

/** Backoff between catalog attempts while the server is not answering: 1 s, 2 s, 4 s ... up to 10 s. */
export function retryDelay(attempt: number): number {
  return Math.min(RETRY_FIRST_MS * 2 ** attempt, RETRY_MAX_MS);
}

async function json(fetcher: Fetcher, url: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetcher(url, { signal });
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return response.json();
}

/** Both catalog requests; rejects when either fails or answers an unexpected shape. */
export async function fetchCatalog(fetcher: Fetcher, signal?: AbortSignal): Promise<ServerCatalog> {
  const [modelsBody, providersBody] = await Promise.all([json(fetcher, "/voice-models", signal), json(fetcher, "/providers", signal)]);
  const models = serverEventSchema.parse(modelsBody);
  if (models.type !== "models") throw new Error(`/voice-models answered a ${models.type} event`);
  return { models: models.models, providers: providersResponseSchema.parse(providersBody).providers };
}

/** Load attempts until one succeeds or `signal` aborts; resolves undefined only when aborted. */
export async function loadCatalog(
  fetcher: Fetcher,
  signal: AbortSignal,
  wait: (ms: number, signal: AbortSignal) => Promise<void>,
): Promise<ServerCatalog | undefined> {
  for (let attempt = 0; !signal.aborted; attempt++) {
    try {
      return await fetchCatalog(fetcher, signal);
    } catch {
      await wait(retryDelay(attempt), signal);
    }
  }
  return undefined;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    });
  });
}

/**
 * The server catalog, kept loading until the server answers: a page opened before
 * symphony-server is up (or while it restarts) fills in once it is reachable.
 * `refresh` reloads it, e.g. after a provider is connected.
 */
export function useServerCatalog(fetcher: Fetcher = (url, init) => fetch(url, init)): [ServerCatalog | undefined, () => void] {
  const [catalog, setCatalog] = useState<ServerCatalog>();
  const [generation, setGeneration] = useState(0);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    const controller = new AbortController();
    void loadCatalog(fetcherRef.current, controller.signal, sleep).then((loaded) => {
      if (loaded !== undefined) setCatalog(loaded);
    });
    return () => controller.abort();
  }, [generation]);
  const refresh = useCallback(() => setGeneration((n) => n + 1), []);
  return [catalog, refresh];
}
