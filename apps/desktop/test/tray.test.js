import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAutomationTray, nextLabel, runNotice, trayTemplate } from '../src/tray.js';

const actions = { open() {}, openCalendar() {}, quit() {}, pause() {} };
const labels = template => template.filter(item => item.label).map(item => item.label);

test('the tray menu follows the mockup: running state, the next automation, then Pause all', () => {
  const now = new Date(2026, 10, 21, 14, 57);
  const next = { title: 'Weekly notes', nextRunAt: new Date(2026, 10, 21, 15, 0).toISOString() };
  assert.deepEqual(labels(trayTemplate({ paused: false, running: 0, next, last: 3 }, actions, now)),
    ['Running', 'Idle', 'Next', 'Weekly notes', 'Today, 3:00 PM · in 3 min', 'Pause all', 'Open calendar', 'Open Osmo', 'Quit Osmo']);
  assert.deepEqual(labels(trayTemplate({ paused: true, running: 0, next, last: 3 }, actions, now)).slice(0, 4), ['Running', 'Paused', 'Nothing scheduled', 'Resume all']);
  assert.equal(labels(trayTemplate(undefined, actions))[0], 'Osmo is starting…');
  assert.equal(nextLabel(new Date(2026, 10, 23, 8).toISOString(), now), 'Mon, Nov 23, 8:00 AM');
});

test('runs become notifications', () => {
  assert.deepEqual(runNotice({ title: 'Brief', status: 'skipped', detail: 'missed' }), { title: 'Brief was skipped', body: 'missed' });
  assert.equal(runNotice({ title: 'Brief', status: 'started', detail: '' }).title, 'Brief');
});

test('the tray notifies only runs that happen after it started, and pauses through the API', async () => {
  const calls = [];
  const notes = [];
  let menu;
  let status = { paused: false, running: 0, next: null, last: 7 };
  const request = async (path, init) => {
    calls.push([path, init?.method ?? 'GET', init?.body]);
    if (path === '/api/automations/status') return status;
    if (path.startsWith('/api/automations/runs')) return { runs: [{ id: 8, title: 'Journal', status: 'ok', detail: 'Write three lines' }], last: 8 };
    if (path === '/api/automations/pause') return { ...status, paused: JSON.parse(init.body).paused };
    throw new Error(path);
  };
  const tray = createAutomationTray({
    request,
    createTray: () => ({ setContextMenu: m => { menu = m; }, setToolTip() {}, destroy() {} }),
    buildMenu: template => template,
    notify: note => notes.push(note),
    ...actions,
    every: () => 0,
    stopEvery() {},
  });
  await tray.refresh();
  assert.deepEqual(notes, []);
  status = { ...status, last: 8 };
  await tray.refresh();
  assert.deepEqual(notes, [{ title: 'Journal', body: 'Write three lines' }]);
  assert.ok(calls.some(([path]) => path === '/api/automations/runs?after=7'));
  await menu.find(item => item.label === 'Pause all').click();
  assert.deepEqual(calls.at(-1), ['/api/automations/pause', 'POST', '{"paused":true}']);
  assert.equal(menu.find(item => item.label === 'Resume all')?.label, 'Resume all');
});
