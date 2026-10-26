import type { VoiceModel } from "@osmo/osmo-composer";
import { Check } from "lucide-react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import type { ProviderInfo } from "../../ConnectProvider";

export interface ModelPickerProps {
  registry: readonly VoiceModel[];
  providers: readonly ProviderInfo[];
  current: string | undefined;
  onPick: (model: VoiceModel) => void;
  /** A provider with no credential: open its Connect dialog. */
  onConnect: (providerId: string) => void;
}

/** The voice model list behind the provider pill, grouped by provider with its connection status. */
export function ModelPicker({ registry, providers, current, onPick, onConnect }: ModelPickerProps) {
  const groups = providerGroups(registry, providers);
  return (
    <Command className="osmo-model-picker" loop>
      <CommandInput placeholder="Search models…" />
      <CommandList>
        <CommandEmpty>No models found.</CommandEmpty>
        {groups.map(({ id, label, connected, models }) => (
          <CommandGroup key={id} heading={`${label} · ${connected ? "connected" : "not connected"}`}>
            {models.length === 0 ? (
              <CommandItem value={`${label} connect`} onSelect={() => onConnect(id)}>
                <span className="osmo-list-title">Connect {label}</span>
              </CommandItem>
            ) : (
              models.map((model) => (
                <CommandItem
                  key={model.id}
                  value={`${label} ${model.id} ${model.label}`}
                  data-current={model.id === current}
                  onSelect={() => (model.locked ? onConnect(id) : onPick(model))}
                >
                  <span className="osmo-list-title">{modelName(model)}</span>
                  {model.locked ? <span className="osmo-tertiary">Connect</span> : model.id === current && <Check size={14} aria-label="Current" />}
                </CommandItem>
              ))
            )}
          </CommandGroup>
        ))}
      </CommandList>
    </Command>
  );
}

/** The model's id without its provider prefix, e.g. `gpt-realtime`. */
export function modelName(model: VoiceModel): string {
  const [, name] = model.id.split(":", 2);
  return name ?? model.id;
}

interface ProviderGroup {
  id: string;
  label: string;
  connected: boolean;
  models: VoiceModel[];
}

function providerGroups(registry: readonly VoiceModel[], providers: readonly ProviderInfo[]): ProviderGroup[] {
  const groups = new Map<string, ProviderGroup>();
  for (const provider of providers) groups.set(provider.id, { id: provider.id, label: provider.label, connected: provider.connected, models: [] });
  for (const model of registry) {
    const group = groups.get(model.provider) ?? { id: model.provider, label: model.provider, connected: !model.locked, models: [] };
    group.models.push(model);
    groups.set(model.provider, group);
  }
  return [...groups.values()].sort((a, b) => Number(b.connected) - Number(a.connected));
}
