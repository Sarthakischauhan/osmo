// electron-builder afterPack hook: VMP-sign the packed castLabs ECS app so Widevine plays protected music (Spotify).
//
// Widevine's Verified Media Path wants the app signed by castLabs EVS. electron-builder runs afterPack once the app is
// laid out and before it code-signs it (codesign on macOS, signtool on Windows), which is the order VMP needs: the VMP
// signature goes in first, the platform signature seals it. Linux has no VMP, so there is nothing to do there.
//
// Needs the castlabs-evs CLI (`pip install --upgrade castlabs-evs`) and an EVS account signed in on this machine
// (`python3 -m castlabs_evs.account reauth`); EVS keeps its tokens in its own config, outside the repository, and
// nothing here reads or prints them. OSMO_VMP_SIGN=skip packs without the signature (a local test build: Widevine
// then refuses some streams), and OSMO_VMP_PYTHON picks the Python that has castlabs-evs.
'use strict';

const { spawnSync } = require('node:child_process');

/** The command that VMP-signs `appOutDir` for `platform` (an electron-builder platform name), or why it is skipped. */
function vmpCommand(platform, appOutDir, env) {
  if (platform === 'linux') return { skip: 'Linux has no VMP signing' };
  if (env.OSMO_VMP_SIGN === 'skip') return { skip: 'OSMO_VMP_SIGN=skip: packing without a VMP signature, Widevine may refuse protected streams' };
  return { command: env.OSMO_VMP_PYTHON || 'python3', args: ['-m', 'castlabs_evs.vmp', 'sign-pkg', appOutDir] };
}

/** Run a VMP signing command, failing the build (so no unsigned app ships by accident) if it does not succeed. */
function signWith(step, run = spawnSync, log = console.log) {
  if ('skip' in step) {
    log(`VMP signing skipped: ${step.skip}`);
    return;
  }
  const result = run(step.command, step.args, { stdio: 'inherit' });
  if (result.error) throw new Error(`VMP signing could not start ${step.command}: ${result.error.message}. Install castlabs-evs (pip install castlabs-evs) or set OSMO_VMP_SIGN=skip for a local build.`);
  if (result.status !== 0) throw new Error(`VMP signing failed (exit ${result.status}). Sign in to EVS with "python3 -m castlabs_evs.account reauth" and pack again.`);
  log('VMP signed');
}

async function afterPack(context) {
  signWith(vmpCommand(context.electronPlatformName, context.appOutDir, process.env));
}

module.exports = afterPack;
module.exports.default = afterPack;
module.exports.vmpCommand = vmpCommand;
module.exports.signWith = signWith;
