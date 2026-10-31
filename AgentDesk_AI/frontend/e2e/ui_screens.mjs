// Saves a screenshot of every page, in both themes, at 100% (1920x1080) and 150% (1280x720 CSS px at 1.5x) scaling
// into docs/ui/, and fails if any page scrolls sideways. Run from frontend/ after `npm run build`:
//   node e2e/ui_screens.mjs
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { api, openApp, startBackend } from './harness.mjs';

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'ui');
mkdirSync(OUT, { recursive: true });
const SCALES = [
  { name: '100', viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 },
  { name: '150', viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5 },
];
const PAGES = [['home', 'Home'], ['characters', 'Characters'], ['memory', 'Memory'], ['settings', 'Settings']];

const backend = await startBackend();
const report = [];
let failed = false;
try {
  for (const theme of ['dark', 'light']) {
    for (const scale of SCALES) {
      const fresh = await openApp(backend, { viewport: scale.viewport, deviceScaleFactor: scale.deviceScaleFactor, theme });
      await fresh.page.getByTestId('onboarding').waitFor();
      await fresh.page.screenshot({ path: join(OUT, `onboarding_${theme}_${scale.name}.png`) });
      await fresh.browser.close();
    }
  }
  await api(backend, '/preferences', { method: 'PUT', body: JSON.stringify({ onboarded: true }) });
  for (const theme of ['dark', 'light']) {
    for (const scale of SCALES) {
      const { browser, page } = await openApp(backend, { viewport: scale.viewport, deviceScaleFactor: scale.deviceScaleFactor, theme });
      await page.getByTestId('orb').waitFor();
      for (const [id, label] of PAGES) {
        await page.getByRole('navigation', { name: 'Pages' }).getByRole('button', { name: label }).click();
        await page.waitForTimeout(400);
        const file = `${id}_${theme}_${scale.name}.png`;
        await page.screenshot({ path: join(OUT, file), fullPage: true });
        const { sw, iw } = await page.evaluate(() => ({ sw: document.scrollingElement.scrollWidth, iw: window.innerWidth }));
        const ok = sw <= iw + 1;
        failed ||= !ok;
        report.push({ file, scrollWidth: sw, innerWidth: iw, ok });
      }
      // The approval card (a task that needs confirmation).
      await page.getByRole('navigation', { name: 'Pages' }).getByRole('button', { name: 'Home' }).click();
      await page.keyboard.press('/');
      await page.waitForTimeout(200);
      await page.keyboard.type(`create a folder called Shot${theme}${scale.name} in Documents`);
      await page.keyboard.press('Enter');
      await page.getByRole('dialog', { name: 'Shall I do this?' }).waitFor({ timeout: 15000 }).catch(async (e) => { await page.screenshot({ path: join(OUT, 'debug_fail.png') }); throw e; });
      await page.screenshot({ path: join(OUT, `approval_${theme}_${scale.name}.png`) });
      await page.getByRole('button', { name: 'Reject' }).first().click();
      await page.waitForTimeout(400);
      await browser.close();
    }
  }
  // Mini mode (the 72 px floating bubble).
  const mini = await openApp(backend, { path: '/?view=orb', viewport: { width: 96, height: 96 }, deviceScaleFactor: 1.5, theme: 'dark' });
  await mini.page.waitForTimeout(800);
  await mini.page.screenshot({ path: join(OUT, 'mini_dark_150.png') });
  await mini.browser.close();
} finally {
  await backend.stop();
}
writeFileSync(join(OUT, 'report.json'), JSON.stringify(report, null, 1));
console.table(report);
if (failed) {
  console.error('FAIL: a page scrolls sideways');
  process.exit(1);
}
console.log(`saved to ${OUT}`);
