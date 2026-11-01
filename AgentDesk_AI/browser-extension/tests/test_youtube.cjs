// The YouTube page controls of the extension (1.2): seek, speed, volume, mute, full screen, next, results, click result N, scroll, switch, read.
// The page is a small fake DOM; every action must answer with what the page shows AFTERWARDS, and bad input must never reach the page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const tabs = new Map([
  [1, { id: 1, url: 'https://drive.google.com/drive/u/0/', title: 'Drive', incognito: false, active: false, windowId: 1 }],
  [2, { id: 2, url: 'https://www.youtube.com/watch?v=aaa', title: 'First video', incognito: false, active: false, windowId: 1 }],
]);
const focused = [];
const video = { currentTime: 100, duration: 600, paused: false, playbackRate: 1, volume: 1, muted: false };
const location = { href: 'https://www.youtube.com/watch?v=aaa', origin: 'https://www.youtube.com', pathname: '/watch' };
const go = (v) => { location.href = `https://www.youtube.com/watch?v=${v}`; location.pathname = '/watch'; };
const results = ['rrr1', 'rrr2', 'rrr3'].map((v, i) => {
  const a = { href: `https://www.youtube.com/watch?v=${v}`, textContent: `  Result number ${i + 1}  `, click() { go(v); } };
  return { querySelector: (sel) => (sel === 'a#video-title' ? a : null), anchor: a };
});
let resultsPage = false;
let fullscreenAllowed = true;
let nextButton = { getAttribute: () => null, hidden: false, click() { go('next1'); } };
let relatedLink = null;
const player = { requestFullscreen: async () => { if (!fullscreenAllowed) throw new Error('needs a user gesture'); document.fullscreenElement = player; } };
const document = {
  title: 'First video - YouTube', fullscreenElement: null, documentElement: { scrollHeight: 5000 },
  querySelector(sel) {
    if (sel === 'video') return video;
    if (sel === '#movie_player') return player;
    if (sel === '.ytp-next-button') return nextButton;
    if (sel.startsWith('ytd-compact-video-renderer')) return relatedLink;
    return null;
  },
  querySelectorAll(sel) {
    if (sel === 'ytd-video-renderer') return resultsPage ? results : [];
    if (sel === 'ytd-video-renderer a#video-title') return resultsPage ? results.map((r) => r.anchor) : [];
    return [];
  },
  async exitFullscreen() { document.fullscreenElement = null; },
};
const window = { scrollY: 0, innerHeight: 800, scrollBy(x, y) { this.scrollY = Math.max(0, Math.min(4200, this.scrollY + y)); } };
const event = { addListener() {} };
const context = vm.createContext({
  URL, WebSocket: { OPEN: 1, CONNECTING: 0 }, navigator: { userAgent: 'Chrome/130' }, fetch: async () => { throw new Error('offline'); },
  setInterval: () => 0, clearInterval() {}, setTimeout, document, location, window, isFinite, Math, Number,
  chrome: {
    tabs: {
      query: async () => [...tabs.values()],
      get: async (id) => { if (!tabs.has(id)) throw new Error('missing tab'); return tabs.get(id); },
      update: async (id, props) => { Object.assign(tabs.get(id), props); return tabs.get(id); },
      remove: async () => {}, create: async () => ({}),
    },
    windows: { update: async (id, props) => { focused.push([id, props]); } },
    scripting: { executeScript: async (o) => [{ result: await o.func(...o.args) }] },
    action: { setBadgeText() {}, setBadgeBackgroundColor() {}, onClicked: event },
    runtime: { id: 'ext-id', onInstalled: event, onStartup: event, onMessage: event, getManifest: () => ({ version: '1.2.0' }), reload() {} },
    storage: { local: { get: async () => ({}), set: async () => {} } },
    alarms: { create() {}, onAlarm: event },
  },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8'), context);
const yt = () => ({ tab_id: 2, expected_url: tabs.get(2).url });
const run = (action, extra = {}) => context.command({ action, ...yt(), ...extra });

(async () => {
  let r = await run('seek', { seconds: 10 });
  assert.equal(r.success, true); assert.equal(r.time, 110); assert.equal(r.target, 110);
  r = await run('seek', { seconds: -3000 });
  assert.equal(r.time, 0, 'seeking before the start stops at 0');
  r = await run('seek', { position: 9999 });
  assert.equal(r.time, 600, 'seeking past the end stops at the end');
  r = await run('seek', { position: 300 });
  assert.equal(r.time, 300);
  await assert.rejects(run('seek', {}), /seconds or a position/);
  await assert.rejects(run('seek', { seconds: 'x' }), /seconds or a position/);

  r = await run('speed', { rate: 1.5 }); assert.equal(r.success, true); assert.equal(r.rate, 1.5);
  await assert.rejects(run('speed', { rate: 9 }), /between 0.25 and 4/);
  assert.equal(video.playbackRate, 1.5, 'a rejected speed must not touch the player');

  video.muted = true;
  r = await run('volume', { level: 40 }); assert.equal(r.success, true); assert.equal(r.volume, 40); assert.equal(r.muted, false, 'setting a volume also unmutes');
  await assert.rejects(run('volume', { level: 150 }), /0 to 100/);
  r = await run('mute'); assert.equal(r.muted, true);
  r = await run('unmute'); assert.equal(r.muted, false);

  r = await run('fullscreen'); assert.equal(r.success, true); assert.equal(r.fullscreen, true);
  r = await run('fullscreen', { exit: true }); assert.equal(r.success, true); assert.equal(r.fullscreen, false);
  fullscreenAllowed = false;
  r = await run('fullscreen');
  assert.equal(r.success, false); assert.match(r.error, /real key press/); assert.equal(r.fullscreen, false, 'a blocked request is reported, not claimed');
  fullscreenAllowed = true;

  r = await run('next');
  assert.equal(r.success, true); assert.equal(r.video_id, 'next1'); assert.equal(r.previous_video_id, 'aaa'); assert.equal(r.changed, true);
  go('aaa'); nextButton = null; relatedLink = { click() { go('related9'); } };
  r = await run('next'); assert.equal(r.video_id, 'related9', 'without a next button the first related video is opened');
  go('aaa'); relatedLink = null;
  r = await run('next'); assert.equal(r.success, false); assert.match(r.error, /no next video/);

  r = await run('results'); assert.equal(r.success, false); assert.match(r.error, /not a YouTube results page/);
  resultsPage = true; location.pathname = '/results';
  r = await run('results');
  assert.deepEqual([...r.results], ['Result number 1', 'Result number 2', 'Result number 3']);
  r = await run('click_result', { index: 2 });
  assert.equal(r.success, true); assert.equal(r.video_id, 'rrr2'); assert.equal(r.clicked_title, 'Result number 2');
  location.pathname = '/results'; go('aaa'); location.pathname = '/results';
  r = await run('click_result', { index: 9 }); assert.equal(r.success, false); assert.match(r.error, /only 3 results/);
  await assert.rejects(run('click_result', { index: 0 }), /1 to 20/);
  await assert.rejects(run('click_result', { index: 1.5 }), /1 to 20/);
  resultsPage = false;
  r = await run('click_result', { index: 1 }); assert.equal(r.success, false);

  r = await run('scroll', { amount: 2 }); assert.equal(r.success, true); assert.equal(r.moved, true); assert.ok(r.scroll_y > 1000);
  r = await run('scroll', { amount: -10 }); assert.equal(r.scroll_y, 0);
  r = await run('scroll', { amount: -1 }); assert.equal(r.success, false, 'already at the top: reported, not claimed'); assert.match(r.error, /did not move/);
  await assert.rejects(run('scroll', { amount: 0 }), /scroll amount/);
  await assert.rejects(run('scroll', { amount: 50 }), /scroll amount/);

  r = await context.command({ action: 'read', tab_id: 2, expected_url: tabs.get(2).url });
  assert.equal(r.title, 'First video'); assert.equal(r.url, 'https://www.youtube.com/watch');
  r = await context.command({ action: 'switch', tab_id: 1, expected_url: tabs.get(1).url });
  assert.equal(r.success, true); assert.equal(r.active, true); assert.equal(JSON.stringify(focused.at(-1)), JSON.stringify([1, { focused: true }]));

  // a page control may only touch a YouTube tab, and only the tab that was inspected
  await assert.rejects(context.command({ action: 'seek', tab_id: 1, expected_url: tabs.get(1).url, seconds: 5 }), /limited to YouTube/);
  await assert.rejects(context.command({ action: 'seek', tab_id: 2, expected_url: 'https://www.youtube.com/watch?v=other', seconds: 5 }), /Tab changed/);
  await assert.rejects(context.command({ action: 'eval', tab_id: 2, expected_url: tabs.get(2).url }), /Unsupported/);
  const caps = vm.runInContext('CAPABILITIES', context);
  for (const c of ['seek', 'speed', 'volume', 'mute', 'unmute', 'fullscreen', 'next', 'results', 'click_result', 'scroll', 'switch', 'read']) assert.ok(caps.includes(c), c);
  console.log('YouTube control checks passed: seek/speed/volume/mute, full screen (blocked is reported), next, results, click result N, scroll, switch, read, input limits.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
