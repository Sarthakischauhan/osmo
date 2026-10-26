import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../../../', import.meta.url));
const url = 'http://127.0.0.1:5173';
const vite = spawn('bun', ['run', '--filter', '@osmo/web', 'dev', '--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: root, stdio: 'inherit' });
let electron;
let stopped = false;
function stop() { if (stopped) return; stopped = true; electron?.kill(); vite.kill(); }
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
vite.on('error', error => { console.error(error); process.exitCode = 1; stop(); });
vite.on('exit', code => { if (!stopped) { process.exitCode = code || 1; stop(); } });
try {
  let ready = false;
  for (let attempt = 0; attempt < 100 && !stopped; attempt++) {
    try { ready = (await fetch(url, { signal: AbortSignal.timeout(500) })).ok; } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!stopped && !ready) throw new Error('Vite did not start');
  if (!stopped) {
    electron = spawn(require('electron'), ['.'], { cwd: fileURLToPath(new URL('../', import.meta.url)), stdio: 'inherit', env: { ...process.env, OSMO_DESKTOP_URL: url } });
    electron.on('error', error => { console.error(error); process.exitCode = 1; stop(); });
    electron.on('exit', code => { process.exitCode = code || 0; stop(); });
  }
} catch (error) { console.error(error); process.exitCode = 1; stop(); }
