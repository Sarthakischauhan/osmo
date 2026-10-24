import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { PickerDev } from "./dev/PickerDev";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      {window.location.pathname === "/dev/picker" ? <PickerDev /> : <App />}
    </StrictMode>,
  );
}
