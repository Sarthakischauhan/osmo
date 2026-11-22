import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const { vmpCommand, signWith } = require('../scripts/vmp-sign.cjs');
const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

test('the app is packaged on castLabs ECS with Widevine, and afterPack VMP-signs it before code signing', () => {
  assert.equal(pkg.devDependencies.electron, 'github:castlabs/electron-releases#v45.2.1+wvcus');
  assert.equal(pkg.build.electronVersion, '45.2.1+wvcus');
  assert.equal(pkg.build.electronDownload.mirror, 'https://github.com/castlabs/electron-releases/releases/download/v');
  assert.equal(pkg.build.afterPack, 'scripts/vmp-sign.cjs');
});

test('macOS and Windows are signed with the EVS CLI on the packed app; Linux has no VMP', () => {
  assert.deepEqual(vmpCommand('darwin', '/out/mac-arm64', {}), { command: 'python3', args: ['-m', 'castlabs_evs.vmp', 'sign-pkg', '/out/mac-arm64'] });
  assert.deepEqual(vmpCommand('win32', 'C:\\out\\win-unpacked', { OSMO_VMP_PYTHON: 'py' }).command, 'py');
  assert.match(vmpCommand('linux', '/out/linux-unpacked', {}).skip, /Linux/);
  assert.match(vmpCommand('darwin', '/out', { OSMO_VMP_SIGN: 'skip' }).skip, /without a VMP signature/);
});

test('a failed or missing signer fails the build; a skip only logs', () => {
  const logged = [];
  const log = line => logged.push(line);
  const step = vmpCommand('darwin', '/out', {});
  const calls = [];
  signWith(step, (command, args) => (calls.push([command, ...args]), { status: 0 }), log);
  assert.deepEqual(calls, [['python3', '-m', 'castlabs_evs.vmp', 'sign-pkg', '/out']]);
  assert.throws(() => signWith(step, () => ({ status: 1 }), log), /VMP signing failed .*reauth/);
  assert.throws(() => signWith(step, () => ({ error: new Error('ENOENT') }), log), /could not start python3/);
  signWith(vmpCommand('linux', '/out', {}), () => assert.fail('nothing runs on Linux'), log);
  assert.deepEqual(logged, ['VMP signed', 'VMP signing skipped: Linux has no VMP signing']);
});
