import { app, BrowserWindow, desktopCapturer, dialog, globalShortcut, ipcMain, Menu, nativeImage, screen, shell, systemPreferences } from 'electron';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createAudioPort } from './audio-port.js';
import { allowMicrophone, localAppUrl, trustedFrame, validateAction } from './policy.js';
import { pythonExecutable, startServer } from './server.js';
import { CHANNELS, validateAudioFrame, validateBoxHeight, validateCaptureResult, validateClientEvent, validateOpenThread, validateStart } from './session-contract.js';
import { createSessionController } from './session-controller.js';
import { SWITCH_ACCELERATOR, createWindowSwitch } from './window-mode.js';
import { boxLayout, defaultOrbBounds } from './window-state.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const origin = localAppUrl(process.env.OSMO_DESKTOP_URL);
// The same orb as docs/osmo-orb.svg, so the dock, taskbar and window match the mark in the README.
const appIcon = nativeImage.createFromPath(path.join(here, '../assets/icon.png'));
const STOP_SHORTCUT = 'CommandOrControl+Shift+Escape';

/**
 * Two windows over one session: the workspace (the Osmo UI) and the always-on-top orb. Main owns the session, the
 * computer-support consent and the stop shortcut; the windows only show and ask. A third, hidden window is the
 * session's microphone and speakers: main has no audio of its own, so that page captures and plays over IPC.
 */
export function runApp() {
  let workspace;
  let floating;
  let audioWindow;
  let orbBounds;
  let switcher;
  let quitting = false;
  let enabled = false;
  let epoch = 0;
  let busy = false;
  let worker;
  let backend;

  const status = () => ({ enabled, platform: process.platform, stopShortcut: STOP_SHORTCUT });
  const live = contents => contents && !contents.isDestroyed();
  const windows = () => [workspace, floating].filter(win => win && live(win.webContents));
  const broadcast = (channel, payload) => { for (const win of windows()) win.webContents.send(channel, payload); };

  const audio = createAudioPort((channel, payload) => { if (audioWindow && live(audioWindow.webContents)) audioWindow.webContents.send(channel, payload); });
  // A call first asks macOS for the microphone: a stop while it asks means the call is over, so capture never opens.
  let stops = 0;
  const port = {
    ...audio.port,
    async start(callback) {
      const version = stops;
      await microphone();
      if (version !== stops) throw new Error('Microphone capture stopped before it started');
      return audio.port.start(callback);
    },
    stop() { stops++; audio.port.stop(); },
  };
  const session = createSessionController({
    url: `${origin.replace(/^http/, 'ws')}/ws`,
    createSocket: url => new WebSocket(url),
    audio: port,
    desktop: { execute: value => execute(value) },
    onEvent: event => broadcast(CHANNELS.event, event),
    onStatus: snapshot => broadcast(CHANNELS.status, snapshot),
    onDisconnected: () => revoke(),
  });

  function revoke() {
    enabled = false;
    epoch++;
    worker?.kill();
    session.setDesktopEnabled(false);
    if (workspace && live(workspace.webContents)) workspace.webContents.send('desktop:status', status());
  }

  /** Electron never asks macOS for the microphone on its own, and a capture macOS has not allowed fails as a bare
   * "Permission denied". Asking resolves at once, without a prompt, once the user has answered. */
  async function microphone() {
    if (process.platform !== 'darwin' || systemPreferences.getMediaAccessStatus('microphone') === 'granted') return;
    if (await systemPreferences.askForMediaAccess('microphone')) return;
    throw new Error('Allow Osmo in System Settings → Privacy & Security → Microphone, then quit Osmo (Cmd+Q) and reopen it.');
  }

  /** Only the page this app serves, in a window of this app: `allowed` names which windows may use the channel. */
  function authorize(event, ...allowed) {
    if (!allowed.some(win => win && event.sender === win.webContents) || event.senderFrame !== event.sender.mainFrame
        || !trustedFrame(event.senderFrame?.url, origin)) throw new Error('Untrusted desktop caller');
  }

  function input(action, bounds) {
    return new Promise((resolve, reject) => {
      const script = app.isPackaged ? path.join(process.resourcesPath, 'input.py') : path.join(here, 'input.py');
      const child = spawn(pythonExecutable(), [script], { stdio: ['pipe', 'pipe', 'pipe'] });
      worker = child;
      let error = '';
      const timer = setTimeout(() => child.kill(), 15000);
      child.stderr.on('data', chunk => { error = (error + chunk).slice(-2000); });
      child.stdout.resume();
      child.stdin.on('error', () => {});
      child.once('error', reject);
      child.once('close', code => {
        clearTimeout(timer);
        if (worker === child) worker = undefined;
        code === 0 ? resolve({ ok: true }) : reject(new Error(error || 'Desktop input stopped; install pyautogui and check OS permissions'));
      });
      child.stdin.end(JSON.stringify({ ...action, bounds }));
    });
  }

  /** A native action Symphony asked for (`desktop.request`): only while consent is active, one at a time. */
  async function execute(value) {
    if (!enabled) throw new Error('Computer support is disabled');
    if (busy) throw new Error('Another desktop operation is running');
    const action = validateAction(value);
    const version = epoch;
    busy = true;
    try {
      const display = screen.getPrimaryDisplay();
      let result;
      if (action.action === 'screenshot') {
        const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 1440, height: 1440 } });
        if (process.platform === 'darwin' && systemPreferences.getMediaAccessStatus('screen') !== 'granted') {
          throw new Error('Allow Osmo in System Settings → Privacy & Security → Screen Recording, then quit Osmo (Cmd+Q) and reopen it.');
        }
        const source = sources.find(item => item.display_id === String(display.id));
        if (!source || source.thumbnail.isEmpty()) throw new Error('Primary display capture is unavailable');
        const size = source.thumbnail.getSize();
        result = { image: source.thumbnail.toJPEG(75).toString('base64'), width: size.width, height: size.height };
      } else {
        // Re-check immediately before input; revoking consent invalidates all in-flight requests.
        if (!enabled || version !== epoch) throw new Error('Computer support stopped');
        result = await input(action, display.bounds);
      }
      if (!enabled || version !== epoch) throw new Error('Computer support stopped');
      return result;
    } finally { busy = false; }
  }

  ipcMain.handle('desktop:status', event => { authorize(event, workspace); return status(); });
  ipcMain.handle('desktop:enable', async (event, next) => {
    authorize(event, workspace);
    if (typeof next !== 'boolean') throw new Error('Expected a boolean');
    revoke();
    if (next) {
      const version = epoch;
      const result = await dialog.showMessageBox(workspace, {
        type: 'question', buttons: ['Cancel', 'Enable computer support'], defaultId: 0, cancelId: 0,
        title: 'Allow Osmo to help on this computer?',
        message: 'Osmo can capture your primary screen, click, type and press keys.',
        detail: 'Screen images are sent to your configured vision provider. Actions run in your current desktop session. Stop anytime with Ctrl/Cmd+Shift+Escape or the Computer support button.',
      });
      if (result.response === 1 && version === epoch) {
        if (process.platform === 'darwin' && !systemPreferences.isTrustedAccessibilityClient(true)) {
          throw new Error('Allow Osmo in System Settings → Privacy & Security → Accessibility, then enable again.');
        }
        enabled = true;
        session.setDesktopEnabled(true);
        workspace.webContents.send('desktop:status', status());
      }
    }
    return status();
  });

  // The session: both windows may ask for it, the same way.
  ipcMain.handle(CHANNELS.snapshot, event => { authorize(event, workspace, floating); return session.snapshot(); });
  ipcMain.handle(CHANNELS.start, (event, options) => { authorize(event, workspace, floating); return session.start(validateStart(options)); });
  ipcMain.handle(CHANNELS.end, event => { authorize(event, workspace, floating); return session.end(); });
  ipcMain.handle(CHANNELS.interrupt, event => { authorize(event, workspace, floating); session.interrupt(); });
  ipcMain.handle(CHANNELS.send, (event, value) => { authorize(event, workspace, floating); session.send(validateClientEvent(value)); });
  // The workspace says which thread it has open; a session the orb starts continues it.
  ipcMain.handle(CHANNELS.openThread, (event, threadId) => { authorize(event, workspace); session.setOpenThread(validateOpenThread(threadId)); });
  // The audio renderer's own: it is up, the microphone is open (or why not), and a frame of what it hears.
  ipcMain.handle(CHANNELS.audioReady, event => { authorize(event, audioWindow); audio.ready(); });
  ipcMain.handle(CHANNELS.audioStarted, (event, result) => { authorize(event, audioWindow); audio.captured(validateCaptureResult(result)); });
  ipcMain.handle(CHANNELS.audioFrame, (event, pcm16) => { authorize(event, audioWindow); audio.frame(validateAudioFrame(pcm16)); });
  // The orb window's own: double-click opens the workspace; the hover box asks for room and learns which side it got.
  ipcMain.handle(CHANNELS.showFull, event => { authorize(event, floating); switcher.toFull(); });
  ipcMain.handle(CHANNELS.box, (event, height) => {
    authorize(event, floating);
    const layout = boxLayout(orbBounds, screen.getDisplayMatching(orbBounds).workArea, validateBoxHeight(height));
    floating.setBounds(layout.bounds);
    return { side: layout.side, orbLeft: layout.orbLeft };
  });

  /** The page's own rules for each window: it may not navigate away or open windows. */
  function secure(win, { revokeOnNavigation }) {
    const contents = win.webContents;
    contents.setWindowOpenHandler(({ url }) => {
      if (url.startsWith('https://')) void shell.openExternal(url);
      return { action: 'deny' };
    });
    contents.on('will-navigate', (event, url) => {
      if (!trustedFrame(url, origin)) { event.preventDefault(); revoke(); }
    });
    if (revokeOnNavigation) contents.on('did-start-navigation', (_event, _url, inPlace, isMainFrame) => { if (isMainFrame && !inPlace) revoke(); });
    contents.on('render-process-gone', revoke);
  }

  const webPreferences = { preload: path.join(here, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: false };
  const chrome = { title: 'Osmo', icon: appIcon };

  /** The hidden window with the microphone and speakers: the only one allowed to capture, and no other page may. */
  function createAudio() {
    audioWindow = new BrowserWindow({
      show: false, width: 200, height: 200, skipTaskbar: true,
      webPreferences: { ...webPreferences, preload: path.join(here, 'audio-preload.cjs'), autoplayPolicy: 'no-user-gesture-required', backgroundThrottling: false },
    });
    const contents = audioWindow.webContents;
    // Every window shares the default session: its handlers allow the audio renderer's microphone and nothing else.
    contents.session.setPermissionCheckHandler((wc, permission, requestingOrigin, details) =>
      allowMicrophone(wc, contents, permission, requestingOrigin, details, origin));
    contents.session.setPermissionRequestHandler((wc, permission, callback, details) =>
      callback(allowMicrophone(wc, contents, permission, '', details, origin)));
    secure(audioWindow, { revokeOnNavigation: false });
    // A crashed audio page ends the call rather than leaving a session that cannot hear or speak.
    contents.on('render-process-gone', () => { audio.lost(); session.end(); audioWindow.reload(); });
    audioWindow.on('closed', () => { audioWindow = undefined; });
    void audioWindow.loadURL(`${origin}/#audio`);
  }

  function createWorkspace() {
    workspace = new BrowserWindow({
      ...chrome, width: 1100, height: 800, minWidth: 520, minHeight: 600, backgroundColor: '#111111', show: false, webPreferences,
    });
    secure(workspace, { revokeOnNavigation: true });
    workspace.on('minimize', () => switcher.toOrb());
    // Closing the workspace never ends voice or background runs: it goes back to the orb, and only Quit ends the app.
    workspace.on('close', event => {
      if (quitting) return;
      event.preventDefault();
      revoke();
      switcher.toOrb();
    });
    workspace.on('closed', () => { workspace = undefined; });
    void workspace.loadURL(origin).catch(() => {
      void dialog.showMessageBox(workspace, { type: 'error', message: 'Osmo server is unavailable', detail: `Start the Osmo server at ${origin}. See apps/desktop/README.md for setup.` });
    });
  }

  function createFloating() {
    orbBounds = defaultOrbBounds(screen.getPrimaryDisplay().workArea);
    floating = new BrowserWindow({
      ...orbBounds, ...chrome, frame: false, transparent: true, hasShadow: false, alwaysOnTop: true, resizable: false, movable: false,
      minimizable: false, maximizable: false, fullscreenable: false, skipTaskbar: true, show: false, webPreferences,
    });
    secure(floating, { revokeOnNavigation: false });
    floating.on('closed', () => { floating = undefined; });
    void floating.loadURL(`${origin}/#orb`);
  }

  // Before ready, so macOS shows Osmo (and the orb) instead of Electron in the Dock and menu bar.
  app.setName('Osmo');
  if (process.platform === 'darwin' && app.dock) app.dock.setIcon(appIcon);
  if (!app.requestSingleInstanceLock()) app.quit();
  else {
    app.on('second-instance', () => switcher?.toFull());
    app.whenReady().then(async () => {
      if (!process.env.OSMO_DESKTOP_URL) {
        try { backend = await startServer({ packaged: app.isPackaged, resourcesPath: process.resourcesPath, origin }); }
        catch (error) {
          await dialog.showMessageBox({ type: 'error', message: 'Osmo could not start', detail: String(error) });
          app.quit();
          return;
        }
      }
      Menu.setApplicationMenu(Menu.buildFromTemplate([
        { label: 'Osmo', submenu: [{ label: 'Stop computer support', accelerator: STOP_SHORTCUT, click: revoke }, { role: 'quit' }] },
        { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
      ]));
      createAudio();
      createWorkspace();
      createFloating();
      switcher = createWindowSwitch({ full: workspace, orb: floating });
      switcher.toOrb();
      if (!globalShortcut.register(STOP_SHORTCUT, revoke)) {
        void dialog.showMessageBox(workspace, { type: 'warning', message: 'The stop shortcut is unavailable', detail: 'Use Computer support → Stop or the Osmo menu to stop control.' });
      }
      if (!globalShortcut.register(SWITCH_ACCELERATOR, () => switcher.toggle())) {
        void dialog.showMessageBox(workspace, { type: 'warning', message: 'The orb shortcut is unavailable', detail: 'Minimize the window to show the orb, and double-click the orb to come back.' });
      }
      app.on('activate', () => switcher.toFull());
    });
    app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
    app.on('before-quit', () => { quitting = true; revoke(); session.end(); backend?.stop(); });
    app.on('will-quit', () => globalShortcut.unregisterAll());
  }
}
