/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)', surface: 'var(--surface)', raised: 'var(--raised)', line: 'var(--line)',
        ink: 'var(--ink)', ink2: 'var(--ink2)', accent: 'var(--accent)',
        ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)',
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
        display: ['"Space Grotesk"', 'Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
      },
      borderRadius: { xl: '14px' },
    },
  },
  plugins: [],
};
