import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, /ws is proxied to symphony-server; in production the server serves this build.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ws": { target: process.env.SYMPHONY_URL ?? "ws://127.0.0.1:8787", ws: true },
      "/voice-models": { target: process.env.SYMPHONY_HTTP_URL ?? "http://127.0.0.1:8787" },
      "/api": { target: process.env.SYMPHONY_HTTP_URL ?? "http://127.0.0.1:8787" },
    },
  },
});
