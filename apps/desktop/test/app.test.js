import { test, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { fake } from './support/electron-stub.js';
import { CHANNELS } from '../src/session-contract.js';
import { SWITCH_ACCELERATOR } from '../src/window-mode.js';

// src/app.js against a stand-in for Electron: the windows, the session in main, and what each window may ask of it.
const ORIGIN = 'http://127.0.0.1:18787';
process.env.OSMO_DESKTOP_URL = ORIGIN;
register('./support/electron-hooks.js', import.meta.url);

class FakeSocket {
  static all = [];
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; FakeSocket.all.push(this); }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; this.closed = true; queueMicrotask(() => this.onclose?.({})); }
  open() { this.readyState = 1; this.onopen?.({}); }
  message(data) { this.onmessage?.({ data }); }
  json() { return this.sent.filter(item => typeof item === 'string').map(item => JSON.parse(item)); }
}
globalThis.WebSocket = FakeSocket;

const { runApp } = await import('../src/app.js');
const settle = () => new Promise(resolve => setImmediate(resolve));

const audioWindow = () => fake.windows.find(win => win.url === `${ORIGIN}/#audio`);
const workspace = () => fake.windows.find(win => win.url === ORIGIN);
const orb = () => fake.windows.find(win => win.url === `${ORIGIN}/#orb`);
const asked = (win, channel) => win.webContents.sent.filter(([name]) => name === channel).map(([, payload]) => payload);

/** What Electron hands a handler: the window's contents and its main frame. */
const from = win => ({ sender: win.webContents, senderFrame: win.webContents.mainFrame });
const call = (win, channel, ...args) => fake.handlers.get(channel)(from(win), ...args);

before(() => { runApp(); });
beforeEach(async () => { await settle(); });

test('the app starts in the orb: the always-on-top orb is showing, the workspace and the audio renderer are not', () => {
  assert.equal(workspace().options.title, 'Osmo');
  assert.equal(orb().visible, true);
  assert.equal(workspace().visible, false);
  assert.equal(audioWindow().visible, false);
  assert.equal(orb().options.alwaysOnTop, true);
  assert.deepEqual(orb().bounds, { x: 1600 - 24 - 112, y: 900 - 24 - 112, width: 112, height: 112 });
});

test('the orb, the workspace and the audio renderer have different bridges, and only the audio renderer has the microphone', () => {
  assert.match(audioWindow().options.webPreferences.preload, /audio-preload\.cjs$/);
  assert.match(workspace().options.webPreferences.preload, /[\\/]preload\.cjs$/);
  assert.equal(orb().options.webPreferences.preload, workspace().options.webPreferences.preload);
  assert.equal(audioWindow().options.webPreferences.autoplayPolicy, 'no-user-gesture-required');
  assert.equal(audioWindow().options.webPreferences.sandbox, true);
});

test('the microphone is allowed for the audio renderer\'s own page and for no other window', () => {
  const mic = { isMainFrame: true, mediaType: 'audio', securityOrigin: ORIGIN };
  const session = audioWindow().webContents.session;
  assert.equal(session.check(audioWindow().webContents, 'media', ORIGIN, mic), true);
  assert.equal(session.check(workspace().webContents, 'media', ORIGIN, mic), false);
  assert.equal(session.check(orb().webContents, 'media', ORIGIN, mic), false);
  const answers = [];
  session.request(audioWindow().webContents, 'media', allowed => answers.push(allowed), mic);
  session.request(orb().webContents, 'media', allowed => answers.push(allowed), mic);
  session.request(audioWindow().webContents, 'geolocation', allowed => answers.push(allowed), mic);
  assert.deepEqual(answers, [true, false, false]);
});

test('double-clicking the orb shows the workspace and hides the orb; the shortcut goes back and forth', async () => {
  await call(orb(), CHANNELS.showFull);
  assert.deepEqual([workspace().visible, orb().visible, workspace().focused], [true, false, true]);
  fake.shortcuts.get(SWITCH_ACCELERATOR)();
  assert.deepEqual([workspace().visible, orb().visible], [false, true]);
  fake.shortcuts.get(SWITCH_ACCELERATOR)();
  assert.deepEqual([workspace().visible, orb().visible], [true, false]);
});

test('minimizing the workspace shows the orb', () => {
  workspace().emit('minimize');
  assert.deepEqual([workspace().visible, orb().visible], [false, true]);
});

test('closing the workspace hides it to the orb instead of closing it, until the app quits', async () => {
  await call(orb(), CHANNELS.showFull);
  let prevented = 0;
  workspace().emit('close', { preventDefault: () => prevented++ });
  assert.equal(prevented, 1);
  assert.deepEqual([workspace().visible, orb().visible], [false, true]);
});

test('each window may use only its own channels', async () => {
  await assert.rejects(async () => call(workspace(), CHANNELS.showFull), /Untrusted/);
  await assert.rejects(async () => call(orb(), CHANNELS.openThread, 't1'), /Untrusted/);
  await assert.rejects(async () => call(orb(), CHANNELS.audioFrame, new ArrayBuffer(8)), /Untrusted/);
  await assert.rejects(async () => call(workspace(), CHANNELS.audioStarted, {}), /Untrusted/);
  await assert.rejects(async () => call(audioWindow(), CHANNELS.start, {}), /Untrusted/);
  await assert.rejects(async () => call(audioWindow(), CHANNELS.send, { type: 'browser.close', id: 'b' }), /Untrusted/);
  await assert.rejects(async () => fake.handlers.get(CHANNELS.audioReady)({ sender: orb().webContents, senderFrame: {} }), /Untrusted/);
});

test('a start before the audio renderer is up fails with why, and closes the socket it opened', async () => {
  const started = call(orb(), CHANNELS.start, {});
  await settle();
  const socket = FakeSocket.all.at(-1);
  socket.open();
  const snapshot = await started;
  assert.match(snapshot.error, /audio renderer is not running/);
  assert.equal(snapshot.connection, 'disconnected');
  assert.equal(socket.closed, true);
});

test('a call from the orb continues the workspace\'s open thread, with the mic and speakers in the audio renderer, and survives switching windows', async () => {
  await call(audioWindow(), CHANNELS.audioReady);
  await call(workspace(), CHANNELS.openThread, 'open-thread');
  const earlier = { starts: asked(audioWindow(), CHANNELS.audioStart).length, flushes: asked(audioWindow(), CHANNELS.audioFlush).length, stops: asked(audioWindow(), CHANNELS.audioStop).length };

  const started = call(orb(), CHANNELS.start, { voice: 'eve' });
  await settle();
  const sockets = FakeSocket.all.length;
  const socket = FakeSocket.all.at(-1);
  socket.open();
  await settle();
  // The socket is in main, the session asked to continue the open thread, and the microphone was asked for.
  assert.deepEqual(socket.json(), [{ type: 'session.start', voice: 'eve', threadId: 'open-thread' }]);
  assert.equal(asked(audioWindow(), CHANNELS.audioStart).length, earlier.starts + 1);
  await call(audioWindow(), CHANNELS.audioStarted, {});
  assert.equal((await started).connection, 'connected');

  // Microphone frames go from the audio renderer, through main, out the socket.
  const frame = new Uint8Array(4800);
  await call(audioWindow(), CHANNELS.audioFrame, frame);
  assert.deepEqual(socket.sent.slice(-1), [frame]);
  await assert.rejects(async () => call(audioWindow(), CHANNELS.audioFrame, new ArrayBuffer(3)), /Invalid audio frame/);

  // Speech comes back the other way, and a barge-in flushes it.
  const speech = new ArrayBuffer(960);
  socket.message(speech);
  assert.deepEqual(asked(audioWindow(), CHANNELS.audioPlay), [speech]);
  socket.message(JSON.stringify({ type: 'state', state: 'listening', detail: 'barge-in' }));
  assert.equal(asked(audioWindow(), CHANNELS.audioFlush).length, earlier.flushes + 1);

  // Switching windows changes who is showing, not the call.
  const flushes = asked(audioWindow(), CHANNELS.audioFlush).length;
  await call(orb(), CHANNELS.showFull);
  fake.shortcuts.get(SWITCH_ACCELERATOR)();
  await call(orb(), CHANNELS.showFull);
  assert.equal(socket.closed, undefined);
  assert.equal(FakeSocket.all.length, sockets);
  assert.equal(asked(audioWindow(), CHANNELS.audioStop).length, earlier.stops);
  const next = new Uint8Array(4800);
  await call(audioWindow(), CHANNELS.audioFrame, next);
  assert.deepEqual(socket.sent.slice(-1), [next]);
  assert.equal(asked(audioWindow(), CHANNELS.audioFlush).length, flushes);

  // Both windows are told what the session says.
  socket.message(JSON.stringify({ type: 'state', state: 'speaking' }));
  assert.equal(asked(workspace(), CHANNELS.event).at(-1).state, 'speaking');
  assert.equal(asked(orb(), CHANNELS.event).at(-1).state, 'speaking');
});

test('ending the call closes the socket and stops the microphone', async () => {
  const before = asked(audioWindow(), CHANNELS.audioStop).length;
  await call(workspace(), CHANNELS.end);
  assert.equal(FakeSocket.all.at(-1).closed, true);
  assert.equal(asked(audioWindow(), CHANNELS.audioStop).length, before + 1);
});

test('a crashed audio renderer ends the call and is reloaded', async () => {
  const started = call(orb(), CHANNELS.start, {});
  await settle();
  const socket = FakeSocket.all.at(-1);
  socket.open();
  await settle();
  await call(audioWindow(), CHANNELS.audioStarted, {});
  await started;
  audioWindow().webContents.emit('render-process-gone');
  assert.equal(socket.closed, true);
  assert.equal(audioWindow().reloads, 1);
  const again = call(orb(), CHANNELS.start, {});
  await settle();
  FakeSocket.all.at(-1).open();
  assert.match((await again).error, /audio renderer is not running/);
});

test('quitting lets the workspace close', () => {
  fake.appListeners.get('before-quit')[0]();
  let prevented = 0;
  workspace().emit('close', { preventDefault: () => prevented++ });
  assert.equal(prevented, 0);
});
