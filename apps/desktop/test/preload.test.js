import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { CHANNELS } from '../src/session-contract.js';

// Loads preload.cjs against a stand-in for `electron`, and records what a page could call.
function load() {
  const invoked = [];
  const listening = new Map();
  const exposed = {};
  const electron = {
    contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
    ipcRenderer: {
      invoke: (channel, ...args) => { invoked.push([channel, ...args]); return Promise.resolve(); },
      on: (channel, listener) => listening.set(channel, listener),
      removeListener: (channel, listener) => { if (listening.get(channel) === listener) listening.delete(channel); },
    },
  };
  const original = Module._load;
  Module._load = (request, ...rest) => (request === 'electron' ? electron : original(request, ...rest));
  try {
    const require = createRequire(import.meta.url);
    delete require.cache[require.resolve('../src/preload.cjs')];
    require('../src/preload.cjs');
  } finally { Module._load = original; }
  return { api: exposed.osmoDesktop, invoked, listening };
}

test('the page gets one object, and it holds no way to execute desktop actions', () => {
  const { api } = load();
  assert.deepEqual(Object.keys(api).sort(), ['onStatus', 'orb', 'session', 'setEnabled', 'status']);
  assert.equal('execute' in api, false);
});

test('each session call goes out on its contract channel', async () => {
  const { api, invoked } = load();
  await api.session.snapshot();
  await api.session.start({ voice: 'eve' });
  await api.session.end();
  await api.session.interrupt();
  await api.session.send({ type: 'browser.close', id: 'b' });
  await api.orb.showFull();
  await api.orb.setBox(120);
  assert.deepEqual(invoked, [
    [CHANNELS.snapshot], [CHANNELS.start, { voice: 'eve' }], [CHANNELS.end], [CHANNELS.interrupt],
    [CHANNELS.send, { type: 'browser.close', id: 'b' }], [CHANNELS.showFull], [CHANNELS.box, 120],
  ]);
});

test('events and status arrive on the contract channels, and each subscription can be dropped', () => {
  const { api, listening } = load();
  const seen = [];
  const dropEvent = api.session.onEvent(event => seen.push(['event', event]));
  const dropStatus = api.session.onStatus(snapshot => seen.push(['status', snapshot]));
  listening.get(CHANNELS.event)({}, { type: 'state' });
  listening.get(CHANNELS.status)({}, { connection: 'connected' });
  assert.deepEqual(seen, [['event', { type: 'state' }], ['status', { connection: 'connected' }]]);
  dropEvent();
  dropStatus();
  assert.equal(listening.has(CHANNELS.event) || listening.has(CHANNELS.status), false);
});
