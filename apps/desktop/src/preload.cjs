const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('osmoDesktop', {
  status: () => ipcRenderer.invoke('desktop:status'),
  setEnabled: enabled => ipcRenderer.invoke('desktop:enable', enabled),
  execute: action => ipcRenderer.invoke('desktop:execute', action),
  onStatus: callback => {
    const listener = (_event, status) => callback(status);
    ipcRenderer.on('desktop:status', listener);
    return () => ipcRenderer.removeListener('desktop:status', listener);
  },
});
