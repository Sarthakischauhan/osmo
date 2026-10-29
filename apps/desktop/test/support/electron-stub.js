// Just enough of Electron for src/app.js to run in a test: windows that record what is done to them, and the app, IPC
// and shortcut registries a test can reach into. `fake` is the one shared state; nothing here opens a real window.

export const fake = {
  windows: [],
  handlers: new Map(),
  shortcuts: new Map(),
  appListeners: new Map(),
  dialogs: [],
  quits: 0,
};

class Emitter {
  constructor() { this.listeners = new Map(); }
  on(name, listener) { this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]); return this; }
  emit(name, ...args) { for (const listener of this.listeners.get(name) ?? []) listener(...args); }
}

const defaultSession = {
  setPermissionCheckHandler(handler) { this.check = handler; },
  setPermissionRequestHandler(handler) { this.request = handler; },
};

class WebContents extends Emitter {
  constructor() {
    super();
    this.sent = [];
    this.session = defaultSession;
    this.mainFrame = { url: '' };
  }
  send(channel, payload) { this.sent.push([channel, payload]); }
  isDestroyed() { return false; }
  setWindowOpenHandler() {}
}

export class BrowserWindow extends Emitter {
  constructor(options) {
    super();
    this.options = options;
    this.visible = false;
    this.minimized = false;
    this.focused = false;
    this.reloads = 0;
    this.bounds = { x: options.x, y: options.y, width: options.width, height: options.height };
    this.webContents = new WebContents();
    fake.windows.push(this);
  }
  loadURL(url) { this.url = url; this.webContents.mainFrame.url = url; return Promise.resolve(); }
  reload() { this.reloads++; }
  show() { this.visible = true; }
  hide() { this.visible = false; }
  focus() { this.focused = true; }
  isMinimized() { return this.minimized; }
  restore() { this.minimized = false; }
  setBounds(bounds) { this.bounds = bounds; }
}

const display = { id: 1, bounds: { x: 0, y: 0, width: 1600, height: 900 }, workArea: { x: 0, y: 0, width: 1600, height: 900 } };

export const app = {
  isPackaged: false,
  requestSingleInstanceLock: () => true,
  whenReady: () => Promise.resolve(),
  on(name, listener) { fake.appListeners.set(name, [...(fake.appListeners.get(name) ?? []), listener]); },
  emit(name, ...args) { for (const listener of fake.appListeners.get(name) ?? []) listener(...args); },
  quit() { fake.quits++; },
};
export const globalShortcut = {
  register(accelerator, callback) { fake.shortcuts.set(accelerator, callback); return true; },
  unregisterAll() { fake.shortcuts.clear(); },
};
export const ipcMain = { handle(channel, handler) { fake.handlers.set(channel, handler); } };
export const screen = { getPrimaryDisplay: () => display, getDisplayMatching: () => display };
export const dialog = { showMessageBox: async (...args) => { fake.dialogs.push(args); return { response: 0 }; } };
export const Menu = { buildFromTemplate: template => template, setApplicationMenu() {} };
export const desktopCapturer = { getSources: async () => [] };
export const shell = { openExternal: async () => {} };
export const systemPreferences = {};
