import { fileURLToPath } from "node:url";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const http = process.env.SYMPHONY_HTTP_URL ?? "http://127.0.0.1:8787";

/** Bundle Silero and ONNX WASM locally so packaged Electron works without a CDN.
 * Development serves the same assets from node_modules on the same origin.
 */
function localVadAssets(): Plugin {
  // Bun can install dependencies under a workspace's node_modules instead of hoisting.
  // Resolve from the composer package that actually declares these dependencies.
  const fromComposer = createRequire(fileURLToPath(new URL("../../packages/osmo-composer/package.json", import.meta.url)));
  function assetDir(module: string, test: (name: string) => boolean): string {
    let directory = path.dirname(fromComposer.resolve(module));
    for (let i = 0; i < 5; i++) {
      try {
        if (readdirSync(directory).some(test)) return directory;
      } catch { /* try its parent */ }
      directory = path.dirname(directory);
    }
    throw new Error("Could not find bundled VAD assets for " + module);
  }
  const vad = assetDir("@ricky0123/vad-web", name => name === "silero_vad_v5.onnx");
  const ort = assetDir("onnxruntime-web", name => /^ort.*\.wasm$/.test(name));
  const assets = new Map<string, string>();
  assets.set("silero_vad_v5.onnx", path.join(vad, "silero_vad_v5.onnx"));
  assets.set("vad.worklet.bundle.min.js", path.join(vad, "vad.worklet.bundle.min.js"));
  for (const file of readdirSync(ort)) {
    if (/^ort.*\.(wasm|mjs)$/.test(file)) assets.set(file, path.join(ort, file));
  }
  return {
    name: "osmo-local-silero-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = req.url?.split("?")[0]?.replace(/^\/vad\//, "");
        const file = req.url?.startsWith("/vad/") ? assets.get(name ?? "") : undefined;
        if (!file) return next();
        res.setHeader("Content-Type",
          name?.endsWith(".wasm") ? "application/wasm" :
          name?.endsWith(".onnx") ? "application/octet-stream" : "text/javascript");
        res.end(readFileSync(file));
      });
    },
    generateBundle() {
      for (const [name, filename] of assets) {
        this.emitFile({ type: "asset", fileName: "vad/" + name, source: readFileSync(filename) });
      }
    },
  };
}

// In dev, /ws is proxied to symphony-server; in production the server serves this build.
export default defineConfig({
  plugins: [react(), tailwindcss(), localVadAssets()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    proxy: {
      "/ws": { target: process.env.SYMPHONY_URL ?? "ws://127.0.0.1:8787", ws: true },
      "/voice-models": { target: http },
      "/providers": { target: http },
      "/agent-model": { target: http },
      "/api": { target: http },
    },
  },
});
