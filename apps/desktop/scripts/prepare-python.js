import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, globSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = fileURLToPath(new URL('../', import.meta.url));
const server = path.resolve(desktop, '../symphony-server');
const runtime = path.join(desktop, 'build/python');
// The runtime is rebuilt only when what it installs changes. The server and core_server sources ship separately and
// come first on PYTHONPATH, so their code changes need no rebuild. sign-python.js keeps its own stamp in the runtime.
const stamp = path.join(runtime, '.osmo-inputs');
const inputs = [path.join(server, 'requirements.txt'), path.join(server, 'pyproject.toml'),
  path.resolve(desktop, '../../vendor/core_server/pyproject.toml'), fileURLToPath(import.meta.url)];
const key = `${process.platform}-${process.arch}-${inputs.reduce((hash, file) => hash.update(readFileSync(file)), createHash('sha256')).digest('hex')}`;
if (existsSync(stamp) && readFileSync(stamp, 'utf8') === key) {
  console.log('Bundled Python is up to date');
  process.exit(0);
}
const run = (args) => execFileSync('uv', args, { cwd: server, stdio: 'inherit' });
// Copy a standalone distribution, not a venv with links to the build machine's Python.
run(['python', 'install', '3.12']);
const sourcePython = execFileSync('uv', ['python', 'find', '--managed-python', '3.12'], { encoding: 'utf8' }).trim();
const source = process.platform === 'win32' ? path.dirname(sourcePython) : path.dirname(path.dirname(sourcePython));
rmSync(runtime, { recursive: true, force: true });
mkdirSync(path.dirname(runtime), { recursive: true });
cpSync(source, runtime, { recursive: true, verbatimSymlinks: true });
const python = path.join(runtime, process.platform === 'win32' ? 'python.exe' : 'bin/python3.12');
const site = path.join(runtime, process.platform === 'win32' ? 'Lib/site-packages' : 'lib/python3.12/site-packages');
const temp = mkdtempSync(path.join(tmpdir(), 'osmo-requirements-'));
try {
  const requirements = path.join(temp, 'requirements.txt');
  // Install runtime dependencies without an editable path into the source checkout or dev tools.
  writeFileSync(requirements, readFileSync(path.join(server, 'requirements.txt'), 'utf8').replace(/^-e .*$/m, ''));
  run(['pip', 'install', '--no-sources', '--python', python, '--target', site, '-r', requirements, '.[desktop]']);
  // pip copies the whole package directory, a developer's .env with its API keys included: none may ship.
  for (const file of globSync('**/.env*', { cwd: site })) rmSync(path.join(site, file));
  execFileSync(python, ['-c', 'import ssl, certifi, dotenv, symphony_server, core_ai.voice; print("Bundled Python imports OK")'], { stdio: 'inherit', env: { ...process.env, PYTHONHOME: runtime } });
  writeFileSync(stamp, key);
} finally {
  rmSync(temp, { recursive: true, force: true });
}
