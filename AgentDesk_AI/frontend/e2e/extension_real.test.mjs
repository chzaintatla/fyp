// Real-browser run of the Agent X Browser Bridge: real Chromium, the real unpacked extension,
// paired through the real setup page, driven through the real task API. Records a JSON live run.
import { test, after, before } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { api, startBackend } from './harness.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const HERE = dirname(fileURLToPath(import.meta.url));
const EXTENSION = resolve(HERE, '..', '..', 'browser-extension');
const RECORD_DIR = resolve(HERE, '..', '..', 'docs', 'live_runs');

let backend;
let context;
const record = { tool: 'control_browser + open_url (Agent X Browser Bridge)', started: new Date().toISOString(), steps: [] };

before(async () => {
  backend = await startBackend({ port: 8000 });  // the extension is pinned to 127.0.0.1:8000
  context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'agentx-chrome-')), {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  });
  record.browser = `Chromium ${context.browser()?.version() ?? ''} (headless, real extension)`;
});
after(async () => {
  await context?.close();
  backend?.stop();
});

async function waitFor(check, timeout = 20000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 300));
  }
}

async function runCommand(command) {
  let task = await api(backend, '/tasks', { method: 'POST', body: JSON.stringify({ command }) });
  if (task.status === 'awaiting_approval') {
    const ids = task.actions.filter((a) => a.status === 'pending').map((a) => a.id);
    await api(backend, `/tasks/${task.id}/approve`, { method: 'POST', body: JSON.stringify({ action_ids: ids }) });
    task = await api(backend, `/tasks/${task.id}/run`, { method: 'POST' });
  }
  record.steps.push({ command, status: task.status, error: task.error,
    actions: task.actions.map((a) => ({ tool: a.tool_name, args: a.args, status: a.status, verification: a.verification })) });
  return task;
}

test('real extension: pair, version handshake, list tabs, open tab via extension, close it', async () => {
  const page = await context.newPage();
  await page.goto(`${backend.url}/browser-extension/setup.html#token=${encodeURIComponent(backend.token)}`);
  await page.getByText('Paired.').waitFor({ timeout: 10000 });
  const status = await waitFor(async () => { const s = await api(backend, '/browser/status'); return s.connected && s; });
  assert.equal(status.version, '1.2.0');
  assert.equal(status.outdated, false);
  record.steps.push({ step: 'pair + handshake', status });

  let task = await runCommand('list tabs');
  assert.equal(task.status, 'completed', task.error);
  assert.equal(task.actions[0].verification.passed, true);

  task = await runCommand('gmail chrome mein kholo');
  assert.equal(task.actions[0].tool_name, 'open_url');
  assert.equal(task.status, 'completed', task.error);
  const tabId = task.actions[0].result.output.tab_id;
  assert.ok(Number.isInteger(tabId));
  // The verifier already re-inspected the tab through the extension; also record what the browser shows.
  const tabs = await api(backend, '/tasks', { method: 'POST', body: JSON.stringify({ command: 'list tabs' }) });
  const listed = tabs.actions[0].result.output.tabs;
  record.steps.push({ step: 'tabs after open', tabs: listed });
  assert.ok(listed.some((t) => t.id === tabId), 'the opened tab is listed by the extension');

  task = await runCommand(`close tab ${tabId}`);
  assert.equal(task.status, 'completed', JSON.stringify(record.steps.at(-1)));
  assert.equal(task.actions[0].verification.passed, true);
  const after = await api(backend, '/tasks?limit=1');
  record.finished = new Date().toISOString();
  record.result = 'pass';
  mkdirSync(RECORD_DIR, { recursive: true });
  writeFileSync(join(RECORD_DIR, `${record.finished.slice(0, 10)}-browser-extension.json`), JSON.stringify(record, null, 2));
  assert.ok(after.length >= 1);
});
