import { useCallback, useEffect, useState } from 'react';
import {
  api,
  authMessage,
  hasApiToken,
  voiceApi,
  type Catalog,
  type ConfigStatus,
  type Preferences,
  type PreferencesUpdate,
} from '../api';
import { accentFor } from '../characters';
import { errMsg } from '../taskFormat';
import { applyTheme, loadTheme, type Theme } from '../theme';

interface Params {
  setBanner: (update: string | null | ((current: string | null) => string | null)) => void;
  setVoiceLanguage: (language: string) => void;
  loadHistory: () => Promise<void>;
}

/** Backend status, saved preferences, the character catalog and the colour theme. */
export function useAppData({ setBanner, setVoiceLanguage, loadHistory }: Params) {
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [theme, setTheme] = useState<Theme>(loadTheme);

  useEffect(() => { void voiceApi.catalog().then(setCatalog).catch(() => setCatalog(null)); }, []);
  useEffect(() => applyTheme(theme, prefs ? accentFor(prefs.persona, catalog) : undefined), [theme, prefs?.persona.avatar_id, catalog]);
  useEffect(() => { if (!hasApiToken()) setBanner(authMessage()); }, []);

  const loadConfig = useCallback(async () => {
    try {
      const c = await api.config();
      setConfig(c);
      setConfigError(null);
      // A successful backend check clears only stale connection warnings.
      // Keep command/validation errors until the user handles them.
      setBanner((current) => current?.startsWith('Backend se connection nahi ho raha.') ? null : current);
    } catch (e) {
      setConfig(null);
      setConfigError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    void api.preferences().then((p) => {
      setPrefs(p);
      setVoiceLanguage(p.persona.language);
    }).catch(() => setPrefs(null));
  }, []);

  const savePreferences = async (patch: PreferencesUpdate) => {
    try {
      const next = await api.savePreferences(patch);
      setPrefs(next);
      if (patch.persona) setVoiceLanguage(next.persona.language);
    } catch (e) {
      setBanner(errMsg(e));
      throw e;
    }
  };

  useEffect(() => {
    void loadConfig();
    void loadHistory();
    // Only retry read-only status checks; never automatically repeat PC commands.
    const reconnect = window.setInterval(() => { void loadConfig(); }, 5000);
    const onFocus = () => { void loadConfig(); };
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(reconnect);
      window.removeEventListener('focus', onFocus);
    };
  }, [loadConfig, loadHistory]);

  return { config, configError, prefs, catalog, theme, setTheme, savePreferences };
}
