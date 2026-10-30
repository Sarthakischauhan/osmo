// The one IPC contract between Electron main (which owns the voice session) and its renderers: the workspace and the
// orb window, which ask for the session, and the hidden audio renderer, which is main's microphone and speakers. Channel
// names, and what main accepts from a renderer: nothing else crosses, and main validates every payload, so a window can
// ask for a session but never reach the socket, the mic or the computer-support bridge.
// The preloads are sandboxed and cannot import this file, so they repeat the names; test/preload.test.js keeps them equal.

import { BOX_MAX_HEIGHT } from './window-state.js';

export const CHANNELS = Object.freeze({
  // renderer -> main (invoke)
  snapshot: 'session:snapshot',
  start: 'session:start',
  end: 'session:end',
  interrupt: 'session:interrupt',
  send: 'session:send',
  showFull: 'window:show-full',
  box: 'orb:box',
  openThread: 'workspace:thread',
  // main -> windows (send)
  event: 'session:event',
  status: 'session:status',
  // audio renderer -> main (send)
  audioReady: 'audio:ready',
  audioStarted: 'audio:started',
  audioFrame: 'audio:frame',
  // main -> audio renderer (send)
  audioStart: 'audio:start',
  audioStop: 'audio:stop',
  audioPlay: 'audio:play',
  audioFlush: 'audio:flush',
});

/**
 * What a renderer sees of the session.
 * @typedef {object} SessionSnapshot
 * @property {'disconnected' | 'connecting' | 'connected'} connection
 * @property {string} state the server's `state` event: idle, listening, thinking, speaking or working
 * @property {string | undefined} detail
 * @property {string | undefined} error why the last start failed
 * @property {number | undefined} startedAt ms
 * @property {{ id: string }| undefined} thread the stored thread the session reads and writes
 * @property {unknown} selection the model, voice and effort the server last confirmed
 * @property {unknown[]} models the server's voice model registry
 */

/**
 * @typedef {object} StartOptions
 * @property {string} [voice]
 * @property {string} [model]
 * @property {string} [effort]
 * @property {string} [threadId]
 */

const EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'];
const isText = value => typeof value === 'string' && value.length > 0;
const isFraction = value => typeof value === 'number' && value >= 0 && value <= 1;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function onlyKeys(value, keys, what) {
  if (!plain(value)) throw new Error(`Invalid ${what}`);
  if (Object.keys(value).some(key => !keys.includes(key))) throw new Error(`Unexpected field in ${what}`);
}

/** @returns {StartOptions} */
export function validateStart(value) {
  if (value === undefined) return {};
  onlyKeys(value, ['voice', 'model', 'effort', 'threadId'], 'session start');
  for (const key of ['voice', 'model', 'threadId']) if (value[key] !== undefined && !isText(value[key])) throw new Error(`Invalid ${key}`);
  if (value.effort !== undefined && !EFFORTS.includes(value.effort)) throw new Error('Invalid effort');
  return value;
}

/**
 * The events a renderer may send on to Symphony. Session start, end, interrupt and the computer-support messages are
 * main's own and have channels or none; voice previews need the speakers, which main does not have.
 * @returns {Record<string, unknown> & { type: string }}
 */
export function validateClientEvent(value) {
  if (!plain(value)) throw new Error('Invalid session event');
  switch (value.type) {
    case 'widget.action':
      onlyKeys(value, ['type', 'id', 'action', 'value'], 'widget action');
      if (!isText(value.id) || !isText(value.action) || (value.value !== undefined && typeof value.value !== 'string')) throw new Error('Invalid widget action');
      break;
    case 'session.configure':
      onlyKeys(value, ['type', 'model', 'voice', 'effort'], 'session configure');
      if (!isText(value.model) || !isText(value.voice) || (value.effort !== undefined && !EFFORTS.includes(value.effort))) throw new Error('Invalid session configure');
      break;
    case 'browser.click':
      onlyKeys(value, ['type', 'id', 'x', 'y'], 'browser click');
      if (!isText(value.id) || !isFraction(value.x) || !isFraction(value.y)) throw new Error('Invalid browser click');
      break;
    case 'browser.scroll':
      onlyKeys(value, ['type', 'id', 'x', 'y', 'deltaX', 'deltaY'], 'browser scroll');
      if (!isText(value.id) || !isFraction(value.x) || !isFraction(value.y) || !Number.isFinite(value.deltaX) || !Number.isFinite(value.deltaY)) throw new Error('Invalid browser scroll');
      break;
    case 'browser.key':
      onlyKeys(value, ['type', 'id', 'key', 'code', 'keyCode'], 'browser key');
      if (!isText(value.id) || !isText(value.key) || typeof value.code !== 'string' || !Number.isInteger(value.keyCode) || value.keyCode < 0) throw new Error('Invalid browser key');
      break;
    case 'browser.close':
      onlyKeys(value, ['type', 'id'], 'browser close');
      if (!isText(value.id)) throw new Error('Invalid browser close');
      break;
    default:
      throw new Error('Unsupported session event');
  }
  return value;
}

/** The orb window's hover box: how tall its content is (0 = closed), at most `BOX_MAX_HEIGHT`. */
export function validateBoxHeight(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > BOX_MAX_HEIGHT) throw new Error('Invalid box height');
  return Math.ceil(value);
}

/** The thread the workspace has open (a session started without one continues it); undefined for a new chat. */
export function validateOpenThread(value) {
  if (value !== undefined && !isText(value)) throw new Error('Invalid thread');
  return value;
}

/**
 * One frame of microphone audio from the audio renderer: 24 kHz mono PCM16, so whole 16-bit samples.
 * @returns {ArrayBuffer | Uint8Array}
 */
export function validateAudioFrame(value) {
  const bytes = value instanceof ArrayBuffer || ArrayBuffer.isView(value) ? value.byteLength : 0;
  if (bytes === 0 || bytes % 2 !== 0) throw new Error('Invalid audio frame');
  return value;
}

/**
 * The audio renderer's answer to `audioStart`: capture is running, or why it could not start.
 * @returns {{ error?: string }}
 */
export function validateCaptureResult(value) {
  onlyKeys(value, ['error'], 'capture result');
  if (value.error !== undefined && !isText(value.error)) throw new Error('Invalid capture result');
  return value;
}
