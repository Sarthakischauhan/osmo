import type { VoiceModel } from "@osmo/osmo-composer";
import { ChevronDown } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { ProviderInfo } from "../../ConnectProvider";
import { ModelPicker, modelName } from "./ModelPicker";

export type PillState = "connected" | "connecting" | "error" | "none";

export interface ProviderPillProps {
  state: PillState;
  registry: readonly VoiceModel[];
  providers: readonly ProviderInfo[];
  current: string | undefined;
  onPick: (model: VoiceModel) => void;
  onConnect: (providerId: string) => void;
  /** Connection failed: sign in to the current provider again. */
  onRetry: () => void;
  /** Open the picker (screenshots and tests). */
  defaultOpen?: boolean;
}

export function pillState(providers: readonly ProviderInfo[], connection: "disconnected" | "connecting" | "connected", error: string | undefined): PillState {
  // A failed /voice-models or session is an error even when it leaves no provider listed as connected.
  if (error !== undefined) return "error";
  if (!providers.some((provider) => provider.connected)) return "none";
  return connection === "connecting" ? "connecting" : "connected";
}

/** Which provider and model the voice runs on; opens the model picker. */
export function ProviderPill({ state, registry, providers, current, onPick, onConnect, onRetry, defaultOpen }: ProviderPillProps) {
  if (state === "none") {
    const first = providers[0];
    return (
      <span className="osmo-pill" data-state="none">
        <span className="osmo-dot osmo-dot-none" aria-hidden />
        No provider
        <button type="button" className="osmo-link osmo-underline" disabled={first === undefined} onClick={() => first && onConnect(first.id)}>
          Connect
        </button>
      </span>
    );
  }
  if (state === "error") {
    return (
      <span className="osmo-pill" data-state="error" role="status">
        <span className="osmo-dot osmo-dot-err" aria-hidden />
        Connection failed
        <button type="button" className="osmo-link osmo-underline" onClick={onRetry}>
          Retry
        </button>
      </span>
    );
  }
  const model = registry.find((m) => m.id === current);
  const provider = providers.find((p) => p.id === model?.provider);
  return (
    <Popover defaultOpen={defaultOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="osmo-pill" data-state={state} aria-label="Voice model">
          {state === "connecting" ? (
            <>
              <span className="osmo-spin osmo-muted" aria-hidden>
                ◌
              </span>
              Connecting
            </>
          ) : (
            <>
              <span className="osmo-dot osmo-dot-ok" aria-hidden />
              {provider?.label ?? model?.provider ?? "Model"}
            </>
          )}
          {model && <span className="osmo-mono osmo-muted">{modelName(model)}</span>}
          <ChevronDown size={14} className="osmo-tertiary" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="osmo-popover">
        <ModelPicker registry={registry} providers={providers} current={current} onPick={onPick} onConnect={onConnect} />
      </PopoverContent>
    </Popover>
  );
}
