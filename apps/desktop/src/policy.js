// Only a locally running Osmo may access the native bridge.
export function localAppUrl(value = 'http://127.0.0.1:8787') {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('OSMO_DESKTOP_URL must be an HTTP loopback origin');
  }
  return url.origin;
}

export function trustedFrame(url, origin) {
  try { return new URL(url).origin === origin; } catch { return false; }
}

export function validateAction(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid desktop action');
  const action = value.action;
  const allowed = {
    screenshot: ['action'], click: ['action', 'x', 'y', 'button'],
    type: ['action', 'text'], key: ['action', 'keys'], scroll: ['action', 'amount'],
  }[action];
  if (!allowed || Object.keys(value).some(key => !allowed.includes(key))) throw new Error('Unsupported desktop action');
  if (action === 'click' && (![value.x, value.y].every(n => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1)
      || (value.button !== undefined && !['left', 'right'].includes(value.button)))) throw new Error('Click coordinates must be in 0..1');
  if (action === 'type' && (typeof value.text !== 'string' || !value.text.length || value.text.length > 2000 || /[^\x20-\x7e\n\t]/.test(value.text))) {
    throw new Error('Typing supports 1..2000 ASCII characters');
  }
  const keys = new Set(['ctrl', 'alt', 'shift', 'command', 'enter', 'tab', 'escape', 'backspace', 'delete', 'space', 'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown', ...'abcdefghijklmnopqrstuvwxyz0123456789']);
  if (action === 'key' && (!Array.isArray(value.keys) || value.keys.length < 1 || value.keys.length > 4 || value.keys.some(k => !keys.has(k)))) throw new Error('Invalid key combination');
  if (action === 'scroll' && (!Number.isInteger(value.amount) || Math.abs(value.amount) > 10 || value.amount === 0)) throw new Error('Scroll must be a nonzero integer in -10..10');
  return value;
}
