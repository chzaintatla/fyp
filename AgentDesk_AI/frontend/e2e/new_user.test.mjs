// Gate: a new user can give a voice command and approve it in under 3 minutes.
import { test, after, before } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { startBackend, openApp } from './harness.mjs';

let backend;
before(async () => { backend = await startBackend(); });
after(() => backend?.stop());

test('new user: onboarding -> voice command -> preview card -> approve -> verified, under 3 minutes', async () => {
  const started = Date.now();
  const { browser, page, errors } = await openApp(backend);
  try {
    // First run shows the guided onboarding.
    const dialog = page.getByRole('dialog', { name: /Welcome to Agent X/ });
    await dialog.waitFor();
    await page.getByLabel('Language you will speak').selectOption('en-US');
    await page.getByRole('button', { name: 'Next' }).click();
    await page.getByRole('heading', { name: 'Choose your character' }).waitFor();
    await page.getByTestId('onboarding-avatar-orion').click(); // a male character
    await page.getByRole('button', { name: 'Next' }).click();
    await page.getByRole('heading', { name: 'Choose a personality' }).waitFor();
    await page.getByTestId('onboarding-personality-friend').check({ force: true });
    await page.getByRole('button', { name: 'Next' }).click();
    await page.getByRole('heading', { name: 'Questions and privacy' }).waitFor();
    await page.getByTestId('onboarding-cloud-no').check({ force: true }); // the choice is the user's: tested both ways in ui.test.mjs
    await page.getByRole('button', { name: 'Next' }).click();
    await page.getByRole('heading', { name: 'Microphone' }).waitFor();
    await page.getByRole('button', { name: 'Next' }).click();
    const example = await page.getByTestId('onboarding-example').innerText();
    assert.match(example, /Create a folder called Demo in Documents/);
    await page.getByTestId('onboarding').getByRole('button', { name: 'Start', exact: true }).click();

    // The orb shows it is listening, then the live transcript shows the words.
    await page.locator('[data-testid="orb"][data-state="listening"]').waitFor();
    await page.evaluate(() => window.__speak('create a folder called Demo in Documents'));
    await page.getByTestId('transcript-heard').filter({ hasText: 'create a folder called Demo in Documents' }).waitFor();

    // Action preview: medium risk, exact target, Approve focused.
    const preview = page.getByRole('dialog', { name: 'Shall I do this?' });
    await preview.waitFor({ timeout: 15000 });
    const card = preview.getByTestId('preview-card');
    assert.equal(await card.getAttribute('data-risk'), 'medium');
    assert.match(await card.innerText(), /Demo/);
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Approve');
    await preview.getByRole('button', { name: 'Approve', exact: true }).click();

    // Completed only after verification; the folder really exists.
    await page.locator('[data-testid="task-status"][data-status="completed"]').waitFor({ timeout: 20000 });
    assert.ok(existsSync(join(backend.home, 'Documents', 'Demo')), 'folder was not created');
    await page.getByTestId('transcript-reply').waitFor();
    const seconds = (Date.now() - started) / 1000;
    console.log(`new-user voice flow completed in ${seconds.toFixed(1)} s (automated)`);
    assert.ok(seconds < 180);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
