// Visual snapshots of Home (dark and light). Baselines live in e2e/snapshots/. Create or refresh them with
//   UPDATE_SNAPSHOTS=1 node --test e2e/visual.test.mjs
// A page differs when more than 0.4% of its pixels differ; the diff image is written next to the baseline.
import { test, after, before } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';
import { api, openApp, startBackend } from './harness.mjs';

const DIR = join(dirname(fileURLToPath(import.meta.url)), 'snapshots');
mkdirSync(DIR, { recursive: true });
const UPDATE = process.env.UPDATE_SNAPSHOTS === '1';
const MAX_DIFF_SHARE = 0.004;

let backend;
before(async () => {
  backend = await startBackend();
  await api(backend, '/preferences', { method: 'PUT', body: JSON.stringify({ onboarded: true }) });
});
after(() => backend?.stop());

for (const theme of ['dark', 'light']) {
  test(`Home looks the same (${theme})`, async () => {
    const { browser, page } = await openApp(backend, { viewport: { width: 1280, height: 720 }, theme });
    try {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.getByTestId('orb').waitFor();
      await page.waitForTimeout(500);
      const shot = await page.screenshot({ animations: 'disabled', caret: 'hide' });
      const file = join(DIR, `home_${theme}.png`);
      if (UPDATE || !existsSync(file)) {
        writeFileSync(file, shot);
        return;
      }
      const a = PNG.sync.read(readFileSync(file));
      const b = PNG.sync.read(shot);
      assert.deepEqual([b.width, b.height], [a.width, a.height], 'screenshot size changed');
      const diff = new PNG({ width: a.width, height: a.height });
      const different = pixelmatch(a.data, b.data, diff.data, a.width, a.height, { threshold: 0.12 });
      const share = different / (a.width * a.height);
      if (share > MAX_DIFF_SHARE) writeFileSync(join(DIR, `home_${theme}.diff.png`), PNG.sync.write(diff));
      assert.ok(share <= MAX_DIFF_SHARE, `${(share * 100).toFixed(2)}% of pixels differ (limit ${MAX_DIFF_SHARE * 100}%)`);
    } finally {
      await browser.close();
    }
  });
}
