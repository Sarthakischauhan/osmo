import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { runAudioRenderer, browserRuntime } from "./audioRenderer";
import { OrbApp } from "./OrbApp";
import "./desktopBridge";
import "./tailwind.css";
import "./styles.css";

// The Electron app's other windows load this same page: the floating window at `#orb`, and the hidden audio renderer
// (the microphone and speakers of main's call) at `#audio`, which shows nothing.
const bridge = window.osmoDesktop;
const audio = window.osmoAudio;
const orb = window.location.hash === "#orb" && bridge !== undefined;
if (orb) document.documentElement.classList.add("osmo-orb-window");

const root = document.getElementById("root");
if (window.location.hash === "#audio" && audio !== undefined) {
  runAudioRenderer(audio, browserRuntime);
} else if (root) {
  createRoot(root).render(
    <StrictMode>
      {orb ? <OrbApp bridge={bridge} /> : <App />}
    </StrictMode>,
  );
}
