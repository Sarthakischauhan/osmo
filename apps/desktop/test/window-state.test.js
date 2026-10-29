import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ORB_SIZE, PANEL_SIZE, clampInto, defaultOrbBounds, orbBoundsIn, panelBounds, panelPlacement, parseSavedPosition, restoreOrbBounds, serializePosition, workAreaOf } from '../src/window-state.js';

const left = { x: 0, y: 0, width: 1920, height: 1080 };
const right = { x: 1920, y: 0, width: 1440, height: 900 };
const margin = 20;

test('the default orb sits in the lower right of the primary work area', () => {
  assert.deepEqual(defaultOrbBounds(left, margin), { width: 112, height: 112, x: 1920 - 112 - margin, y: 1080 - 112 - margin });
});

test('a saved position is kept on its screen, and clamped when that screen got smaller', () => {
  assert.deepEqual(restoreOrbBounds({ x: 2100, y: 300 }, [left, right], left, margin), { ...ORB_SIZE, x: 2100, y: 300 });
  const shrunk = { x: 1920, y: 0, width: 1280, height: 720 };
  assert.deepEqual(restoreOrbBounds({ x: 3100, y: 700 }, [left, shrunk], left, margin), { ...ORB_SIZE, x: 1920 + 1280 - 112, y: 720 - 112 });
});

test('a position on a screen that is gone, or no saved position, falls back to the primary default', () => {
  const fallback = defaultOrbBounds(left, margin);
  assert.deepEqual(restoreOrbBounds({ x: 2100, y: 300 }, [left], left, margin), fallback);
  assert.deepEqual(restoreOrbBounds(undefined, [left, right], left, margin), fallback);
});

test('the work area of a rectangle is the one holding most of it', () => {
  assert.equal(workAreaOf({ x: 1900, y: 0, width: 112, height: 112 }, [left, right]), right);
  assert.equal(workAreaOf({ x: 5000, y: 0, width: 112, height: 112 }, [left, right]), undefined);
  assert.deepEqual(clampInto({ x: -50, y: 2000, width: 112, height: 112 }, left), { x: 0, y: 1080 - 112, width: 112, height: 112 });
});

test('the panel opens toward the middle of the screen, keeping the orb corner in place', () => {
  const lowerRight = defaultOrbBounds(left, margin);
  const opened = panelBounds(lowerRight, left);
  assert.equal(opened.placement, 'up-left');
  assert.deepEqual(opened.bounds, { ...PANEL_SIZE, x: lowerRight.x + 112 - 360, y: lowerRight.y + 112 - 400 });
  const upperLeft = { ...ORB_SIZE, x: 30, y: 40 };
  assert.equal(panelPlacement(upperLeft, left), 'down-right');
  assert.deepEqual(panelBounds(upperLeft, left).bounds, { ...PANEL_SIZE, x: 30, y: 40 });
  assert.equal(panelPlacement({ ...ORB_SIZE, x: 30, y: 900 }, left), 'up-right');
  assert.equal(panelPlacement({ ...ORB_SIZE, x: 1700, y: 40 }, left), 'down-left');
});

test('a panel that would leave the work area is moved inside it, and closing returns the orb to its own corner', () => {
  const nearTop = { ...ORB_SIZE, x: 1500, y: 700 };
  const small = { x: 0, y: 0, width: 1600, height: 760 };
  const { placement, bounds } = panelBounds(nearTop, small);
  assert.equal(placement, 'up-left');
  assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 1600 && bounds.y + bounds.height <= 760);
  assert.deepEqual(orbBoundsIn(panelBounds(defaultOrbBounds(left, margin), left).bounds, 'up-left'), defaultOrbBounds(left, margin));
});

test('only a position is stored, and damaged files read as none', () => {
  assert.deepEqual(parseSavedPosition(serializePosition({ x: 10.4, y: 20.6, width: 112, height: 112 })), { x: 10, y: 21 });
  for (const text of ['', 'not json', '{}', '{"x":"1","y":2}', 'null', '{"x":1,"y":null}']) assert.equal(parseSavedPosition(text), undefined);
});
