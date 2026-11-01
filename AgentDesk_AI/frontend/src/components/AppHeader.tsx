import type { ConfigStatus } from '../api';
import type { Theme } from '../theme';
import { Badge } from './Spinner';

interface Props {
  name: string;
  config: ConfigStatus | null;
  configError: string | null;
  theme: Theme;
  onToggleTheme: () => void;
  inert: Record<string, string>;
}

export function AppHeader({ name, config, configError, theme, onToggleTheme, inert }: Props) {
  return (
    <header {...inert} className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-700 text-lg font-bold text-white"
            aria-hidden="true"
          >
            AX
          </div>
          <div>
            <h1 className="text-lg font-bold leading-tight text-slate-900">{name}</h1>
            <p className="text-xs text-slate-500">Natural-language Windows desktop assistant</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {config ? (
            config.mock_mode || !config.ai_configured ? (
              <Badge className="border-amber-300 bg-amber-100 text-amber-800">
                <span className="h-2 w-2 rounded-full bg-amber-500" aria-hidden="true" />
                {config.mock_mode ? 'Mock AI mode' : 'AI key missing'}
              </Badge>
            ) : (
              <Badge className="border-emerald-300 bg-emerald-100 text-emerald-800">
                <span className="h-2 w-2 rounded-full bg-emerald-500" aria-hidden="true" />
                {config.ai_provider === 'openai' ? 'OpenAI connected' : `${config.ai_provider} connected`}
              </Badge>
            )
          ) : configError ? (
            <Badge className="border-red-300 bg-red-100 text-red-800">Backend unreachable</Badge>
          ) : (
            <Badge className="border-slate-300 bg-slate-100 text-slate-600">Connecting…</Badge>
          )}
          {config?.platform && (
            <Badge className="border-slate-300 bg-slate-100 text-slate-700">
              {config.platform}
            </Badge>
          )}
          <button type="button" onClick={onToggleTheme} aria-pressed={theme === 'light'}
            title="Switch between the dark and light theme" className="rounded-md px-3 py-1.5 text-sm font-medium ring-1 ring-inset ring-slate-300">
            {theme === 'dark' ? 'Light theme' : 'Dark theme'}
          </button>
        </div>
      </div>
    </header>
  );
}
