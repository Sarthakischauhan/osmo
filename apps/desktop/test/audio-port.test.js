import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AUDIO_NOT_READY, createAudioPort } from '../src/audio-port.js';
import { CHANNELS } from '../src/session-contract.js';

function fixture() {
  const sent = [];
  const audio = createAudioPort((channel, payload) => sent.push(payload === undefined ? [channel] : [channel, payload]));
  return { audio, sent };
}

test('before the audio renderer is up a start fails, and asks it for nothing', async () => {
  const { audio, sent } = fixture();
  await assert.rejects(audio.port.start(() => {}), { message: AUDIO_NOT_READY });
  assert.deepEqual(sent, []);
});

test('start asks the renderer for the microphone and resolves once it says it is open', async () => {
  const { audio, sent } = fixture();
  audio.ready();
  let settled = false;
  const started = audio.port.start(() => {}).then(() => { settled = true; });
  assert.deepEqual(sent, [[CHANNELS.audioStart]]);
  await Promise.resolve();
  assert.equal(settled, false);
  audio.captured({});
  await started;
  assert.equal(settled, true);
});

test('frames from the renderer reach the session while a capture is wanted, and not after stop', async () => {
  const { audio } = fixture();
  audio.ready();
  const heard = [];
  const started = audio.port.start(frame => heard.push(frame));
  audio.frame(new Uint8Array([1, 2]));
  audio.captured({});
  await started;
  audio.frame(new Uint8Array([3, 4]));
  audio.port.stop();
  audio.frame(new Uint8Array([5, 6]));
  assert.deepEqual(heard.map(frame => [...frame]), [[1, 2], [3, 4]]);
});

test('a microphone that cannot open fails the start with the renderer\'s reason and drops frames', async () => {
  const { audio } = fixture();
  audio.ready();
  const heard = [];
  const started = audio.port.start(frame => heard.push(frame));
  audio.captured({ error: 'Permission denied' });
  await assert.rejects(started, { message: 'Permission denied' });
  audio.frame(new Uint8Array([1, 2]));
  assert.deepEqual(heard, []);
});

test('an answer nobody is waiting for changes nothing', async () => {
  const { audio } = fixture();
  audio.ready();
  const heard = [];
  const started = audio.port.start(frame => heard.push(frame));
  audio.captured({});
  await started;
  audio.captured({ error: 'late' });
  audio.frame(new Uint8Array([1, 2]));
  assert.equal(heard.length, 1);
});

test('stopping while the microphone is still opening fails that start, and tells the renderer to stop', async () => {
  const { audio, sent } = fixture();
  audio.ready();
  const started = audio.port.start(() => {});
  audio.port.stop();
  await assert.rejects(started, /stopped/);
  assert.deepEqual(sent, [[CHANNELS.audioStart], [CHANNELS.audioStop]]);
});

test('speech and flushes go to the renderer, in order', () => {
  const { audio, sent } = fixture();
  const first = new ArrayBuffer(960);
  const second = new ArrayBuffer(480);
  audio.port.play(first);
  audio.port.flush();
  audio.port.play(second);
  assert.deepEqual(sent, [[CHANNELS.audioPlay, first], [CHANNELS.audioFlush], [CHANNELS.audioPlay, second]]);
});

test('losing the renderer fails a pending start, ends the capture, and needs the renderer to be ready again', async () => {
  const { audio } = fixture();
  audio.ready();
  const heard = [];
  const started = audio.port.start(frame => heard.push(frame));
  audio.lost();
  await assert.rejects(started, /stopped/);
  audio.frame(new Uint8Array([1, 2]));
  assert.deepEqual(heard, []);
  await assert.rejects(audio.port.start(() => {}), { message: AUDIO_NOT_READY });
  audio.ready();
  const again = audio.port.start(() => {});
  audio.captured({});
  await again;
});
