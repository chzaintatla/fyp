const API = 'http://127.0.0.1:8000';
let socket = null;
let connecting = false;
let heartbeat = null;
const seen = new Map();
const VERSION = chrome.runtime.getManifest ? chrome.runtime.getManifest().version : '0';
const CAPABILITIES = ['inspect', 'open', 'close', 'pause', 'resume', 'reload', 'switch', 'read', 'seek', 'speed', 'volume', 'mute', 'unmute',
  'fullscreen', 'next', 'results', 'click_result', 'scroll'];
// Everything that needs the page itself (the DOM): YouTube only. Each is checked afterwards by reading the page again.
const PAGE_ACTIONS = ['seek', 'speed', 'volume', 'mute', 'unmute', 'fullscreen', 'next', 'results', 'click_result', 'scroll'];
const YOUTUBE_HOSTS = ['www.youtube.com', 'm.youtube.com', 'youtube.com'];

// Runs INSIDE the YouTube page (chrome.scripting serialises it, so it must not use anything outside itself). Returns the state it
// observed after acting, never what it intended: seek -> the real currentTime, speed -> the real playbackRate, next -> the new video id.
async function pageControl(action, p) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const video = document.querySelector('video');
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const videoId = () => new URL(location.href).searchParams.get('v') || '';
  const snap = () => ({
    url: location.origin + location.pathname + (videoId() ? '?v=' + videoId() : ''), title: document.title, video_id: videoId(),
    time: video ? Math.round(video.currentTime * 10) / 10 : null, duration: video && isFinite(video.duration) ? Math.round(video.duration) : null,
    paused: video ? video.paused : null, rate: video ? video.playbackRate : null, volume: video ? Math.round(video.volume * 100) : null,
    muted: video ? video.muted : null, fullscreen: !!document.fullscreenElement, scroll_y: Math.round(window.scrollY || 0),
    scroll_max: Math.max(0, Math.round((document.documentElement.scrollHeight || 0) - (window.innerHeight || 0))),
  });
  const noVideo = { success: false, error: 'There is no video on this YouTube page.' };
  const results = () => [...document.querySelectorAll('ytd-video-renderer')].map((el) => {
    const a = el.querySelector('a#video-title');
    return a && String(a.href || '').includes('/watch') ? { title: String(a.textContent || '').trim().slice(0, 120), href: a.href } : null;
  }).filter(Boolean);
  if (action === 'seek') {
    if (!video) return noVideo;
    const duration = isFinite(video.duration) ? video.duration : Infinity;
    const target = clamp(p.position != null ? p.position : video.currentTime + p.seconds, 0, duration);
    video.currentTime = target;
    await wait(250);
    return { ...snap(), target, success: Math.abs(video.currentTime - target) <= 1.5 };
  }
  if (action === 'speed') {
    if (!video) return noVideo;
    video.playbackRate = p.rate;
    await wait(50);
    return { ...snap(), success: Math.abs(video.playbackRate - p.rate) < 0.01 };
  }
  if (action === 'volume') {
    if (!video) return noVideo;
    video.muted = false;
    video.volume = clamp(p.level / 100, 0, 1);
    await wait(50);
    return { ...snap(), success: Math.abs(Math.round(video.volume * 100) - p.level) <= 1 && !video.muted };
  }
  if (action === 'mute' || action === 'unmute') {
    if (!video) return noVideo;
    video.muted = action === 'mute';
    await wait(50);
    return { ...snap(), success: video.muted === (action === 'mute') };
  }
  if (action === 'fullscreen') {
    const want = !p.exit;
    try {
      if (want) await (document.querySelector('#movie_player') || video || document.documentElement).requestFullscreen();
      else if (document.fullscreenElement) await document.exitFullscreen();
    } catch {
      return { ...snap(), success: false, error: 'Chrome allows full screen only after a real key press. Press F on the video.' };
    }
    await wait(200);
    return { ...snap(), success: !!document.fullscreenElement === want };
  }
  if (action === 'next') {
    const before = videoId();
    const button = document.querySelector('.ytp-next-button');
    const link = document.querySelector('ytd-compact-video-renderer a[href*="/watch"], ytd-watch-next-secondary-results-renderer a[href*="/watch"]');
    if (button && button.getAttribute('aria-disabled') !== 'true' && !button.hidden) button.click();
    else if (link) link.click();
    else return { success: false, error: 'There is no next video to go to.' };
    for (let i = 0; i < 25 && (!videoId() || videoId() === before); i++) await wait(200);
    return { ...snap(), previous_video_id: before, changed: videoId() !== before, success: !!videoId() && videoId() !== before };
  }
  if (action === 'results') {
    const found = results();
    return { ...snap(), success: found.length > 0, results: found.slice(0, 10).map((r) => r.title), error: found.length ? null : 'This is not a YouTube results page.' };
  }
  if (action === 'click_result') {
    const found = results();
    if (!found.length) return { success: false, error: 'This is not a YouTube results page. Search first.' };
    const item = found[p.index - 1];
    if (!item) return { success: false, error: `There are only ${found.length} results.` };
    const wanted = new URL(item.href).searchParams.get('v');
    const link = [...document.querySelectorAll('ytd-video-renderer a#video-title')].find((a) => a.href === item.href);
    link.click();
    for (let i = 0; i < 25 && videoId() !== wanted; i++) await wait(200);
    return { ...snap(), clicked_title: item.title, wanted_video_id: wanted, success: videoId() === wanted };
  }
  if (action === 'scroll') {
    const before = window.scrollY || 0;
    window.scrollBy(0, p.amount * (window.innerHeight || 600) * 0.8);
    await wait(300);
    const moved = Math.abs((window.scrollY || 0) - before) > 5;
    return { ...snap(), moved, success: moved, error: moved ? null : 'The page did not move (already at the end?).' };
  }
  return { success: false, error: 'Unsupported page command' };
}

// The numbers the backend sends are checked again here: nothing outside these ranges reaches the page.
function pageParams(message) {
  const num = (v, lo, hi) => (Number.isFinite(v) && v >= lo && v <= hi ? v : null);
  if (message.action === 'seek') {
    const position = num(message.position, 0, 86400);
    const seconds = num(message.seconds, -3600, 3600);
    if (position === null && seconds === null) throw new Error('seek needs seconds or a position');
    return { position, seconds };
  }
  if (message.action === 'speed') { const rate = num(message.rate, 0.25, 4); if (rate === null) throw new Error('speed must be between 0.25 and 4'); return { rate }; }
  if (message.action === 'volume') { const level = num(message.level, 0, 100); if (level === null) throw new Error('volume must be 0 to 100'); return { level }; }
  if (message.action === 'click_result') { const index = num(message.index, 1, 20); if (!Number.isInteger(index)) throw new Error('result number must be 1 to 20'); return { index }; }
  if (message.action === 'scroll') { const amount = num(message.amount, -10, 10); if (amount === null || amount === 0) throw new Error('scroll amount must be -10 to 10'); return { amount }; }
  if (message.action === 'fullscreen') return { exit: message.exit === true };
  return {};
}

// Browsers normalise URLs (https://site -> https://site/); compare the same form on both sides.
function sameUrl(a, b) {
  try { return new URL(a).href === new URL(b).href; } catch { return false; }
}

// The bridge may only be used by an extension the user paired with this app's
// local API token (handed over once from the setup page; never typed or logged).
async function pairedToken() {
  const stored = await chrome.storage.local.get('apiToken');
  return typeof stored.apiToken === 'string' && stored.apiToken.length >= 32 ? stored.apiToken : null;
}

function acceptPairing(message, sender) {
  return Boolean(
    message && message.type === 'agentx-pair' && typeof message.token === 'string' && message.token.length >= 32 &&
    sender && sender.id === chrome.runtime.id && typeof sender.url === 'string' &&
    sender.url.startsWith(API + '/browser-extension/setup.html'),
  );
}

async function command(message) {
  if (message.action === 'open') {
    const url = new URL(message.url);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Only http(s) website URLs are allowed.');
    const browser = navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome';
    if (message.browser && message.browser !== browser) throw new Error(`Connected profile is ${browser}; connect the requested ${message.browser} profile.`);
    const existing = (await chrome.tabs.query({})).filter(t => !t.incognito && sameUrl(t.url || '', message.url));
    const tab = existing.length === 1 ? await chrome.tabs.update(existing[0].id, { active: true }) : await chrome.tabs.create({ url: message.url, active: true });
    if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, { focused: true });
    const actual = await chrome.tabs.get(tab.id);
    if (![actual.url, actual.pendingUrl].some(u => u && sameUrl(u, message.url))) throw new Error('Browser did not confirm the requested tab navigation.');
    return { success: true, tab_id: tab.id, url: message.url, browser, navigation_requested: true };
  }
  if (message.action === 'reload') {
    // The backend asks an outdated extension to load its updated files (unpacked install).
    setTimeout(() => chrome.runtime.reload(), 200);
    return { success: true, reloading: true, version: VERSION };
  }
  if (message.action === 'inspect') {
    const tabs = await chrome.tabs.query({});
    // A loading tab has only pendingUrl; report it so a later guard compares the same page.
    return { success: true, tabs: tabs.filter(t => !t.incognito).map(t => ({ id: t.id, title: t.title || '', url: t.url || t.pendingUrl || '', active: t.active, audible: t.audible })) };
  }
  if (!['close', 'pause', 'resume', 'switch', 'read', ...PAGE_ACTIONS].includes(message.action) || !Number.isInteger(message.tab_id)) {
    throw new Error('Unsupported browser command');
  }
  const tab = await chrome.tabs.get(message.tab_id);
  if (tab.incognito || !message.expected_url || !sameUrl(tab.url || tab.pendingUrl || '', message.expected_url)) {
    throw new Error('Tab changed since inspection; nothing was changed. Repeat with the current title.');
  }
  if (message.action === 'read') {
    const parts = new URL(tab.url || tab.pendingUrl || 'about:blank');
    return { success: true, title: tab.title || '', url: parts.origin + parts.pathname, active: !!tab.active };
  }
  if (message.action === 'switch') {
    const updated = await chrome.tabs.update(tab.id, { active: true });
    if (updated.windowId !== undefined) await chrome.windows.update(updated.windowId, { focused: true });
    const now = await chrome.tabs.get(tab.id);
    return { success: !!now.active, active: !!now.active, title: now.title || '' };
  }
  if (PAGE_ACTIONS.includes(message.action)) {
    if (!YOUTUBE_HOSTS.includes(new URL(tab.url).hostname)) throw new Error('Media and page controls are limited to YouTube.');
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: pageControl, args: [message.action, pageParams(message)] });
    return results[0]?.result || { success: false, error: 'No page state returned.' };
  }
  if (message.action === 'close') {
    await chrome.tabs.remove(tab.id);
    let exists = true;
    try { await chrome.tabs.get(tab.id); } catch { exists = false; }
    if (exists) throw new Error('Browser did not confirm that the tab closed.');
    return { success: true, closed: true };
  }
  const host = new URL(tab.url).hostname;
  if (!['www.youtube.com', 'm.youtube.com', 'youtube.com'].includes(host)) throw new Error('Media controls are limited to YouTube.');
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: async (action) => {
      const video = document.querySelector('video');
      if (!video) return { success: false, error: 'Is YouTube tab mein playable video nahi mila.' };
      if (action === 'pause') video.pause();
      else {
        try { await video.play(); }
        catch { return { success: false, error: 'Browser ne playback block ki; video par Play dabayein.' }; }
      }
      const passed = action === 'pause' ? video.paused : !video.paused;
      return { success: passed, paused: video.paused, error: passed ? null : 'Video state did not change.' };
    },
    args: [message.action]
  });
  return results[0]?.result || { success: false, error: 'No media state returned.' };
}

async function connect() {
  if (connecting || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  connecting = true;
  try {
    const apiToken = await pairedToken();
    if (!apiToken) { chrome.action.setBadgeText({ text: 'PAIR' }); return; }
    const response = await fetch(`${API}/browser/connect-token`, { headers: { 'X-AgentX-Bridge': '1', Authorization: `Bearer ${apiToken}` } });
    if (!response.ok) throw new Error('Local backend unavailable or pairing no longer valid');
    const { token } = await response.json();
    const ws = new WebSocket('ws://127.0.0.1:8000/browser/ws');
    socket = ws;
    ws.onopen = () => ws.send(JSON.stringify({ token, version: VERSION, capabilities: CAPABILITIES }));
    ws.onmessage = async event => {
      const message = JSON.parse(event.data);
      if (message.ready) {
        chrome.action.setBadgeText({ text: 'ON' });
        chrome.action.setBadgeBackgroundColor({ color: '#047857' });
        if (heartbeat) clearInterval(heartbeat);
        heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ heartbeat: true })); }, 20000);
        return;
      }
      if (!message.id) return;
      let result = seen.get(message.id);
      if (!result) {
        try { result = await command(message); }
        catch (error) { result = { success: false, error: String(error.message || error) }; }
        seen.set(message.id, result);
        if (seen.size > 200) seen.delete(seen.keys().next().value);
      }
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ id: message.id, result }));
    };
    ws.onclose = () => {
      if (heartbeat) clearInterval(heartbeat);
      heartbeat = null;
      if (socket === ws) socket = null;
      chrome.action.setBadgeText({ text: 'OFF' });
    };
    ws.onerror = () => ws.close();
  } catch {
    chrome.action.setBadgeText({ text: 'OFF' });
  } finally { connecting = false; }
}

chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create('reconnect', { periodInMinutes: 0.5 }); connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'reconnect') connect(); });
chrome.action.onClicked.addListener(connect);
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!acceptPairing(message, sender)) return false;
  chrome.storage.local.set({ apiToken: message.token }).then(() => { sendResponse({ ok: true }); connect(); });
  return true;
});
connect();
