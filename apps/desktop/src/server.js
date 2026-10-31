import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
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

async function responseAt(origin, path, token) {
  const headers = token ? { 'X-Osmo-Desktop-Token': token } : undefined;
  return fetch(`${origin}${path}`, { headers, signal: AbortSignal.timeout(500) });
}

export async function trustedServer(origin, token) {
  try {
    const health = await responseAt(origin, '/api/health', token);
    if (!health.ok || health.headers.get('x-osmo-desktop-token') !== token) return false;
    const page = await responseAt(origin, '/', token);
    return page.ok && page.headers.get('content-type')?.includes('text/html') === true;
  } catch { return false; }
}

/** Start a server this app owns. A server already on the port is trusted only when it proves this app's token. */
export async function startServer({ packaged, resourcesPath, origin, token = process.env.OSMO_DESKTOP_TOKEN }) {
  if (!token) throw new Error('OSMO_DESKTOP_TOKEN is required before the desktop app can trust its server');
  if (await trustedServer(origin, token)) return { stop() {}, token };
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
      OSMO_STATIC_DIR: web, OSMO_TOOLS: process.env.OSMO_TOOLS ?? 'osmo,search,image,desktop', OSMO_DESKTOP_TOKEN: token },
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
      if (await trustedServer(origin, token)) return { stop, token };
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error(`Osmo server did not start. ${error}`);
  } catch (error) { stop(); throw error; }
}
