import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const http = process.env.SYMPHONY_HTTP_URL ?? "http://127.0.0.1:8787";

// In dev, /ws is proxied to symphony-server; in production the server serves this build.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    proxy: {
      "/ws": { target: process.env.SYMPHONY_URL ?? "ws://127.0.0.1:8787", ws: true },
      "/voice-models": { target: http },
      "/providers": { target: http },
      "/api": { target: http },
    },
  },
});
