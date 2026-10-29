import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BOX_MAX_HEIGHT, BOX_WIDTH, ORB_MARGIN, ORB_SIZE, boxLayout, defaultOrbBounds } from '../src/window-state.js';

const workArea = { x: 0, y: 0, width: 1920, height: 1080 };
const orb = defaultOrbBounds(workArea);

test('the orb starts 24 px in from the lower right of the work area', () => {
  assert.equal(ORB_MARGIN, 24);
  assert.deepEqual(orb, { ...ORB_SIZE, x: 1920 - 112 - 24, y: 1080 - 112 - 24 });
  const offset = { x: 1920, y: 40, width: 1440, height: 860 };
  assert.deepEqual(defaultOrbBounds(offset), { ...ORB_SIZE, x: 1920 + 1440 - 112 - 24, y: 40 + 860 - 112 - 24 });
});

test('with the box closed the window is the orb alone', () => {
  assert.deepEqual(boxLayout(orb, workArea, 0), { side: 'below', orbLeft: 0, bounds: orb });
});

test('the box opens below the orb when it fits', () => {
  const high = { ...ORB_SIZE, x: 800, y: 100 };
  const layout = boxLayout(high, workArea, 200);
  assert.equal(layout.side, 'below');
  assert.deepEqual(layout.bounds, { x: 800 + 56 - BOX_WIDTH / 2, y: 100, width: BOX_WIDTH, height: 112 + 200 });
  assert.equal(layout.orbLeft, BOX_WIDTH / 2 - 56);
});

test('the box flips above the orb when it would go off the bottom, and the orb stays put on screen', () => {
  const layout = boxLayout(orb, workArea, BOX_MAX_HEIGHT);
  assert.equal(layout.side, 'above');
  assert.equal(layout.bounds.y, orb.y - BOX_MAX_HEIGHT);
  assert.equal(layout.bounds.y + layout.bounds.height, orb.y + orb.height);
  assert.equal(layout.bounds.x + layout.orbLeft, orb.x);
});

test('the box stays below exactly while it ends inside the work area', () => {
  const edge = { ...ORB_SIZE, x: 800, y: 1080 - 112 - 100 };
  assert.equal(boxLayout(edge, workArea, 100).side, 'below');
  assert.equal(boxLayout(edge, workArea, 101).side, 'above');
});

test('the window is kept inside the work area sideways, and the orb is placed inside it', () => {
  const right = boxLayout(orb, workArea, 100);
  assert.equal(right.bounds.x + right.bounds.width, workArea.x + workArea.width);
  assert.equal(right.bounds.x + right.orbLeft, orb.x);
  const left = boxLayout({ ...ORB_SIZE, x: 10, y: 100 }, workArea, 100);
  assert.equal(left.bounds.x, 0);
  assert.equal(left.orbLeft, 10);
  const second = { x: 1920, y: 0, width: 1440, height: 900 };
  const onSecond = boxLayout({ ...ORB_SIZE, x: 1930, y: 100 }, second, 100);
  assert.equal(onSecond.bounds.x, 1920);
});
