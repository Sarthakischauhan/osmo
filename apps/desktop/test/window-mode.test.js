import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SWITCH_ACCELERATOR, createWindowSwitch } from '../src/window-mode.js';

function windows({ minimized = false } = {}) {
  const calls = [];
  const full = { isMinimized: () => minimized, restore: () => calls.push('full.restore'), show: () => calls.push('full.show'), focus: () => calls.push('full.focus'), hide: () => calls.push('full.hide') };
  const orb = { show: () => calls.push('orb.show'), hide: () => calls.push('orb.hide') };
  return { calls, full, orb };
}

test('the shortcut is Cmd/Ctrl+Shift+O', () => {
  assert.equal(SWITCH_ACCELERATOR, 'CommandOrControl+Shift+O');
});

test('minimizing the workspace hides it and shows the orb', () => {
  const { calls, full, orb } = windows();
  const windowSwitch = createWindowSwitch({ full, orb });
  windowSwitch.toOrb();
  assert.deepEqual(calls, ['full.hide', 'orb.show']);
  assert.equal(windowSwitch.mode, 'orb');
});

test('double-clicking the orb hides it and shows and focuses the workspace', () => {
  const { calls, full, orb } = windows();
  const windowSwitch = createWindowSwitch({ full, orb });
  windowSwitch.toFull();
  assert.deepEqual(calls, ['orb.hide', 'full.show', 'full.focus']);
  assert.equal(windowSwitch.mode, 'full');
});

test('a workspace that was hidden while minimized is restored before it is shown', () => {
  const { calls, full, orb } = windows({ minimized: true });
  createWindowSwitch({ full, orb }).toFull();
  assert.deepEqual(calls, ['orb.hide', 'full.restore', 'full.show', 'full.focus']);
});

test('the shortcut toggles: orb to workspace and back', () => {
  const { calls, full, orb } = windows();
  const windowSwitch = createWindowSwitch({ full, orb });
  windowSwitch.toOrb();
  calls.length = 0;
  windowSwitch.toggle();
  assert.equal(windowSwitch.mode, 'full');
  assert.deepEqual(calls, ['orb.hide', 'full.show', 'full.focus']);
  calls.length = 0;
  windowSwitch.toggle();
  assert.equal(windowSwitch.mode, 'orb');
  assert.deepEqual(calls, ['full.hide', 'orb.show']);
});
