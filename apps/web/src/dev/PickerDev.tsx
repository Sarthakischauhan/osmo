import {
  RadialPicker,
  type ServerEvent,
  type VoiceModel,
  type VoiceSelection,
  sameOriginSocketUrl,
  usePersistedPick,
  useOsmoSession,
} from "@osmo/osmo-composer";
import "@osmo/osmo-composer/radial-picker.css";
import { useCallback, useMemo, useRef, useState } from "react";
import { MOCK_REGISTRY } from "./mockRegistry";

/** /dev/picker: the radial picker against a mock registry, wired to Symphony when connected. */
export function PickerDev() {
  const [commits, setCommits] = useState<VoiceSelection[]>([]);
  const savePick = useRef<(next: VoiceSelection) => void>();
  const onEvent = useCallback((event: ServerEvent) => {
    // `set_voice` changed the session: the picker value and summary follow it.
    if (event.type === "voice.configured" && event.source === "tool") savePick.current?.(event.selection);
  }, []);
  const [registryFromServer, setRegistryFromServer] = useState<VoiceModel[]>([]);
  const registry = useMemo<VoiceModel[]>(() => {
    const live = new Map(registryFromServer.map((model) => [model.id, model]));
    return [...MOCK_REGISTRY.map((model) => live.get(model.id) ?? model), ...registryFromServer.filter((m) => !MOCK_REGISTRY.some((x) => x.id === m.id))];
  }, [registryFromServer]);
  const [pick, save] = usePersistedPick(registry);
  savePick.current = save;
  const session = useOsmoSession({
    url: sameOriginSocketUrl(window.location),
    voice: pick?.voice,
    model: pick?.model,
    effort: pick?.effort,
    onEvent,
  });
  if (session.voiceModels !== registryFromServer && session.voiceModels.length > 0) setRegistryFromServer(session.voiceModels);

  const onChange = (next: VoiceSelection) => {
    setCommits((previous) => [...previous, next]);
    save(next);
    session.configure(next);
  };

  return (
    <div className="picker-dev">
      <header>
        <strong>/dev/picker</strong>
        <button type="button" className="end" onClick={session.connection === "disconnected" ? () => void session.start() : session.end}>
          {session.connection === "disconnected" ? "Connect to Symphony" : session.connection === "connecting" ? "Connecting…" : "Disconnect"}
        </button>
      </header>
      <main>
        {pick && (
          <RadialPicker
            registry={registry}
            value={pick}
            onChange={onChange}
            onPreview={session.previewVoice}
            onPreviewStop={session.stopPreview}
            previewing={session.previewing}
            previewDisabled={session.state === "speaking"}
          />
        )}
      </main>
      <ol className="picker-commits" aria-label="onChange calls">
        {commits.map((commit, i) => (
          <li key={i}>{`session.configure ${JSON.stringify(commit)}`}</li>
        ))}
      </ol>
    </div>
  );
}
