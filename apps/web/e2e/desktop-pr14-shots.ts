/** UI integration check for Electron's shared PR14 shell (native bridge is mocked).
 * Build first, then CHROME_PATH=/path/to/chrome node apps/web/e2e/desktop-pr14-shots.ts [outDir].
 */
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { preview } from 'vite';
import { chromium } from 'playwright-core';
import { MOCK_REGISTRY } from '../test/fixtures/mockRegistry.ts';
const out = process.argv[2] ?? '/tmp/osmo-desktop-pr14';
await mkdir(out, { recursive: true });
const server = await preview({ root: new URL('../', import.meta.url).pathname, preview: { host: '127.0.0.1', port: 4173, strictPort: true } });
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH ?? '/usr/bin/google-chrome', headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'] });
const errors: string[] = [];
try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 820 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/providers', route => route.fulfill({ json: { providers: [{ id: 'xai', label: 'xAI', authKinds: ['api_key'], connected: true }] } }));
  await page.route('**/voice-models', route => route.fulfill({ json: { type: 'models', models: MOCK_REGISTRY, selection: { model: 'grok-voice-latest', voice: 'eve' } } }));
  await page.route('**/api/threads', route => route.fulfill({ json: { threads: [] } }));
  await page.route('**/api/runs?*', route => route.fulfill({ json: { runs: [], approvals: [] } }));
  await page.addInitScript(() => {
    Object.assign(window, { osmoDesktop: {
      status: async () => ({ enabled: false, platform: 'darwin', stopShortcut: 'CommandOrControl+Shift+Escape' }),
      setEnabled: async () => ({ enabled: false, platform: 'darwin', stopShortcut: 'CommandOrControl+Shift+Escape' }),
      onStatus: () => () => {}, execute: async () => { throw new Error('Native actions are outside this UI check'); },
    } });
  });
  for (const theme of ['dark', 'light']) {
    await page.goto(`http://127.0.0.1:4173/?theme=${theme}`);
    await page.getByRole('button', { name: /^New thread/ }).waitFor();
    await page.locator('.rp-orb').waitFor();
    assert.equal(await page.locator('.rp-summary, .osmo-pill, .osmo-model-picker').count(), 0);
    assert(await page.getByRole('button', { name: 'Computer support', exact: true }).isVisible());
    await page.screenshot({ path: `${out}/desktop-${theme}.png`, animations: 'disabled' });
    await page.locator('.rp-orb').hover();
    await page.waitForFunction(() => document.querySelector('.rp')?.getAttribute('data-open') === 'true');
    assert(await page.getByRole('listbox', { name: 'Providers', exact: true }).isVisible());
    assert.equal(await page.locator('.rp-summary').count(), 0);
    await page.screenshot({ path: `${out}/orb-picker-${theme}.png`, animations: 'disabled' });
    await page.mouse.move(10, 10);
  }
  await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
  assert(await page.getByRole('button', { name: 'Computer support', exact: true }).isVisible());
  await page.setViewportSize({ width: 390, height: 820 });
  await page.screenshot({ path: `${out}/desktop-narrow.png`, animations: 'disabled' });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  assert.deepEqual(errors, []);
  console.log('Persistent sidebar, native controls, dark/light layout, orb-only model selection and narrow viewport passed.');
} finally { await browser.close(); await new Promise<void>(resolve => server.httpServer.close(() => resolve())); }
