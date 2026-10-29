import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowMicrophone, localAppUrl, trustedFrame, validateAction } from '../src/policy.js';
test('native bridge accepts only loopback HTTP origins', () => {
  assert.equal(localAppUrl(), 'http://127.0.0.1:8787');
  for (const url of ['https://example.com', 'file:///tmp/app.html', 'http://localhost.evil', 'http://user:pass@localhost', 'http://localhost/path']) assert.throws(() => localAppUrl(url));
  assert.equal(trustedFrame('http://127.0.0.1:8787/', localAppUrl()), true);
  assert.equal(trustedFrame('http://127.0.0.1:8788/', localAppUrl()), false);
  assert.equal(trustedFrame('about:blank', localAppUrl()), false);
});
test('microphone is allowed only for the local main frame, including Electron securityOrigin checks', () => {
  const contents = {};
  const origin = localAppUrl();
  assert.equal(allowMicrophone(contents, contents, 'media', '', { isMainFrame: true, mediaType: 'audio', securityOrigin: origin }, origin), true);
  assert.equal(allowMicrophone(contents, contents, 'media', `${origin}/`, { isMainFrame: true, requestingUrl: `${origin}/` }, origin), true);
  assert.equal(allowMicrophone(contents, contents, 'media', 'null', { isMainFrame: true, securityOrigin: origin, requestingUrl: `${origin}/` }, origin), true);
  assert.equal(allowMicrophone(contents, contents, 'media', origin, { isMainFrame: true, mediaTypes: ['audio', 'video'], securityOrigin: origin }, origin), false);
  assert.equal(allowMicrophone(contents, contents, 'media', origin, { isMainFrame: true, mediaType: 'video', securityOrigin: origin }, origin), false);
  assert.equal(allowMicrophone(contents, contents, 'geolocation', origin, { isMainFrame: true }, origin), false);
  assert.equal(allowMicrophone(contents, contents, 'media', 'https://evil.example', { isMainFrame: true, mediaType: 'audio', securityOrigin: 'https://evil.example' }, origin), false);
  assert.equal(allowMicrophone({}, contents, 'media', '', { isMainFrame: true, mediaType: 'audio', securityOrigin: origin }, origin), false);
  assert.equal(allowMicrophone(contents, contents, 'media', origin, { isMainFrame: false, mediaType: 'audio', securityOrigin: origin }, origin), false);
});
test('input validates bounds and rejects unknown commands, text and key injection', () => {
  assert.deepEqual(validateAction({ action: 'click', x: 0.5, y: 0.2 }), { action: 'click', x: 0.5, y: 0.2 });
  for (const action of [{action:'exec',command:'ls'}, {action:'click',x:NaN,y:0}, {action:'click',x:1.1,y:0}, {action:'type',text:'x'.repeat(2001)}, {action:'type',text:'\u0001'}, {action:'key',keys:['enter;rm']}, {action:'scroll',amount:100}, {action:'screenshot',command:'ls'}]) assert.throws(() => validateAction(action));
});
