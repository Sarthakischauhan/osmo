const { contextBridge, ipcRenderer } = require('electron');

// The only things a page can reach in the app. Channel names mirror session-contract.js (a sandboxed preload cannot
// import it); test/preload.test.js keeps the two equal. Main authorizes every call by window.
const listen = (channel, callback) => {
  const listener = (_event, value) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};

contextBridge.exposeInMainWorld('osmoDesktop', {
  status: () => ipcRenderer.invoke('desktop:status'),
  setEnabled: enabled => ipcRenderer.invoke('desktop:enable', enabled),
  onStatus: callback => listen('desktop:status', callback),
  session: {
    snapshot: () => ipcRenderer.invoke('session:snapshot'),
    start: options => ipcRenderer.invoke('session:start', options),
    end: () => ipcRenderer.invoke('session:end'),
    interrupt: () => ipcRenderer.invoke('session:interrupt'),
    send: event => ipcRenderer.invoke('session:send', event),
    openThread: threadId => ipcRenderer.invoke('workspace:thread', threadId),
    onEvent: callback => listen('session:event', callback),
    onStatus: callback => listen('session:status', callback),
  },
  orb: {
    showFull: () => ipcRenderer.invoke('window:show-full'),
    setBox: height => ipcRenderer.invoke('orb:box', height),
  },
});
