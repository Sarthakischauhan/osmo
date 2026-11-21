import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ProviderInfo } from "./ConnectProvider";
import { type LoginStarted, SignInError, subscriptionLogin } from "./subscriptionLogin";

const POLL_MS = 1500;

type Phase = { kind: "idle"; error?: string } | { kind: "starting" } | { kind: "pending"; login: LoginStarted; error?: string; busy?: boolean };

export interface SubscriptionSignInProps {
  provider: ProviderInfo & { subscription: NonNullable<ProviderInfo["subscription"]> };
  /** The subscription was connected or removed: refresh the registry. */
  onChanged: (provider: ProviderInfo) => void;
}

/** "Sign in with Grok / ChatGPT / Claude": core_ai's subscription login, run by the server. The token
 *  stays on the server; this only shows the link, code or paste box the provider's flow needs. */
export function SubscriptionSignIn({ provider, onChanged }: SubscriptionSignInProps) {
  const { subscription } = provider;
  const api = useRef(subscriptionLogin(provider.id)).current;
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [pasted, setPasted] = useState("");
  const pendingId = phase.kind === "pending" ? phase.login.loginId : undefined;
  // The poll must not restart (and cancel the login) when the parent re-renders with new callbacks.
  const latest = useRef({ provider, onChanged });
  latest.current = { provider, onChanged };

  // Follow a pending login until it finishes; cancel it if the dialog closes first.
  useEffect(() => {
    if (pendingId === undefined) return;
    let done = false;
    const timer = window.setInterval(async () => {
      const state = await api.state(pendingId).catch(() => undefined);
      if (done || state === undefined || state.status === "pending") return;
      done = true;
      window.clearInterval(timer);
      if (state.status === "connected") {
        const { provider: current, onChanged: changed } = latest.current;
        toast.success(`${current.label} subscription connected`);
        setPhase({ kind: "idle" });
        changed(current);
      } else {
        setPhase({ kind: "idle", error: state.error || "Sign-in did not finish" });
      }
    }, POLL_MS);
    return () => {
      window.clearInterval(timer);
      if (!done) void api.cancel(pendingId);
    };
  }, [api, pendingId]);

  const start = async () => {
    setPhase({ kind: "starting" });
    setPasted("");
    try {
      setPhase({ kind: "pending", login: await api.start() });
    } catch (error) {
      setPhase({ kind: "idle", error: error instanceof SignInError ? error.message : "Could not start the sign-in" });
    }
  };

  const finish = async (login: LoginStarted) => {
    setPhase({ kind: "pending", login, busy: true });
    try {
      const state = await api.paste(login.loginId, pasted.trim());
      if (state.status !== "connected") setPhase({ kind: "pending", login, error: state.error || "Still waiting for the provider" });
      // `connected` is picked up by the poll, which also refreshes the registry.
    } catch (error) {
      setPhase({ kind: "pending", login, error: error instanceof SignInError ? error.message : "Could not finish the sign-in" });
    }
  };

  const signOut = async () => {
    try {
      await api.signOut();
      toast.success(`Signed out of the ${provider.label} subscription`);
      onChanged(provider);
    } catch {
      toast.error(`Could not sign out of the ${provider.label} subscription`);
    }
  };

  const scope = `Unlocks ${subscription.capabilities.join(", ").toLowerCase()} only; voice and images still need an API key.`;

  if (phase.kind === "pending") {
    const { login } = phase;
    return (
      <div className="grid gap-3">
        <p className="m-0 text-sm text-foreground/80">{login.title}</p>
        <p className="m-0 text-sm text-foreground/60">{login.instructions}</p>
        {login.userCode && (
          <p className="m-0 justify-self-start rounded-md bg-foreground/5 px-3 py-1 font-mono text-lg tracking-widest" aria-label="Code to enter">
            {login.userCode}
          </p>
        )}
        {login.url && (
          <a className="text-sm underline underline-offset-4" href={login.url} target="_blank" rel="noreferrer noopener">
            Open {provider.label} sign-in
          </a>
        )}
        {login.kind !== "device" && (
          <form
            className="grid gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (pasted.trim()) void finish(login);
            }}
          >
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={login.pasteHint || "Paste here"}
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
              aria-label="Paste from the sign-in page"
            />
            <Button type="submit" disabled={phase.busy || !pasted.trim()} className="justify-self-end rounded-full">
              {phase.busy ? "Checking…" : "Finish sign-in"}
            </Button>
          </form>
        )}
        {login.kind === "device" && <p className="m-0 text-sm text-foreground/40">Waiting for you to approve…</p>}
        {phase.error && <p className="m-0 text-sm text-foreground/80">{phase.error}</p>}
        <Button variant="ghost" className="justify-self-start rounded-full" onClick={() => setPhase({ kind: "idle" })}>
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      <p className="m-0 text-sm text-foreground/60">
        {subscription.connected ? `Signed in with your ${provider.label} subscription. ` : `Use your ${provider.label} subscription instead of an API key. `}
        {scope}
      </p>
      {phase.kind === "idle" && phase.error && <p className="m-0 text-sm text-foreground/80">{phase.error}</p>}
      {subscription.connected ? (
        <Button variant="outline" className="justify-self-end rounded-full" onClick={() => void signOut()}>
          Sign out
        </Button>
      ) : (
        <Button className="justify-self-end rounded-full" disabled={phase.kind === "starting"} onClick={() => void start()}>
          {phase.kind === "starting" ? "Starting…" : subscription.label}
        </Button>
      )}
    </div>
  );
}
