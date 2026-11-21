import { z } from "zod";

/** `POST /providers/{id}/subscription/login`: what to show while a subscription sign-in is pending (never a token). */
export const loginStartedSchema = z.object({
  loginId: z.string(),
  kind: z.enum(["browser", "device", "paste"]),
  title: z.string(),
  instructions: z.string(),
  url: z.string().default(""),
  userCode: z.string().default(""),
  pasteHint: z.string().default(""),
});
export type LoginStarted = z.infer<typeof loginStartedSchema>;

export const loginStateSchema = z.object({
  status: z.enum(["pending", "connected", "failed", "cancelled"]),
  error: z.string().default(""),
});
export type LoginState = z.infer<typeof loginStateSchema>;

const errorSchema = z.object({ detail: z.string() });

export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

/** Thrown with the server's message when a sign-in request is refused. */
export class SignInError extends Error {}

async function call<S extends z.ZodTypeAny>(fetcher: Fetcher, url: string, init: RequestInit, schema: S): Promise<z.output<S>> {
  const response = await fetcher(url, init).catch(() => undefined);
  if (response === undefined) throw new SignInError("Could not reach the server");
  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = errorSchema.safeParse(body);
    throw new SignInError(detail.success ? detail.data.detail : `Sign-in failed (HTTP ${response.status})`);
  }
  return schema.parse(body);
}

/** The server's subscription sign-in endpoints for one provider. */
export function subscriptionLogin(provider: string, fetcher: Fetcher = (input, init) => fetch(input, init)) {
  const base = `/providers/${encodeURIComponent(provider)}/subscription`;
  const login = (id: string) => `${base}/login/${encodeURIComponent(id)}`;
  return {
    start: () => call(fetcher, `${base}/login`, { method: "POST" }, loginStartedSchema),
    state: (id: string) => call(fetcher, login(id), {}, loginStateSchema),
    paste: (id: string, value: string) =>
      call(fetcher, `${login(id)}/paste`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) }, loginStateSchema),
    cancel: (id: string) => fetcher(login(id), { method: "DELETE" }).then(() => undefined, () => undefined),
    signOut: async () => {
      const response = await fetcher(base, { method: "DELETE" }).catch(() => undefined);
      if (!response?.ok) throw new SignInError("Could not sign out");
    },
  };
}
