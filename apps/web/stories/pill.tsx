/**
 * ProviderPill story: every pill state side by side, in dark and light, with no server.
 * Run `bunx vite` in apps/web and open /stories/pill.html (dev only; not part of the build).
 */
import type { VoiceModel } from "@osmo/osmo-composer";
import { createRoot } from "react-dom/client";
import { type PillState, ProviderPill } from "@/components/home/ProviderPill";
import type { ProviderInfo } from "@/ConnectProvider";
import "@/tailwind.css";
import "@/styles.css";

const STATES: readonly PillState[] = ["connected", "connecting", "error", "none"];
const PROVIDERS: ProviderInfo[] = [{ id: "xai", label: "xAI", authKinds: ["api_key"], connected: true }];
const REGISTRY: VoiceModel[] = [];
const noop = () => undefined;

function Column({ theme }: { theme: "dark" | "light" }) {
  return (
    <section className={theme} data-theme={theme} style={{ display: "grid", gap: 16, padding: 24, background: "var(--background)", color: "var(--foreground)" }}>
      {STATES.map((state) => (
        <div key={state} data-pill={state} style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <code style={{ width: 96, fontSize: 12, color: "var(--muted-foreground)" }}>{state}</code>
          <ProviderPill state={state} registry={REGISTRY} providers={state === "none" ? [] : PROVIDERS} current="grok-voice-latest" onPick={noop} onConnect={noop} onRetry={noop} />
        </div>
      ))}
    </section>
  );
}

const root = document.getElementById("root");
if (root)
  createRoot(root).render(
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
      <Column theme="dark" />
      <Column theme="light" />
    </div>,
  );
