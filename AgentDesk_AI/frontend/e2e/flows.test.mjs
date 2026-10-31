// Main desktop-UI flows against a real backend: keyboard only, high-risk preview, memory, settings, mini orb, ARIA.
import { test, after, before } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { api, openApp, startBackend } from './harness.mjs';

let backend;
before(async () => {
  backend = await startBackend();
  await api(backend, '/preferences', { method: 'PUT', body: JSON.stringify({ onboarded: true, persona: {
    name: 'Agent X', language: 'en-US', style: 'friendly', speak_replies: true, orb_color: 'indigo' } }) });
});
after(() => backend?.stop());

async function withPage(fn, path) {
  const { browser, page, errors } = await openApp(backend, { path });
  try {
    await page.getByTestId('orb').waitFor();  // React mounted, key handlers attached
    await fn(page);
    assert.deepEqual(errors, [], 'page errors');
  } finally {
    await browser.close();
  }
}

const status = (page, value) => page.locator(`[data-testid="task-status"][data-status="${value}"]`).waitFor({ timeout: 20000 });

test('keyboard only: "/" focuses the command box, Enter runs, Enter approves the focused button', async () => {
  await withPage(async (page) => {
    await page.keyboard.press('/');
    await page.waitForFunction(() => document.activeElement?.id === 'command-input');
    await page.keyboard.type('create folder KB in Documents');
    await page.keyboard.press('Enter');
    const preview = page.getByRole('dialog', { name: 'Shall I do this?' });
    await preview.waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Approve');
    // Tab stays inside the dialog; the page behind it is inert (only the dialog's Approve is reachable).
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    assert.ok(await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]'))));
    const cdp = await page.context().newCDPSession(page);
    const { nodes } = await cdp.send('Accessibility.getFullAXTree');  // the browser's real a11y tree honours inert
    const approves = nodes.filter((n) => !n.ignored && n.role?.value === 'button' && n.name?.value === 'Approve');
    assert.equal(approves.length, 1, 'background controls must be inert while the dialog is open');
    await preview.getByRole('button', { name: 'Approve', exact: true }).focus();
    await page.keyboard.press('Enter');
    await status(page, 'completed');
    assert.ok(existsSync(join(backend.home, 'Documents', 'KB')));
  });
});

test('high risk: red card, Reject is focused (Enter cannot approve), Esc rejects, nothing deleted', async () => {
  const file = join(backend.home, 'Documents', 'notes.txt');
  writeFileSync(file, 'keep me');
  await withPage(async (page) => {
    await page.getByLabel('Natural-language command').fill('delete file notes.txt');
    await page.getByRole('button', { name: 'Run command' }).click();
    const preview = page.getByRole('dialog', { name: 'Shall I do this?' });
    await preview.waitFor();
    const card = preview.getByTestId('preview-card');
    assert.equal(await card.getAttribute('data-risk'), 'high');
    assert.match(await card.innerText(), /notes\.txt/);
    assert.match(await card.innerText(), /High risk/);
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Reject');
    // Rule 3: a spoken "yes" cannot approve high risk.
    await preview.getByRole('button', { name: 'Answer by voice' }).click();
    await page.evaluate(() => window.__speak('yes'));
    await preview.getByTestId('preview-note').filter({ hasText: /High risk needs a click on Approve/ }).waitFor({ timeout: 10000 });
    assert.ok(existsSync(file), 'voice yes must not delete');
    await page.getByRole('dialog', { name: 'Shall I do this?' }).getByRole('button', { name: 'Reject' }).focus();
    await page.keyboard.press('Escape');
    await status(page, 'rejected');
    assert.ok(existsSync(file), 'file must not be deleted');
  });
});

test('voice approval is reachable from the preview card ("Answer by voice")', async () => {
  await withPage(async (page) => {
    await page.getByLabel('Natural-language command').fill('create folder Voice in Documents');
    await page.keyboard.press('Enter');
    const preview = page.getByRole('dialog', { name: 'Shall I do this?' });
    await preview.waitFor();
    await preview.getByRole('button', { name: 'Answer by voice' }).click();
    await page.evaluate(() => window.__speak('yes'));
    await status(page, 'completed');
    assert.ok(existsSync(join(backend.home, 'Documents', 'Voice')));
  });
});

test('memory page: add, edit, delete; secrets refused', async () => {
  await withPage(async (page) => {
    await page.keyboard.press('Alt+3');
    await page.getByTestId('memory-page').waitFor();
    await page.getByLabel('New memory').fill('6th sem means D:\\6th sem');
    await page.getByRole('button', { name: 'Remember' }).click();
    const item = page.getByTestId('memory-item');
    await item.filter({ hasText: '6th sem means' }).waitFor();
    await page.getByRole('button', { name: /^Edit: 6th sem/ }).click();
    await page.getByLabel('Edit memory').fill('lab means D:\\cn\\lab');
    await page.getByRole('button', { name: 'Save' }).click();
    await item.filter({ hasText: 'lab means' }).waitFor();
    await page.getByLabel('New memory').fill('my password: hunter22');
    await page.getByRole('button', { name: 'Remember' }).click();
    await page.getByRole('alert').filter({ hasText: /422/ }).waitFor();
    page.once('dialog', (d) => d.accept());
    await page.getByRole('button', { name: /^Delete: lab means/ }).click();
    await page.getByText('Nothing saved yet.').waitFor();
  });
});

test('settings: persona name, strict policy and listen mode take effect', async () => {
  await withPage(async (page) => {
    await page.keyboard.press('Alt+4');
    await page.getByTestId('settings-page').waitFor();
    await page.getByLabel('Assistant name').fill('Zara');
    await page.getByRole('button', { name: 'Save persona' }).click();
    await page.getByRole('heading', { level: 1, name: 'Zara' }).waitFor();
    await page.getByLabel(/^Strict/).check();
    await page.getByRole('status').filter({ hasText: 'Approval policy saved.' }).waitFor();
    await page.getByLabel(/^Off/).check();
    await page.getByRole('status').filter({ hasText: 'Listen mode saved.' }).waitFor();
    assert.equal(await page.getByText('Usage', { exact: true }).count(), 1);
    await page.keyboard.press('Alt+1');
    assert.equal(await page.getByRole('button', { name: /Speak/ }).count(), 0, 'mic hidden in Off mode');
    // Strict: even a low-risk listing waits for approval.
    await page.getByLabel('Natural-language command').fill(`list folder ${join(backend.home, 'Documents')}`);
    await page.keyboard.press('Enter');
    await page.getByRole('dialog', { name: 'Shall I do this?' }).waitFor();
    assert.equal(await page.getByTestId('preview-card').getAttribute('data-risk'), 'low');
    await page.keyboard.press('Escape');
  });
  await api(backend, '/preferences', { method: 'PUT', body: JSON.stringify({ risk_policy: 'standard', listen_mode: 'push_to_talk' }) });
});

test('mini orb page shows state and is a named, keyboard-reachable button', async () => {
  await withPage(async (page) => {
    const orb = page.getByTestId('orb');
    await orb.waitFor();
    assert.match(await orb.getAttribute('aria-label'), /Press to open the panel/);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.testid), 'orb');
  }, '/?view=orb');
});

test('every visible control has an accessible name', async () => {
  await withPage(async (page) => {
    for (const key of ['Alt+1', 'Alt+2', 'Alt+3', 'Alt+4']) {
      await page.keyboard.press(key);
      await page.waitForTimeout(300);
      const unnamed = await page.evaluate(() => {
        const name = (el) => {
          if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
          const by = el.getAttribute('aria-labelledby');
          if (by) return document.getElementById(by)?.textContent;
          if (el.id && document.querySelector(`label[for="${el.id}"]`)) return 'label';
          if (el.closest('label')) return 'label';
          if (['BUTTON', 'A'].includes(el.tagName)) return el.textContent.trim();
          return '';
        };
        return [...document.querySelectorAll('button, a, input, select, textarea')]
          .filter((el) => el.offsetParent !== null && el.type !== 'hidden')
          .filter((el) => !name(el))
          .map((el) => el.outerHTML.slice(0, 100));
      });
      assert.deepEqual(unnamed, [], `unnamed controls on ${key}`);
    }
  });
});
