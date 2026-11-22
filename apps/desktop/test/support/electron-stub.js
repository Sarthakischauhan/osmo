// Just enough of Electron for src/app.js to run in a test: windows that record what is done to them, and the app, IPC
// and shortcut registries a test can reach into. `fake` is the one shared state; nothing here opens a real window.

export const fake = {
  windows: [],
  handlers: new Map(),
  shortcuts: new Map(),
  appListeners: new Map(),
  dialogs: [],
  quits: 0,
  trays: [],
  notifications: [],
  loginItems: [],
  componentsReady: 0,
  // Cookies set on the default session, each with how many windows existed when it was set.
  cookies: [],
};

class Emitter {
  constructor() { this.listeners = new Map(); }
  on(name, listener) { this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]); return this; }
  emit(name, ...args) { for (const listener of this.listeners.get(name) ?? []) listener(...args); }
}

const defaultSession = {
  setPermissionCheckHandler(handler) { this.check = handler; },
  setPermissionRequestHandler(handler) { this.request = handler; },
  cookies: { set: async cookie => { fake.cookies.push({ cookie, windowsBefore: fake.windows.length }); } },
};
export const session = { defaultSession };

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
  setName() {},
  dock: undefined,
  requestSingleInstanceLock: () => true,
  whenReady: () => Promise.resolve(),
  on(name, listener) { fake.appListeners.set(name, [...(fake.appListeners.get(name) ?? []), listener]); },
  emit(name, ...args) { for (const listener of fake.appListeners.get(name) ?? []) listener(...args); },
  quit() { fake.quits++; },
  setLoginItemSettings(settings) { fake.loginItems.push(settings); },
};
export const components = { whenReady: async () => { fake.componentsReady++; return []; } };
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
const image = { isEmpty: () => false, resize() { return image; } };
export const nativeImage = { createFromPath: () => image };
export class Tray {
  constructor(icon) { this.icon = icon; this.menu = undefined; this.destroyed = false; fake.trays.push(this); }
  setContextMenu(menu) { this.menu = menu; }
  setToolTip(tip) { this.tip = tip; }
  destroy() { this.destroyed = true; }
}
export class Notification {
  static isSupported() { return true; }
  constructor(options) { this.options = options; }
  show() { fake.notifications.push(this.options); }
}
// The microphone is already allowed, as macOS answers once the user has.
export const systemPreferences = { getMediaAccessStatus: () => 'granted', askForMediaAccess: async () => true };
