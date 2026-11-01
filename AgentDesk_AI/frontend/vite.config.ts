import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
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
      }]),
    ),
  },
});
