import { app, BrowserWindow, desktopCapturer, dialog, globalShortcut, ipcMain, Menu, screen, shell, systemPreferences } from 'electron';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { localAppUrl, trustedFrame, validateAction } from './policy.js';
import { pythonExecutable, startServer } from './server.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const origin = localAppUrl(process.env.OSMO_DESKTOP_URL);
let window;
let enabled = false;
let epoch = 0;
let busy = false;
let worker;
let backend;
const status = () => ({ enabled, platform: process.platform, stopShortcut: 'CommandOrControl+Shift+Escape' });
function revoke() {
  enabled = false;
  epoch++;
  worker?.kill();
  if (window && !window.isDestroyed() && !window.webContents.isDestroyed()) window.webContents.send('desktop:status', status());
}
function authorize(event) {
  if (event.sender !== window?.webContents || event.senderFrame !== event.sender.mainFrame
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

ipcMain.handle('desktop:status', event => { authorize(event); return status(); });
ipcMain.handle('desktop:enable', async (event, next) => {
  authorize(event);
  if (typeof next !== 'boolean') throw new Error('Expected a boolean');
  revoke();
  if (next) {
    const version = epoch;
    const result = await dialog.showMessageBox(window, {
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
      window.webContents.send('desktop:status', status());
    }
  }
  return status();
});
ipcMain.handle('desktop:execute', async (event, value) => {
  authorize(event);
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
        throw new Error('Allow Osmo in System Settings → Privacy & Security → Screen Recording, then restart Osmo.');
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
});

function createWindow() {
  window = new BrowserWindow({
    title: 'Osmo', width: 1100, height: 800, minWidth: 520, minHeight: 600, backgroundColor: '#111111',
    webPreferences: { preload: path.join(here, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: false },
  });
  const contents = window.webContents;
  const session = contents.session;
  session.setPermissionCheckHandler((wc, permission, requestingOrigin, details) =>
    wc === contents && details.isMainFrame && trustedFrame(requestingOrigin, origin) && permission === 'media' && details.mediaType === 'audio');
  session.setPermissionRequestHandler((wc, permission, callback, details) => {
    callback(wc === contents && details.isMainFrame && trustedFrame(details.requestingUrl, origin)
      && permission === 'media' && 'mediaTypes' in details && details.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio');
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) void shell.openExternal(url);
    return { action: 'deny' };
  });
  contents.on('will-navigate', (event, url) => {
    if (!trustedFrame(url, origin)) { event.preventDefault(); revoke(); }
  });
  contents.on('did-start-navigation', (_event, _url, inPlace, isMainFrame) => { if (isMainFrame && !inPlace) revoke(); });
  contents.on('render-process-gone', revoke);
  window.on('closed', () => { revoke(); window = undefined; });
  void window.loadURL(origin).catch(() => {
    void dialog.showMessageBox(window, { type: 'error', message: 'Osmo server is unavailable', detail: `Start the Osmo server at ${origin}. See apps/desktop/README.md for setup.` });
  });
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { window?.restore(); window?.show(); window?.focus(); });
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
      { label: 'Osmo', submenu: [{ label: 'Stop computer support', accelerator: 'CommandOrControl+Shift+Escape', click: revoke }, { role: 'quit' }] },
      { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
    ]));
    createWindow();
    if (!globalShortcut.register('CommandOrControl+Shift+Escape', revoke)) {
      void dialog.showMessageBox(window, { type: 'warning', message: 'The stop shortcut is unavailable', detail: 'Use Computer support → Stop or the Osmo menu to stop control.' });
    }
    app.on('activate', () => { if (!window) createWindow(); });
  });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
  app.on('before-quit', () => { revoke(); backend?.stop(); });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
