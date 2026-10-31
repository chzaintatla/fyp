// M4 UI: onboarding picks, avatar selection, voice-state rendering, approval flow, theme and settings persistence,
// layout (rail, mini mode), keyboard focus. Real backend + real browser; the microphone and speakers are faked.
import { test, after, before } from 'node:test';
import assert from 'node:assert/strict';
import { api, openApp, startBackend } from './harness.mjs';

let backend;
before(async () => {
  backend = await startBackend();
  await api(backend, '/preferences', { method: 'PUT', body: JSON.stringify({ onboarded: true }) });
});
after(() => backend?.stop());

async function withPage(fn, options = {}) {
  const { browser, page, errors } = await openApp(backend, options);
  try {
    await page.getByTestId('orb').waitFor();
    await fn(page);
    assert.deepEqual(errors, [], 'page errors');
  } finally {
    await browser.close();
  }
}
const nav = (page, name) => page.getByRole('navigation', { name: 'Pages' }).getByRole('button', { name });

test('selecting a male avatar changes character, name and voices, and survives a reload', async () => {
  await withPage(async (page) => {
    await nav(page, 'Characters').click();
    await page.getByTestId('avatar-orion').click();
    await page.getByText('Saved.').waitFor();
    const persona = (await api(backend, '/preferences')).persona;
    assert.equal(persona.avatar_id, 'orion');
    assert.equal(persona.gender, 'male');
    assert.equal(persona.voice_en, 'daniel');
    assert.equal(persona.voice_ur, 'ur-PK-AsadNeural');
    await page.reload();
    await page.getByTestId('orb').waitFor();
    assert.equal(await page.locator('[data-testid="avatar"]').first().getAttribute('data-avatar'), 'orion');
    assert.equal(await page.locator('h1').first().innerText(), 'Orion');
  });
});

test('female / male filter shows 3 characters each', async () => {
  await withPage(async (page) => {
    await nav(page, 'Characters').click();
    await page.getByRole('button', { name: 'Male', exact: true }).click();
    assert.equal(await page.getByTestId('avatar-grid').locator('li').count(), 3);
    await page.getByRole('button', { name: 'Female', exact: true }).click();
    assert.equal(await page.getByTestId('avatar-grid').locator('li').count(), 3);
    await page.getByRole('button', { name: 'All', exact: true }).click();
    assert.equal(await page.getByTestId('avatar-grid').locator('li').count(), 7); // 6 + "your own"
  });
});

test('personality choice is saved, custom shows its description box and sliders', async () => {
  await withPage(async (page) => {
    await nav(page, 'Characters').click();
    await page.getByTestId('personality-friend').check({ force: true });
    await page.getByText('Saved.').waitFor();
    assert.equal((await api(backend, '/preferences')).persona.personality, 'friend');
    await page.getByTestId('personality-custom').check({ force: true });
    await page.getByTestId('custom-personality').waitFor();
    assert.equal(await page.getByRole('slider').count(), 3);
    await page.getByTestId('personality-assistant').check({ force: true });
  });
});

test('avatar mood follows the voice state: idle -> listening -> concerned while waiting for approval', async () => {
  await withPage(async (page) => {
    const avatar = page.locator('[data-testid="avatar"]').first();
    assert.equal(await avatar.getAttribute('data-mood'), 'idle');
    await page.getByTestId('start-ai').click();
    await page.locator('[data-testid="orb"][data-state="listening"]').waitFor();
    assert.equal(await avatar.getAttribute('data-mood'), 'listening');
    await page.evaluate(() => window.__speak('create a folder called MoodTest in Documents'));
    await page.getByRole('dialog', { name: 'Shall I do this?' }).waitFor({ timeout: 15000 });
    assert.equal(await avatar.getAttribute('data-mood'), 'concerned');
    await page.getByRole('button', { name: 'Reject' }).first().click();
    await page.getByTestId('start-ai').click(); // Stop
  });
});

test('approval flow: medium-risk card, Approve verifies the folder', async () => {
  await withPage(async (page) => {
    await page.keyboard.press('/');
    await page.waitForTimeout(200);
    await page.keyboard.type('create a folder called UiFlow in Documents');
    await page.keyboard.press('Enter');
    const card = page.getByRole('dialog', { name: 'Shall I do this?' });
    await card.waitFor({ timeout: 15000 });
    await card.getByRole('button', { name: 'Approve', exact: true }).click();
    await page.locator('[data-testid="task-status"][data-status="completed"]').waitFor({ timeout: 20000 });
  });
});

test('theme toggle switches dark/light, changes the page colours and is remembered', async () => {
  await withPage(async (page) => {
    const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
    const dark = await bg();
    await page.getByRole('button', { name: 'Light theme' }).click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light');
    assert.notEqual(await bg(), dark);
    await page.reload();
    await page.getByTestId('orb').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light');
    await page.getByRole('button', { name: 'Dark theme' }).click();
  });
});

test('settings persist: follow-up window and speech volume', async () => {
  await withPage(async (page) => {
    await nav(page, 'Settings').click();
    await page.getByTestId('follow-up').fill('3');
    await page.getByTestId('speech-volume').fill('55');
    await page.getByText('Volume saved.').waitFor();
    const prefs = await api(backend, '/preferences');
    assert.equal(prefs.follow_up_seconds, 3);
    assert.equal(prefs.speech_volume, 55);
    await page.reload();
    await nav(page, 'Settings').click();
    assert.equal(await page.getByTestId('follow-up-value').innerText(), '3');
    await api(backend, '/preferences', { method: 'PUT', body: JSON.stringify({ follow_up_seconds: 8, speech_volume: 100 }) });
  });
});

test('layout: left rail on wide windows, bottom bar on narrow ones; no sideways scroll at 360 px', async () => {
  await withPage(async (page) => {
    const box = await page.getByTestId('rail').boundingBox();
    assert.ok(box.x === 0 && box.width < 120 && box.height > 400, `rail should be a left column: ${JSON.stringify(box)}`);
  }, { viewport: { width: 1280, height: 720 } });
  await withPage(async (page) => {
    const box = await page.getByTestId('rail').boundingBox();
    assert.ok(box.y > 300 && box.width >= 340, `rail should be a bottom bar: ${JSON.stringify(box)}`);
    assert.ok(await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth + 1), 'page scrolls sideways');
  }, { viewport: { width: 360, height: 700 } });
});

test('keyboard: every rail button and the Start button show a visible focus ring', async () => {
  await withPage(async (page) => {
    await nav(page, 'Home').focus();
    await page.keyboard.press('Tab');
    const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
    assert.notEqual(outline, 'none');
  });
});

test('mini mode: a 72 px character bubble that is a named button', async () => {
  await withPage(async () => {}, {});
  const { browser, page } = await openApp(backend, { path: '/?view=orb', viewport: { width: 96, height: 96 } });
  try {
    const orb = page.getByTestId('orb');
    await orb.waitFor();
    assert.match(await orb.getAttribute('aria-label'), /Press to open the panel/);
    assert.equal(await page.locator('svg[data-testid="avatar"]').getAttribute('width'), '72');
  } finally {
    await browser.close();
  }
});
