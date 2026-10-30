import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, openSync, readFileSync, readSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Signs the bundled Python's binaries once per runtime, so electron-builder (`mac.signIgnore`) can skip its thousands
// of files on every build. prepare-python.js removes the runtime, and with it this stamp, whenever it rebuilds it.
// See docs/desktop-signing.md.
if (process.platform !== 'darwin') process.exit(0);
const runtime = fileURLToPath(new URL('../build/python', import.meta.url));
const stamp = path.join(runtime, '.osmo-signed');
const name = process.env.CSC_NAME || 'Osmo Dev';
const line = execFileSync('security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' })
  .split('\n').find(it => it.includes(`"${name}"`));
if (!line) {
  console.warn(`No code signing identity "${name}": the bundled Python stays unsigned. Run \`bun run signing:create\`.`);
  process.exit(0);
}
const identity = line.trim().split(/\s+/)[1];
if (existsSync(stamp) && readFileSync(stamp, 'utf8') === identity) {
  console.log(`Bundled Python is already signed by "${name}"`);
  process.exit(0);
}

// Mach-O and universal binaries, by their first four bytes; everything else is sealed as a resource of the app.
const MAGIC = new Set([0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe]);
function binary(file) {
  const fd = openSync(file, 'r');
  try {
    const head = Buffer.alloc(4);
    return readSync(fd, head, 0, 4, 0) === 4 && MAGIC.has(head.readUInt32BE(0));
  } finally {
    closeSync(fd);
  }
}
function binaries(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) return binaries(file);
    return entry.isFile() && binary(file) ? [file] : [];
  });
}

const files = binaries(runtime);
// One codesign run for all of them. No secure timestamp, as for the app (`mac.timestamp`).
execFileSync('codesign', ['--force', '--sign', identity, '--timestamp=none', ...files], { stdio: 'inherit' });
writeFileSync(stamp, identity);
console.log(`Signed ${files.length} bundled Python binaries with "${name}"`);
