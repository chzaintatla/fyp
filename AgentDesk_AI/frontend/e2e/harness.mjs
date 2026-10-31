// End-to-end harness: a real AgentDesk backend (temp database, temp home folder) serving the
// built UI, a real Chromium, and a fake microphone so a test can "speak" a command.
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND = resolve(HERE, '..', '..', 'backend');

export async function startBackend({ port: fixedPort } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'agentdesk-e2e-'));
  const home = join(root, 'home');
  mkdirSync(join(home, 'Documents'), { recursive: true });
  mkdirSync(join(home, 'Desktop'), { recursive: true });
  const port = fixedPort ?? 18700 + Math.floor(Math.random() * 200);
  const tokenFile = join(root, 'api_token');
  const python = process.env.AGENTDESK_PYTHON ?? (process.platform === 'win32' ? join(BACKEND, '.venv', 'Scripts', 'python.exe') : 'python3');
  const env = {
    ...process.env,
    HOME: home, USERPROFILE: home,
    DATABASE_URL: `sqlite:///${join(root, 'e2e.db')}`,
    AGENTDESK_ALLOWED_DIRECTORIES: home,
    AGENTDESK_DISCOVERY_DIRECTORIES: home,
    AGENTDESK_TEST_DIR: join(root, 'testdir'),
    AGENTDESK_LOG_DIR: join(root, 'logs'),
    AGENTDESK_TOKEN_FILE: tokenFile,
    AI_PROVIDER: 'groq', GROQ_API_KEY: '', GROQ_API_KEYS: '', CLOUD_BRAIN_URL: '', CLOUD_BRAIN_TOKEN: '', AI_CLOUD_MODE: 'explicit',
  };
  const child = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'],
    { cwd: BACKEND, env, stdio: ['ignore', 'inherit', 'inherit'] });
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/health`);
      if (res.ok && existsSync(tokenFile)) break;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  const token = readFileSync(tokenFile, 'utf8').trim();
  process.on('exit', () => child.kill());
  return { url, token, home, root, stop: () => child.kill() };
}

// Fake Web Speech API: window.__speak(text) delivers interim then final results to the active recognizer.
const FAKE_SPEECH = `
  (() => {
    class FakeRecognition {
      constructor() { this.lang = ''; this.continuous = false; this.interimResults = false; }
      start() { window.__recognizer = this; window.__micStarts = (window.__micStarts || 0) + 1; }
      stop() { this.abort(); }
      abort() { if (window.__recognizer === this) window.__recognizer = null; setTimeout(() => this.onend && this.onend(), 0); }
    }
    const results = (text, isFinal) => {
      const alt = { transcript: text, confidence: 0.95 };
      const result = [alt]; result.isFinal = isFinal;
      const list = [result];
      return { resultIndex: 0, results: list };
    };
    window.__speak = (text) => {
      const r = window.__recognizer;
      if (!r) throw new Error('microphone is not listening');
      const words = text.split(' ');
      r.onresult && r.onresult(results(words.slice(0, Math.ceil(words.length / 2)).join(' '), false));
      r.onresult && r.onresult(results(text, true));
    };
    window.SpeechRecognition = FakeRecognition;
    window.webkitSpeechRecognition = FakeRecognition;
    // Speech output finishes immediately in tests.
    Object.defineProperty(window, 'speechSynthesis', { configurable: true,
      value: { speak: (u) => setTimeout(() => u.onend && u.onend(), 10), cancel: () => {} } });
    window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  })();
`;

export async function openApp(backend, { path = '/', viewport = { width: 1280, height: 900 } } = {}) {
  const browser = await chromium.launch(process.env.E2E_CHANNEL ? { channel: process.env.E2E_CHANNEL } : (process.platform === 'win32' ? { channel: 'msedge' } : {}));
  const context = await browser.newContext({ viewport });
  await context.addInitScript(FAKE_SPEECH);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${backend.url}${path}#token=${encodeURIComponent(backend.token)}`);
  return { browser, page, errors };
}

export async function api(backend, path, options = {}) {
  const res = await fetch(`${backend.url}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${backend.token}`, ...(options.headers ?? {}) },
  });
  return res.json();
}
