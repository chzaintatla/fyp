import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The same per-user token file the backend and launcher use (backend/app/security/token.py).
const tokenFile = process.env.AGENTDESK_TOKEN_FILE
  ?? join(process.env.LOCALAPPDATA ?? join(homedir(), '.agentdesk'), 'AgentDesk', 'api_token');
function localToken(): string {
  try { return readFileSync(tokenFile, 'utf-8').trim(); } catch { return ''; }
}

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Dev server only: the proxy below adds the token, so the page needs no /#token=... in its address.
  define: { __PROXY_AUTH__: command === 'serve' && localToken() !== '' },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ['/health', '/config', '/tasks', '/tools', '/browser', '/usage', '/preferences', '/memory', '/voice'].map(path => [path, {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
        configure: (proxy: { on: (event: string, cb: (req: { getHeader: (n: string) => unknown; setHeader: (n: string, v: string) => void }) => void) => void }) => {
          proxy.on('proxyReq', (req) => {
            const token = localToken();
            if (token && !req.getHeader('authorization')) req.setHeader('Authorization', `Bearer ${token}`);
          });
        },
      }]),
    ),
  },
}));
