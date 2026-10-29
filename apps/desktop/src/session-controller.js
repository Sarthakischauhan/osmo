// The voice session, owned by Electron main: the one Symphony WebSocket, its audio, and what both renderers are told
// of it. Renderers ask (session-contract.js) and listen; they never hold a socket or a microphone, so hiding, showing
// or reloading a window cannot end the session, and there is never a second connection.
//
// Audio is a port, because Electron's main process has no way to capture a microphone or play sound (no
// getUserMedia, no Web Audio, no Node audio API). Until a mechanism is chosen the app passes no port and `start`
// reports AUDIO_UNAVAILABLE instead of connecting.

export const AUDIO_UNAVAILABLE = 'Microphone capture is not available in the main process: Electron gives it no audio capture or playback. A capture mechanism has to be chosen first.';

/** `state.detail` that means: drop queued assistant audio now (the protocol's BARGE_IN). */
const BARGE_IN = 'barge-in';
const OPEN = 1;

/**
 * Microphone in, assistant speech out: whatever main will use for audio.
 * @typedef {object} AudioPort
 * @property {(onFrame: (pcm16: ArrayBuffer) => void) => Promise<void>} start begin capturing 24 kHz mono PCM16
 * @property {() => void} stop
 * @property {(pcm16: ArrayBuffer) => void} play
 * @property {() => void} flush drop everything queued for playback
 */

/** @returns {import('./session-contract.js').SessionSnapshot} */
export function emptySnapshot() {
  return { connection: 'disconnected', state: 'idle', detail: undefined, error: undefined, startedAt: undefined, thread: undefined, selection: undefined, models: [] };
}

/** What the snapshot keeps of a server event: the session's state, thread, registry and confirmed pick. */
export function reduceSnapshot(snapshot, event) {
  switch (event.type) {
    case 'state': return { ...snapshot, state: event.state, detail: event.detail === BARGE_IN ? undefined : event.detail };
    case 'thread': return { ...snapshot, thread: event.thread };
    case 'models': return { ...snapshot, models: event.models, selection: event.selection };
    case 'voice.configured': return { ...snapshot, selection: event.selection };
    default: return snapshot;
  }
}

/**
 * @param {{
 *   url: string,
 *   createSocket: (url: string) => any,
 *   audio?: AudioPort,
 *   desktop: { execute: (action: Record<string, unknown>) => Promise<Record<string, unknown>> },
 *   onEvent: (event: Record<string, any>) => void,
 *   onStatus: (snapshot: import('./session-contract.js').SessionSnapshot) => void,
 *   onDisconnected?: () => void,
 * }} options
 */
export function createSessionController({ url, createSocket, audio, desktop, onEvent, onStatus, onDisconnected }) {
  let snapshot = emptySnapshot();
  let socket;
  let desktopEnabled = false;

  const set = next => { snapshot = next; onStatus(snapshot); };
  const sendJson = (target, event) => { if (target.readyState === OPEN) target.send(JSON.stringify(event)); };

  /** Everything that ends a session, whoever ended it. */
  function teardown(target) {
    if (socket !== target) return false;
    socket = undefined;
    audio?.stop();
    audio?.flush();
    target.close();
    return true;
  }

  async function runDesktop(target, request) {
    try {
      const result = await desktop.execute(request.action);
      if (socket === target) sendJson(target, { type: 'desktop.result', requestId: request.requestId, result });
    } catch (error) {
      const message = (error instanceof Error ? error.message : String(error)).slice(0, 2000);
      if (socket === target) sendJson(target, { type: 'desktop.result', requestId: request.requestId, error: message });
    }
  }

  function onMessage(target, data) {
    if (socket !== target) return;
    if (typeof data !== 'string') { audio?.play(data); return; }
    let event;
    try { event = JSON.parse(data); } catch { return; }
    if (event === null || typeof event !== 'object' || typeof event.type !== 'string') return;
    if (event.type === 'desktop.request') { void runDesktop(target, event); return; }
    if (event.type === 'state' && event.detail === BARGE_IN) audio?.flush();
    const next = reduceSnapshot(snapshot, event);
    if (next !== snapshot) set(next);
    onEvent(event);
  }

  function onClose(target) {
    if (!teardown(target)) return;
    set({ ...snapshot, connection: 'disconnected', state: 'idle', detail: undefined, startedAt: undefined });
    onDisconnected?.();
  }

  function sendFrame(target, frame) {
    if (socket === target && target.readyState === OPEN) target.send(frame);
  }

  const failed = (target, error) => {
    teardown(target);
    set({ ...snapshot, connection: 'disconnected', state: 'idle', error: error instanceof Error ? error.message : String(error) });
  };

  return {
    snapshot: () => snapshot,

    /** Starts the one session; while one is starting or live this changes nothing. Failures arrive as `error`. */
    async start(options) {
      if (socket) return snapshot;
      if (!audio) { set({ ...snapshot, error: AUDIO_UNAVAILABLE }); return snapshot; }
      const target = createSocket(url);
      socket = target;
      set({ ...snapshot, connection: 'connecting', error: undefined });
      try {
        target.binaryType = 'arraybuffer';
        await new Promise((resolve, reject) => {
          target.onopen = () => resolve(undefined);
          target.onerror = () => reject(new Error(`could not connect to ${url}`));
          target.onclose = () => reject(new Error('the connection closed before the session started'));
        });
        if (socket !== target) return snapshot;
        target.onmessage = message => onMessage(target, message.data);
        target.onclose = () => onClose(target);
        sendJson(target, { type: 'session.start', ...options });
        if (desktopEnabled) sendJson(target, { type: 'desktop.configure', enabled: true });
        await audio.start(frame => sendFrame(target, frame));
        if (socket !== target) return snapshot;
        set({ ...snapshot, connection: 'connected', startedAt: Date.now() });
      } catch (error) {
        if (socket === target) failed(target, error);
      }
      return snapshot;
    },

    end() {
      const target = socket;
      if (!target) return snapshot;
      sendJson(target, { type: 'session.end' });
      teardown(target);
      set({ ...snapshot, connection: 'disconnected', state: 'idle', detail: undefined, startedAt: undefined });
      onDisconnected?.();
      return snapshot;
    },

    interrupt() {
      if (!socket) return;
      audio?.flush();
      sendJson(socket, { type: 'interrupt' });
    },

    /** An already validated event from a renderer, passed on to Symphony. */
    send(event) {
      if (!socket) return;
      if (event.type === 'widget.action') audio?.flush();
      sendJson(socket, event);
    },

    /** Computer support was granted or revoked in main: Symphony is told, now and on the next session. */
    setDesktopEnabled(enabled) {
      desktopEnabled = enabled;
      if (socket && snapshot.connection === 'connected') sendJson(socket, { type: 'desktop.configure', enabled });
    },
  };
}
