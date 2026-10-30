import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { CHANNELS } from '../src/session-contract.js';

// Loads preload.cjs against a stand-in for `electron`, and records what a page could call.
function load(file = 'preload.cjs', name = 'osmoDesktop') {
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
    delete require.cache[require.resolve(`../src/${file}`)];
    require(`../src/${file}`);
  } finally { Module._load = original; }
  return { api: exposed[name], exposed, invoked, listening };
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
  await api.session.openThread('t1');
  await api.orb.showFull();
  await api.orb.setBox(120);
  assert.deepEqual(invoked, [
    [CHANNELS.snapshot], [CHANNELS.start, { voice: 'eve' }], [CHANNELS.end], [CHANNELS.interrupt],
    [CHANNELS.send, { type: 'browser.close', id: 'b' }], [CHANNELS.openThread, 't1'], [CHANNELS.showFull], [CHANNELS.box, 120],
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

test('the audio renderer gets its own object, and nothing of the session or the windows', () => {
  const { api, exposed } = load('audio-preload.cjs', 'osmoAudio');
  assert.deepEqual(Object.keys(exposed), ['osmoAudio']);
  assert.deepEqual(Object.keys(api).sort(), ['frame', 'onFlush', 'onPlay', 'onStart', 'onStop', 'ready', 'started']);
});

test('what the audio renderer sends goes out on the contract channels', async () => {
  const { api, invoked } = load('audio-preload.cjs', 'osmoAudio');
  const frame = new ArrayBuffer(4800);
  await api.ready();
  await api.started({});
  await api.started({ error: 'Permission denied' });
  await api.frame(frame);
  assert.deepEqual(invoked, [[CHANNELS.audioReady], [CHANNELS.audioStarted, {}], [CHANNELS.audioStarted, { error: 'Permission denied' }], [CHANNELS.audioFrame, frame]]);
});

test('what main asks of the audio renderer arrives on the contract channels, and each subscription can be dropped', () => {
  const { api, listening } = load('audio-preload.cjs', 'osmoAudio');
  const seen = [];
  const drops = [
    api.onStart(() => seen.push('start')),
    api.onStop(() => seen.push('stop')),
    api.onPlay(pcm => seen.push(['play', pcm.byteLength])),
    api.onFlush(() => seen.push('flush')),
  ];
  listening.get(CHANNELS.audioStart)({}, undefined);
  listening.get(CHANNELS.audioPlay)({}, new ArrayBuffer(960));
  listening.get(CHANNELS.audioFlush)({}, undefined);
  listening.get(CHANNELS.audioStop)({}, undefined);
  assert.deepEqual(seen, ['start', ['play', 960], 'flush', 'stop']);
  for (const drop of drops) drop();
  assert.equal(listening.size, 0);
});
