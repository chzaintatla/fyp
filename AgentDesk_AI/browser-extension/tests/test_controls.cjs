const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const tabs = new Map([
  [1, { id: 1, url: 'https://drive.google.com/drive/u/0/', title: 'Drive', incognito: false }],
  [2, { id: 2, url: 'https://www.youtube.com/watch?v=test', title: 'Video', incognito: false }],
]);
const removed = [];
const reloads = [];
const video = { paused: false, pause() { this.paused = true; }, async play() { this.paused = false; } };
const event = { addListener() {} };
const context = vm.createContext({
  URL, WebSocket: { OPEN: 1, CONNECTING: 0 },
  navigator: { userAgent: 'Chrome/130' },
  fetch: async () => { throw new Error('offline test'); },
  setInterval: () => 0, clearInterval() {}, setTimeout,
  document: { querySelector: () => video },
  chrome: {
    tabs: {
      query: async () => [...tabs.values()],
      get: async id => { if (!tabs.has(id)) throw new Error('missing tab'); return tabs.get(id); },
      remove: async id => { removed.push(id); tabs.delete(id); },
      // Like Chrome, the created tab reports the normalised URL (trailing slash added).
      create: async ({ url }) => { const tab = { id: 3, url: new URL(url).href, windowId: 10, incognito: false }; tabs.set(3, tab); return tab; },
      update: async id => tabs.get(id),
    },
    windows: { update: async () => {} },
    scripting: { executeScript: async options => [{ result: await options.func(...options.args) }] },
    action: { setBadgeText() {}, setBadgeBackgroundColor() {}, onClicked: event },
    runtime: { id: "ext-id", onInstalled: event, onStartup: event, onMessage: event,
      getManifest: () => ({ version: '1.1.0' }), reload: () => { reloads.push(1); } },
    storage: { local: { get: async () => ({}), set: async () => {} } },
    alarms: { create() {}, onAlarm: event },
  },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8'), context);
(async () => {
  let result = await context.command({ action: 'pause', tab_id: 2, expected_url: tabs.get(2).url });
  assert.equal(result.paused, true);
  result = await context.command({ action: 'pause', tab_id: 2, expected_url: tabs.get(2).url });
  assert.equal(result.paused, true, 'second pause must not resume');
  result = await context.command({ action: 'resume', tab_id: 2, expected_url: tabs.get(2).url });
  assert.equal(result.paused, false);
  await assert.rejects(context.command({ action: 'close', tab_id: 1, expected_url: 'https://mail.google.com/' }));
  assert.equal(removed.length, 0, 'navigated tab must not be closed');
  result = await context.command({ action: 'close', tab_id: 1, expected_url: tabs.get(1).url });
  assert.equal(result.closed, true);
  assert.deepEqual(removed, [1]);
  assert.equal(tabs.has(2), true, 'unrelated YouTube tab must remain open');
  result = await context.command({ action: 'open', url: 'https://www.youtube.com', browser: 'chrome' });
  assert.equal(result.tab_id, 3);
  const count = tabs.size;
  result = await context.command({ action: 'open', url: 'https://www.youtube.com', browser: 'chrome' });
  assert.equal(result.tab_id, 3);
  assert.equal(tabs.size, count, 'opening the same exact URL must reuse the unique tab');
  // Regression: https://site (no slash) must match the tab Chrome reports as https://site/.
  result = await context.command({ action: 'open', url: 'https://mail.google.com', browser: 'chrome' });
  assert.equal(result.success, true);
  result = await context.command({ action: 'reload' });
  assert.equal(result.reloading, true);
  assert.equal(result.version, '1.1.0');
  await new Promise(r => setTimeout(r, 300));
  assert.equal(reloads.length, 1, 'reload command must reload the extension');
  assert.ok(['inspect', 'open', 'close', 'pause', 'resume', 'reload', 'switch', 'seek', 'next', 'click_result', 'scroll'].every(c => vm.runInContext('CAPABILITIES', context).includes(c)));
  await assert.rejects(context.command({ action: 'open', url: 'javascript:alert(1)', browser: 'chrome' }));
  await assert.rejects(context.command({ action: 'open', url: 'https://www.youtube.com', browser: 'edge' }));
  console.log('Browser extension checks passed: idempotent media state, stale URL guard, exact tab close, URL normalisation, self-reload.');
})().catch(error => { console.error(error); process.exitCode = 1; });
