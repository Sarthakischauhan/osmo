const { contextBridge, ipcRenderer } = require('electron');

// The hidden audio renderer's only way out: it hears when main wants the microphone or playback, and sends frames and
// results back. Channel names mirror session-contract.js (a sandboxed preload cannot import it); test/preload.test.js
// keeps the two equal. This page gets no session, window or computer-support calls.
const listen = (channel, callback) => {
  const listener = (_event, value) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};

contextBridge.exposeInMainWorld('osmoAudio', {
  ready: () => ipcRenderer.invoke('audio:ready'),
  started: result => ipcRenderer.invoke('audio:started', result),
  frame: pcm16 => ipcRenderer.invoke('audio:frame', pcm16),
  onStart: callback => listen('audio:start', callback),
  onStop: callback => listen('audio:stop', callback),
  onPlay: callback => listen('audio:play', callback),
  onFlush: callback => listen('audio:flush', callback),
});
