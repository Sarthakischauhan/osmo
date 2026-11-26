import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULT_TOOLS, serverEnvironment, socketProtocols, trustedServer, withToken } from '../src/server.js';

test('a local server is trusted only when it echoes this app token', async () => {
  const pages = {
    '/api/health': { ok: true, type: 'application/json', token: 'secret' },
    '/': { ok: true, type: 'text/html', token: 'secret' },
  };
  globalThis.fetch = async (url, options) => {
    const path = new URL(url).pathname;
    const page = pages[path];
    const presented = options.headers['X-Osmo-Desktop-Token'];
    return {
      ok: page.ok,
      headers: { get: name => (name === 'content-type' ? page.type : presented === page.token ? page.token : null) },
    };
  };
  assert.equal(await trustedServer('http://127.0.0.1:8787', 'secret'), true);
  assert.equal(await trustedServer('http://127.0.0.1:8787', 'other'), false);
  pages['/'].type = 'application/json';
  assert.equal(await trustedServer('http://127.0.0.1:8787', 'secret'), false);
});

test('the server never gets the token in its environment, only the word to read it from stdin', () => {
  const env = serverEnvironment({ PATH: '/bin', OSMO_DESKTOP_TOKEN: 'secret' }, { OSMO_TOOLS: 'osmo' });
  assert.deepEqual(env, { PATH: '/bin', OSMO_TOOLS: 'osmo', OSMO_DESKTOP_TOKEN_STDIN: '1' });
});

test('without a token main sends nothing extra; with one it adds the header and the socket subprotocol', () => {
  assert.deepEqual(socketProtocols(undefined), []);
  assert.deepEqual(socketProtocols('t'), ['osmo', 'osmo-token.t']);
  assert.equal(withToken(undefined, undefined), undefined);
  assert.deepEqual(withToken({ method: 'POST', headers: { 'Content-Type': 'application/json' } }, 't'),
    { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Osmo-Desktop-Token': 't' } });
});

test("the app's server gets every one of the server's default tool categories, plus desktop", () => {
  const settings = readFileSync(new URL('../../symphony-server/src/symphony_server/settings.py', import.meta.url), 'utf8');
  const line = /^DEFAULT_TOOL_CATEGORIES[^=]*= \(([^)]*)\)/m.exec(settings)?.[1];
  assert.ok(line, 'DEFAULT_TOOL_CATEGORIES not found');
  const server = [...line.matchAll(/"([a-z]+)"/g)].map(match => match[1]);
  assert.deepEqual(DEFAULT_TOOLS.split(',').sort(), [...server, 'desktop'].sort());
});
