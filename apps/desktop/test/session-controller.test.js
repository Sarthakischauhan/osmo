import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AUDIO_UNAVAILABLE, createSessionController, emptySnapshot, reduceSnapshot } from '../src/session-controller.js';

class FakeSocket {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.closed = false; }
  send(data) { this.sent.push(data); }
  close() { this.closed = true; this.readyState = 3; queueMicrotask(() => this.onclose?.({})); }
  open() { this.readyState = 1; this.onopen?.({}); }
  message(data) { this.onmessage?.({ data: typeof data === 'string' ? data : data }); }
  json(event) { this.message(JSON.stringify(event)); }
  sentJson() { return this.sent.filter(item => typeof item === 'string').map(item => JSON.parse(item)); }
}

function fixture({ withAudio = true, execute = async () => ({ ok: true }) } = {}) {
  const sockets = [];
  const log = { events: [], statuses: [], audio: [], disconnected: 0 };
  let onFrame;
  const audio = withAudio ? {
    start: async callback => { onFrame = callback; log.audio.push('start'); },
    stop: () => log.audio.push('stop'),
    play: pcm => log.audio.push(['play', pcm]),
    flush: () => log.audio.push('flush'),
  } : undefined;
  const controller = createSessionController({
    url: 'ws://127.0.0.1:8080/ws',
    createSocket: url => { const socket = new FakeSocket(url); sockets.push(socket); return socket; },
    audio,
    desktop: { execute },
    onEvent: event => log.events.push(event),
    onStatus: snapshot => log.statuses.push(snapshot),
    onDisconnected: () => { log.disconnected++; },
  });
  return { controller, sockets, log, frame: pcm => onFrame(pcm) };
}

const connected = async ({ options = {}, ...rest } = {}) => {
  const fx = fixture(rest);
  const started = fx.controller.start(options);
  fx.sockets[0].open();
  await started;
  return fx;
};

test('start connects, sends session.start with the pick, and begins capture', async () => {
  const { controller, sockets, log } = await connected({ options: { voice: 'eve', threadId: 't1' } });
  assert.equal(sockets[0].url, 'ws://127.0.0.1:8080/ws');
  assert.equal(sockets[0].binaryType, 'arraybuffer');
  assert.deepEqual(sockets[0].sentJson(), [{ type: 'session.start', voice: 'eve', threadId: 't1' }]);
  assert.deepEqual(log.audio, ['start']);
  assert.equal(controller.snapshot().connection, 'connected');
  assert.ok(controller.snapshot().startedAt > 0);
  assert.deepEqual(log.statuses.map(item => item.connection), ['connecting', 'connected']);
});

test('microphone frames go to the socket as binary', async () => {
  const { sockets, frame } = await connected();
  const pcm = new Int16Array([1, 2, 3]).buffer;
  frame(pcm);
  assert.equal(sockets[0].sent.at(-1), pcm);
});

test('without an audio mechanism start reports why and opens nothing', async () => {
  const { controller, sockets } = fixture({ withAudio: false });
  const snapshot = await controller.start({});
  assert.equal(sockets.length, 0);
  assert.equal(snapshot.connection, 'disconnected');
  assert.equal(snapshot.error, AUDIO_UNAVAILABLE);
});

test('there is one session: starting again, from either window, opens no second socket', async () => {
  const fx = fixture();
  const first = fx.controller.start({});
  const second = fx.controller.start({});
  fx.sockets[0].open();
  await Promise.all([first, second]);
  await fx.controller.start({});
  assert.equal(fx.sockets.length, 1);
  assert.equal(fx.sockets[0].sentJson().filter(event => event.type === 'session.start').length, 1);
});

test('events are forwarded to the windows and folded into the snapshot', async () => {
  const { controller, sockets, log } = await connected();
  const thread = { type: 'thread', thread: { id: 't9' } };
  sockets[0].json({ type: 'state', state: 'listening' });
  sockets[0].json(thread);
  sockets[0].json({ type: 'agent.update', run: { id: 'r' } });
  assert.deepEqual(log.events.map(event => event.type), ['state', 'thread', 'agent.update']);
  assert.equal(controller.snapshot().state, 'listening');
  assert.deepEqual(controller.snapshot().thread, { id: 't9' });
});

test('binary replies play; a barge-in drops queued audio and is still shown to the windows', async () => {
  const { sockets, log } = await connected();
  const pcm = new ArrayBuffer(4);
  sockets[0].message(pcm);
  sockets[0].json({ type: 'state', state: 'listening', detail: 'barge-in' });
  assert.deepEqual(log.audio, ['start', ['play', pcm], 'flush']);
  assert.equal(log.events.at(-1).detail, 'barge-in');
});

test('malformed frames are ignored', async () => {
  const { sockets, log } = await connected();
  sockets[0].message('not json');
  sockets[0].message('42');
  sockets[0].message('{"no":"type"}');
  assert.deepEqual(log.events, []);
});

test('desktop.request is answered from main and never reaches the windows', async () => {
  const seen = [];
  const { sockets, log } = await connected({ execute: async action => { seen.push(action); return { ok: true }; } });
  sockets[0].json({ type: 'desktop.request', requestId: 'r1', action: { action: 'screenshot' } });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(seen, [{ action: 'screenshot' }]);
  assert.deepEqual(sockets[0].sentJson().at(-1), { type: 'desktop.result', requestId: 'r1', result: { ok: true } });
  assert.deepEqual(log.events, []);
});

test('a failed desktop action is reported to Symphony as an error', async () => {
  const { sockets } = await connected({ execute: async () => { throw new Error('Computer support is disabled'); } });
  sockets[0].json({ type: 'desktop.request', requestId: 'r2', action: {} });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(sockets[0].sentJson().at(-1), { type: 'desktop.result', requestId: 'r2', error: 'Computer support is disabled' });
});

test('a desktop result for an ended session is not sent to the next one', async () => {
  let finish;
  const { controller, sockets } = await connected({ execute: () => new Promise(resolve => { finish = resolve; }) });
  sockets[0].json({ type: 'desktop.request', requestId: 'r3', action: {} });
  controller.end();
  finish({ ok: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sockets[0].sentJson().some(event => event.type === 'desktop.result'), false);
});

test('computer support consent is told to Symphony when it changes and on the next session', async () => {
  const { controller, sockets } = await connected();
  controller.setDesktopEnabled(true);
  controller.setDesktopEnabled(false);
  assert.deepEqual(sockets[0].sentJson().slice(1), [{ type: 'desktop.configure', enabled: true }, { type: 'desktop.configure', enabled: false }]);
  controller.setDesktopEnabled(true);
  controller.end();
  const next = controller.start({});
  await Promise.resolve();
  const fresh = sockets[1];
  fresh.open();
  await next;
  assert.deepEqual(fresh.sentJson(), [{ type: 'session.start' }, { type: 'desktop.configure', enabled: true }]);
});

test('interrupt and widget actions flush playback and reach Symphony', async () => {
  const { controller, sockets, log } = await connected();
  controller.interrupt();
  controller.send({ type: 'widget.action', id: 'w', action: 'go' });
  controller.send({ type: 'browser.close', id: 'b' });
  assert.deepEqual(sockets[0].sentJson().slice(1), [{ type: 'interrupt' }, { type: 'widget.action', id: 'w', action: 'go' }, { type: 'browser.close', id: 'b' }]);
  assert.deepEqual(log.audio, ['start', 'flush', 'flush']);
});

test('end tells Symphony, stops audio, closes the socket and revokes computer support', async () => {
  const { controller, sockets, log } = await connected();
  controller.end();
  assert.deepEqual(sockets[0].sentJson().at(-1), { type: 'session.end' });
  assert.equal(sockets[0].closed, true);
  assert.deepEqual(log.audio.slice(1), ['stop', 'flush']);
  assert.equal(controller.snapshot().connection, 'disconnected');
  assert.equal(log.disconnected, 1);
  await Promise.resolve();
  assert.equal(log.disconnected, 1);
});

test('the server closing the connection ends the session', async () => {
  const { controller, sockets, log } = await connected();
  sockets[0].json({ type: 'state', state: 'speaking' });
  sockets[0].close();
  await Promise.resolve();
  assert.equal(controller.snapshot().connection, 'disconnected');
  assert.equal(controller.snapshot().state, 'idle');
  assert.equal(log.disconnected, 1);
  assert.ok(log.audio.includes('stop'));
});

test('a connection that fails reports the error and can be retried', async () => {
  const fx = fixture();
  const started = fx.controller.start({});
  fx.sockets[0].onerror({});
  const snapshot = await started;
  assert.equal(snapshot.connection, 'disconnected');
  assert.match(snapshot.error, /could not connect/);
  const retry = fx.controller.start({});
  fx.sockets[1].open();
  assert.equal((await retry).connection, 'connected');
  assert.equal(fx.controller.snapshot().error, undefined);
});

test('a capture that fails to start ends the session with that error', async () => {
  const sockets = [];
  const controller = createSessionController({
    url: 'ws://x/ws', createSocket: () => { const socket = new FakeSocket(); sockets.push(socket); return socket; },
    audio: { start: async () => { throw new Error('no microphone'); }, stop() {}, play() {}, flush() {} },
    desktop: { execute: async () => ({}) }, onEvent() {}, onStatus() {},
  });
  const started = controller.start({});
  sockets[0].open();
  const snapshot = await started;
  assert.equal(snapshot.error, 'no microphone');
  assert.equal(snapshot.connection, 'disconnected');
  assert.equal(sockets[0].closed, true);
});

test('ending while connecting settles the start and leaves no session', async () => {
  const fx = fixture();
  const started = fx.controller.start({});
  fx.controller.end();
  const snapshot = await started;
  assert.equal(snapshot.connection, 'disconnected');
  assert.equal(fx.sockets.length, 1);
});

test('events from a socket that is no longer the session are dropped', async () => {
  const { controller, sockets, log } = await connected();
  controller.end();
  sockets[0].json({ type: 'state', state: 'speaking' });
  assert.deepEqual(log.events, []);
});

test('the snapshot keeps state, thread, registry and confirmed pick', () => {
  const start = emptySnapshot();
  assert.equal(reduceSnapshot(start, { type: 'agent.update' }), start);
  const models = reduceSnapshot(start, { type: 'models', models: [{ id: 'm' }], selection: { model: 'm' } });
  assert.deepEqual([models.models, models.selection], [[{ id: 'm' }], { model: 'm' }]);
  assert.deepEqual(reduceSnapshot(models, { type: 'voice.configured', selection: { model: 'n' } }).selection, { model: 'n' });
  assert.equal(reduceSnapshot(start, { type: 'state', state: 'thinking', detail: 'barge-in' }).detail, undefined);
});
