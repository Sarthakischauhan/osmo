import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CHANNELS, validateAudioFrame, validateBoxHeight, validateCaptureResult, validateClientEvent, validateOpenThread, validateStart } from '../src/session-contract.js';
import { BOX_MAX_HEIGHT } from '../src/window-state.js';

test('every channel is unique, and renderer channels are separate from the ones main sends on', () => {
  const names = Object.values(CHANNELS);
  assert.equal(new Set(names).size, names.length);
  assert.deepEqual(Object.keys(CHANNELS).sort(), [
    'audioFlush', 'audioFrame', 'audioPlay', 'audioReady', 'audioStart', 'audioStarted', 'audioStop',
    'box', 'end', 'event', 'interrupt', 'openThread', 'send', 'showFull', 'snapshot', 'start', 'status',
  ]);
});

test('a session start carries only the pick and the thread', () => {
  assert.deepEqual(validateStart(undefined), {});
  assert.deepEqual(validateStart({ voice: 'eve', model: 'grok-voice-latest', effort: 'low', threadId: 't1' }), { voice: 'eve', model: 'grok-voice-latest', effort: 'low', threadId: 't1' });
  for (const value of [null, 'x', [], { voice: '' }, { model: 3 }, { effort: 'max' }, { url: 'ws://evil' }, { threadId: {} }]) assert.throws(() => validateStart(value));
});

test('a renderer may send on only the events the UI sends', () => {
  const allowed = [
    { type: 'widget.action', id: 'w1', action: 'pick', value: 'a' },
    { type: 'widget.action', id: 'w1', action: 'pick' },
    { type: 'session.configure', model: 'm', voice: 'v', effort: 'high' },
    { type: 'browser.click', id: 'b', x: 0.5, y: 1 },
    { type: 'browser.scroll', id: 'b', x: 0, y: 0.2, deltaX: 0, deltaY: -120 },
    { type: 'browser.key', id: 'b', key: 'a', code: 'KeyA', keyCode: 65 },
    { type: 'browser.close', id: 'b' },
  ];
  for (const event of allowed) assert.deepEqual(validateClientEvent(event), event);
});

test('session control, computer support, previews and malformed events are refused', () => {
  const refused = [
    null, 'x', [], {}, { type: 'session.start' }, { type: 'session.end' }, { type: 'interrupt' },
    { type: 'desktop.result', requestId: 'r', result: {} }, { type: 'desktop.configure', enabled: true },
    { type: 'voice.preview', requestId: 'p', model: 'm', voice: 'v' },
    { type: 'widget.action', id: '', action: 'a' }, { type: 'widget.action', id: 'w', action: 'a', extra: 1 },
    { type: 'browser.click', id: 'b', x: 2, y: 0 }, { type: 'browser.scroll', id: 'b', x: 0, y: 0, deltaX: NaN, deltaY: 0 },
    { type: 'browser.key', id: 'b', key: 'a', code: 'KeyA', keyCode: -1 }, { type: 'session.configure', model: 'm', voice: 'v', effort: 'max' },
  ];
  for (const event of refused) assert.throws(() => validateClientEvent(event), undefined, JSON.stringify(event));
});

test('the hover box asks for at most its maximum height', () => {
  assert.equal(validateBoxHeight(0), 0);
  assert.equal(validateBoxHeight(120.2), 121);
  assert.equal(validateBoxHeight(BOX_MAX_HEIGHT), BOX_MAX_HEIGHT);
  for (const value of [-1, BOX_MAX_HEIGHT + 1, NaN, Infinity, '100', null, undefined]) assert.throws(() => validateBoxHeight(value));
});

test('the open thread is an id, or nothing for a new chat', () => {
  assert.equal(validateOpenThread('t1'), 't1');
  assert.equal(validateOpenThread(undefined), undefined);
  for (const value of ['', null, 3, {}, ['t1']]) assert.throws(() => validateOpenThread(value));
});

test('a microphone frame is whole 16-bit samples, as a buffer or a view of one', () => {
  const buffer = new ArrayBuffer(4800);
  assert.equal(validateAudioFrame(buffer), buffer);
  const bytes = new Uint8Array(4);
  assert.equal(validateAudioFrame(bytes), bytes);
  for (const value of [new ArrayBuffer(0), new ArrayBuffer(3), new Uint8Array(5), 'pcm', null, undefined, [1, 2], { byteLength: 4 }]) assert.throws(() => validateAudioFrame(value));
});

test('the audio renderer answers a capture request with nothing, or an error', () => {
  assert.deepEqual(validateCaptureResult({}), {});
  assert.deepEqual(validateCaptureResult({ error: 'Permission denied' }), { error: 'Permission denied' });
  for (const value of [undefined, null, 'ok', { error: '' }, { error: 5 }, { ok: true }]) assert.throws(() => validateCaptureResult(value));
});
