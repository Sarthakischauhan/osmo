import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trustedServer } from '../src/server.js';

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
