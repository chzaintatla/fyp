import { useEffect, useState } from 'react';
import { api, voiceApi, type Catalog, type Preferences, type TaskOut } from './api';
import { accentFor, loadCustomImage } from './characters';
import { Avatar, moodFor } from './components/Avatar';
import { Orb } from './components/Orb';
import { expandPanel } from './desktop';
import { applyTheme, loadTheme } from './theme';
import { deriveVoiceState, STATE_LABEL } from './voiceState';

/** Always-on-top mini window (?view=orb): a 72 px character bubble that glows in the state colour; click expands the panel. */
export default function MiniOrb() {
  const [task, setTask] = useState<TaskOut | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    document.body.classList.add('mini-orb');
    applyTheme(loadTheme());
    void api.preferences().then(setPrefs).catch(() => {});
    void voiceApi.catalog().then(setCatalog).catch(() => {});
    const poll = async () => {
      try {
        const [latest] = await api.listTasks(1);
        setTask(latest ?? null);
        setOffline(false);
      } catch {
        setOffline(true);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => window.clearInterval(timer);
  }, []);

  const state = deriveVoiceState({ listening: false, heard: '', submitting: false, speaking: false, task, error: offline });
  const needsApproval = task?.status === 'awaiting_approval';
  const label = `${prefs?.persona.name ?? 'Agent X'}: ${STATE_LABEL[state]}. Press to open the panel`;
  const accent = prefs ? accentFor(prefs.persona, catalog) : '#38E1FF';
  return (
    <main className="flex h-screen items-center justify-center" aria-label="Agent X mini orb">
      {prefs?.persona.show_avatar !== false && prefs ? (
        <button type="button" data-testid="orb" data-state={state} aria-label={label} title={label}
          onClick={() => void expandPanel()}
          className="relative rounded-full outline-none focus-visible:ring-4 focus-visible:ring-blue-300"
          style={{ filter: `drop-shadow(0 0 ${state === 'idle' ? 6 : 12}px ${accent})` }}>
          <Avatar id={prefs.persona.avatar_id} color={accent} mood={moodFor(state)} size={72}
            image={prefs.persona.avatar_id === 'custom' ? loadCustomImage() : null} label={label} />
          {needsApproval && (
            <span role="status" data-testid="mini-approval"
              className="absolute -top-1 -right-1 rounded-full bg-amber-100 px-1.5 text-[10px] font-bold text-amber-800 ring-1 ring-amber-300">
              Approve?
            </span>
          )}
        </button>
      ) : (
        <Orb state={state} size="mini" color={prefs?.persona.orb_color} name={prefs?.persona.name}
          onActivate={() => void expandPanel()} activateHint="Press to open the panel" />
      )}
    </main>
  );
}
