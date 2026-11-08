import { Check, ChevronDown, Settings, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { type AgentModelState, agentModelApi, groupOptions, modelName } from "../agentModel";
import type { ProviderInfo } from "../ConnectProvider";
import { SubscriptionSignIn } from "../SubscriptionSignIn";
import { DesktopSupport, type useDesktopSupport } from "../desktopSupport";

const TEXT_PROVIDERS = new Set(["xai", "openai", "anthropic", "gemini", "vercel"]);

export interface AgentModelDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  providers: readonly ProviderInfo[];
  /** Refresh provider status after a sign-in or sign-out. */
  onCredentialsChanged: () => void;
  onConnectKey: (provider: ProviderInfo) => void;
  desktop: ReturnType<typeof useDesktopSupport>;
  onChanged?: (state: AgentModelState) => void;
}

/** Dedicated background-agent settings: credentials and model are configured independently of voice. */
export function AgentModelDialog({ open, onOpenChange, providers, onCredentialsChanged, onConnectKey, onChanged, desktop }: AgentModelDialogProps) {
  const api = useRef(agentModelApi()).current;
  const [state, setState] = useState<AgentModelState>();
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const refreshModel = useCallback(async () => {
    try {
      const loaded = await api.get();
      setState(loaded);
      setError(undefined);
      onChangedRef.current?.(loaded);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not load agent settings");
    }
  }, [api]);

  // Re-query after provider state changes as well as when opening the screen.
  useEffect(() => {
    if (open) void refreshModel();
  }, [open, providers, refreshModel]);

  const choose = async (model: string | null) => {
    setPickerOpen(false);
    setSaving(true);
    try {
      const saved = await api.set(model);
      setState(saved);
      onChanged?.(saved);
      setError(undefined);
      toast.success(model === null ? `Agent model: automatic (${modelName(saved.effective)})` : `Agent model: ${modelName(saved.effective)}`);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save the model");
    } finally {
      setSaving(false);
    }
  };

  const textProviders = providers.filter((provider) => TEXT_PROVIDERS.has(provider.id));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="osmo-agent-settings osmo-scroll max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader className="osmo-agent-settings-header">
          <div className="osmo-agent-settings-icon"><Settings size={17} aria-hidden /></div>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Manage your background agent and computer support.</DialogDescription>
        </DialogHeader>
        <section className="osmo-agent-section" aria-label="Computer support">
          <div className="osmo-agent-section-heading"><div><h3>Computer support</h3><p>Allow Osmo to see and interact with your computer when you need help.</p></div></div>
          {desktop.available ? <div className="osmo-settings-desktop"><DesktopSupport desktop={desktop} /></div> : <p className="osmo-agent-note">Available in the Osmo desktop app.</p>}
        </section>
        <div className="osmo-settings-divider" />
        <div className="osmo-agent-section-heading"><div><h3>Background agent</h3><p>Research and multi-step tasks run in the background while you keep talking. The agent uses a separate text model from your voice.</p></div></div>
        <section className="osmo-agent-section" aria-label="Agent model selection">
          <div className="osmo-agent-section-heading"><div><h3>Model</h3><p>Used for the next background task.</p></div><span className="osmo-agent-mode">{state?.source === "configured" ? "Configured" : state?.source === "chosen" ? "Selected" : "Automatic"}</span></div>
          <div className="osmo-agent-current"><span>Current model</span><strong>{state ? modelName(state.effective) : "Loading…"}</strong></div>
          <p className="sr-only">{state ? `Next task: ${state.effective} (${state.source}${state.source === "configured" ? ", OSMO_AGENT_MODEL" : ""}).` : "Loading model availability…"}</p>
          {state && <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
            <PopoverTrigger asChild>
              <button type="button" className="osmo-model-trigger" disabled={saving} aria-label="Background agent model" aria-expanded={pickerOpen}>
                <span><span className="osmo-model-trigger-caption">Selected model</span><strong>{state.chosen ? (state.options.find((option) => option.id === state.chosen)?.label ?? `${modelName(state.chosen)} (unavailable)`) : "Automatic"}</strong></span>
                <ChevronDown size={16} aria-hidden />
              </button>
            </PopoverTrigger>
            <PopoverContent align="start" sideOffset={6} className="osmo-model-popover" onOpenAutoFocus={(event) => event.preventDefault()}>
              <Command className="osmo-model-command">
                <CommandInput placeholder="Search models…" aria-label="Search models" />
                <CommandList className="osmo-model-list">
                  <CommandEmpty>No models found.</CommandEmpty>
                  <CommandGroup heading="Recommended">
                    <CommandItem value="__automatic__" keywords={["automatic", "default"]} onSelect={() => void choose(null)}>
                      <span className="osmo-model-icon"><Sparkles size={15} aria-hidden /></span><span className="osmo-model-copy"><strong>Automatic</strong><small>Let Osmo choose an available model</small></span>{state.chosen === null && <Check size={15} aria-label="Selected" />}
                    </CommandItem>
                  </CommandGroup>
                  {groupOptions(state.options).map((group) => <CommandGroup key={group.provider} heading={`${group.label} · ${group.via === "subscription" ? "subscription" : "API key"}`}>
                    {group.models.map((option) => <CommandItem key={option.id} value={option.id} keywords={[option.label, option.providerLabel]} onSelect={() => void choose(option.id)}>
                      <span className="osmo-model-copy"><strong>{option.label}</strong></span>{state.chosen === option.id && <Check size={15} aria-label="Selected" />}
                    </CommandItem>)}
                  </CommandGroup>)}
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>}
          {state?.notes.map((note) => <p key={note} className="osmo-agent-note">{note}</p>)}
          {error && <p role="alert" className="osmo-agent-error">{error}</p>}
        </section>
        <section className="osmo-agent-section" aria-label="Agent authentication">
          <div className="osmo-agent-section-heading"><div><h3>Connections</h3><p>Connect a provider for the agent's text model.</p></div></div>
          <div className="osmo-agent-provider-list">
            {textProviders.length === 0 && <p className="osmo-agent-note">Provider status is unavailable. Check your connection and reopen settings.</p>}
            {textProviders.map((provider) => (
              <details key={provider.id} className="osmo-agent-provider">
                <summary><span className="osmo-agent-provider-name">{provider.label}</span><span className="osmo-agent-provider-status">{provider.subscription?.connected ? "Subscription connected" : provider.connected ? "API key connected" : "Not connected"}</span><ChevronDown size={14} aria-hidden /></summary>
                <div className="osmo-agent-provider-body">
                  <div className="osmo-agent-key-row"><span>API key: {provider.connected ? "connected" : "not connected"}</span><Button variant="outline" size="sm" onClick={() => onConnectKey(provider)}>{provider.connected ? "Manage key" : "Connect key"}</Button></div>
                  {provider.subscription ? <SubscriptionSignIn provider={{ ...provider, subscription: provider.subscription }} onChanged={() => { onCredentialsChanged(); void refreshModel(); }} /> : <p className="osmo-agent-note">Subscription sign-in isn't available for this provider. Use an API key.</p>}
                </div>
              </details>
            ))}
          </div>
          <p className="osmo-agent-footnote">Subscription sign-in is separate from your API key and preferred for text when both are connected. Voice and images still need an API key. Provider eligibility and limits apply.</p>
        </section>
      </DialogContent>
    </Dialog>
  );
}
