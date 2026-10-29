import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { OrbApp } from "./OrbApp";
import "./desktopBridge";
import "./tailwind.css";
import "./styles.css";

// The Electron app's floating window loads this same page at `#orb`.
const bridge = window.osmoDesktop;
const orb = window.location.hash === "#orb" && bridge !== undefined;
if (orb) document.documentElement.classList.add("osmo-orb-window");

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      {orb ? <OrbApp bridge={bridge} /> : <App />}
    </StrictMode>,
  );
}
