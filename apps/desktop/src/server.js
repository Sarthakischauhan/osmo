import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../', import.meta.url));
export function pythonRuntime(resourcesPath = process.resourcesPath) {
  if (process.env.OSMO_DESKTOP_PYTHON) return { executable: process.env.OSMO_DESKTOP_PYTHON, home: undefined };
  const bundledRoot = resourcesPath ? path.join(resourcesPath, 'python') : '';
  const bundled = bundledRoot && path.join(bundledRoot, process.platform === 'win32' ? 'python.exe' : 'bin/python3.12');
  if (bundled && existsSync(bundled)) return { executable: bundled, home: bundledRoot };
  const venv = path.join(root, 'apps/symphony-server/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (existsSync(venv)) return { executable: venv, home: undefined };
  return { executable: process.platform === 'win32' ? 'python' : 'python3', home: undefined };
}

export function pythonExecutable() {
  return pythonRuntime().executable;
}

async function healthy(origin) {
  try { return (await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(500) })).ok; }
  catch { return false; }
}

/** Reuse a running local server; otherwise own a child process and stop only that child on quit. */
export async function startServer({ packaged, resourcesPath, origin }) {
  if (await healthy(origin)) {
    const response = await fetch(`${origin}/`, { signal: AbortSignal.timeout(2000) });
    if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) {
      throw new Error(`The server at ${origin} has no web UI. Stop that server and restart Osmo, or start it with OSMO_STATIC_DIR pointing to the built web client.`);
    }
    return { stop() {} };
  }
  if (origin !== 'http://127.0.0.1:8787') throw new Error(`Start your Osmo server at ${origin}`);
  const server = packaged ? path.join(resourcesPath, 'server') : path.join(root, 'apps/symphony-server');
  const vendor = packaged ? path.join(resourcesPath, 'core-server') : path.join(root, 'vendor/core_server');
  const web = packaged ? path.join(resourcesPath, 'web') : path.join(root, 'apps/web/dist');
  if (!existsSync(path.join(web, 'index.html'))) throw new Error('Build the web client with make build before starting the desktop app');
  const python = pythonRuntime(resourcesPath);
  // Native input subprocesses use the same runtime as the server.
  if (python.home) process.env.PYTHONHOME = python.home;
  const child = spawn(python.executable, ['-m', 'symphony_server', '--host', '127.0.0.1', '--port', '8787'], {
    cwd: server,
    stdio: ['ignore', 'inherit', 'pipe'],
    env: { ...process.env, ...(python.home ? { PYTHONHOME: python.home } : {}),
      PYTHONPATH: [path.join(server, 'src'), path.join(vendor, 'src'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      OSMO_STATIC_DIR: web, OSMO_TOOLS: process.env.OSMO_TOOLS ?? 'osmo,search,image,desktop' },
  });
  let error = '';
  let exited = false;
  child.stderr.on('data', chunk => { error = (error + chunk).slice(-4000); });
  child.on('error', cause => { error = cause.message; exited = true; });
  child.on('exit', () => { exited = true; });
  const stop = () => child.kill();
  try {
    for (let attempt = 0; attempt < 60; attempt++) {
      if (exited) throw new Error(error || 'Osmo server exited');
      if (await healthy(origin)) return { stop };
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error(`Osmo server did not start. ${error}`);
  } catch (error) { stop(); throw error; }
}
