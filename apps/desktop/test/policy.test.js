import { test } from 'node:test';
import assert from 'node:assert/strict';
import { localAppUrl, trustedFrame, validateAction } from '../src/policy.js';
test('native bridge accepts only loopback HTTP origins', () => {
  assert.equal(localAppUrl(), 'http://127.0.0.1:8787');
  for (const url of ['https://example.com', 'file:///tmp/app.html', 'http://localhost.evil', 'http://user:pass@localhost', 'http://localhost/path']) assert.throws(() => localAppUrl(url));
  assert.equal(trustedFrame('http://127.0.0.1:8787/', localAppUrl()), true);
  assert.equal(trustedFrame('http://127.0.0.1:8788/', localAppUrl()), false);
  assert.equal(trustedFrame('about:blank', localAppUrl()), false);
});
test('input validates bounds and rejects unknown commands, text and key injection', () => {
  assert.deepEqual(validateAction({ action: 'click', x: 0.5, y: 0.2 }), { action: 'click', x: 0.5, y: 0.2 });
  for (const action of [{action:'exec',command:'ls'}, {action:'click',x:NaN,y:0}, {action:'click',x:1.1,y:0}, {action:'type',text:'x'.repeat(2001)}, {action:'type',text:'\u0001'}, {action:'key',keys:['enter;rm']}, {action:'scroll',amount:100}, {action:'screenshot',command:'ls'}]) assert.throws(() => validateAction(action));
});
