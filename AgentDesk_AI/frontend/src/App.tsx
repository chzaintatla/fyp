import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { ActionPreview } from './components/ActionPreview';
import { AppHeader } from './components/AppHeader';
import { CommandPanel } from './components/CommandPanel';
import { Onboarding } from './components/Onboarding';
import { Rail, type View } from './components/Rail';
import { TaskHistory } from './components/TaskHistory';
import { TaskPanel } from './components/TaskPanel';
import { CharactersPage } from './pages/CharactersPage';
import { MemoryPage } from './pages/MemoryPage';
import { SettingsPage } from './pages/SettingsPage';
import { useAppData } from './hooks/useAppData';
import { useSpokenReplies } from './hooks/useSpokenReplies';
import { useTasks } from './hooks/useTasks';
import { useVoiceCore } from './hooks/useVoiceCore';
import { useVoiceInput } from './hooks/useVoiceInput';
import { deriveVoiceState } from './voiceState';

export default function App() {
  const [banner, setBanner] = useState<string | null>(null);
  const [view, setView] = useState<View>('assistant');
  const [voiceLanguage, setVoiceLanguage] = useState('ur-PK');
  const commandInputRef = useRef<HTMLInputElement | null>(null);
  const toggleVoiceRef = useRef<() => void>(() => {});
  const commandFailedRef = useRef<() => void>(() => {});

  const commandStartRef = useRef<(command: string) => void>(() => {});
  const tasks = useTasks({ setBanner, onCommandFailed: () => commandFailedRef.current(), onCommandStart: (c) => commandStartRef.current(c) });
  const { activeTask, command, setCommand, submitting, busy } = tasks;
  const { config, configError, prefs, catalog, theme, setTheme, savePreferences } =
    useAppData({ setBanner, setVoiceLanguage, loadHistory: tasks.loadHistory });
  const voice = useVoiceCore({ prefs, config, voiceLanguage });
  const input = useVoiceInput({
    voice, prefs, voiceLanguage, setCommand, setBanner,
    submittingRef: tasks.submittingRef, runCommandRef: tasks.runCommandRef,
  });
  const { earlyAck } = useSpokenReplies({ voice, activeTask, prefs, voiceLanguage, beginFollowUp: input.beginFollowUp });
  commandStartRef.current = earlyAck;

  // A voice command that was waiting for its task moves on when creating the task failed.
  commandFailedRef.current = () => {
    if (!voice.voiceWaitingForTaskRef.current) return;
    voice.voiceWaitingForTaskRef.current = false;
    voice.voiceTaskActiveRef.current = false;
    if (voice.handsFreeRef.current) {
      voice.voiceRestartRef.current = window.setTimeout(() => {
        voice.voiceRestartRef.current = null;
        voice.drainVoiceQueueRef.current();
        voice.startListeningRef.current();
      }, 1500);
    }
  };

  toggleVoiceRef.current = input.toggleVoiceInput;
  // Hooks for the desktop tray (fixed calls only): start/stop listening, open a page.
  useEffect(() => {
    (window as unknown as { __agentx?: unknown }).__agentx = {
      toggleVoice: () => toggleVoiceRef.current(),
      openView: (v: View) => setView(v),
    };
  }, []);

  // Keyboard: Ctrl+Space = microphone, Alt+1/2/3/4 = Assistant/Characters/Memory/Settings, "/" = command box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
      if (e.ctrlKey && e.code === 'Space') {
        e.preventDefault();
        toggleVoiceRef.current();
      } else if (e.altKey && ['1', '2', '3', '4'].includes(e.key)) {
        e.preventDefault();
        setView((['assistant', 'characters', 'memory', 'settings'] as const)[Number(e.key) - 1]);
      } else if (e.key === '/' && !typing) {
        e.preventDefault();
        setView('assistant');
        window.setTimeout(() => commandInputRef.current?.focus(), 0);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // While a dialog is open, the page behind it can't be focused or read (aria-modal alone is not enforced).
  const modalOpen = activeTask?.status === 'awaiting_approval' || (prefs !== null && !prefs.onboarded);
  const inertWhenModal = (modalOpen ? { inert: 'true' } : {}) as Record<string, string>;  // React 18 drops inert=""
  const micSupported = typeof window !== 'undefined' && ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window);
  const voiceState = deriveVoiceState({
    listening: voice.listening || voice.handsFree, heard: voice.heard, submitting,
    speaking: voice.speaking, task: activeTask, error: Boolean(banner),
  });

  return (
    <div className="min-h-screen pb-20 md:pb-0 md:pl-[84px]">
      <Rail view={view} onChange={setView} />
      {activeTask?.status === 'awaiting_approval' && (
        <ActionPreview
          task={activeTask}
          busy={busy}
          reply={command}
          onReplyChange={setCommand}
          onReplySubmit={tasks.submitCommand}
          onApprove={(ids) => void tasks.mutate('confirm', () => tasks.approveThenRun(activeTask.id, ids))}
          onReject={() => void tasks.mutate('reject-all', () => api.reject(activeTask.id))}
          micLabel={voice.handsFree ? 'Stop microphone' : 'Answer by voice'}
          onMic={prefs?.listen_mode === 'off' ? undefined : input.toggleVoiceInput}
        />
      )}
      {prefs && !prefs.onboarded && (
        <Onboarding
          persona={prefs.persona}
          catalog={catalog}
          micSupported={micSupported}
          memoryEnabled={prefs.memory_enabled}
          cloudAnswers={prefs.cloud_answers}
          onFinish={(persona, memoryEnabled, cloudAnswers, startVoice) => {
            setVoiceLanguage(persona.language);
            void savePreferences({ onboarded: true, memory_enabled: memoryEnabled, persona, ...(cloudAnswers === null ? {} : { cloud_answers: cloudAnswers }) }).catch(() => {});
            if (startVoice) input.toggleVoiceInput();
            else window.setTimeout(() => commandInputRef.current?.focus(), 0);
          }}
        />
      )}
      <AppHeader
        name={prefs?.persona.name ?? 'Agent X'}
        config={config}
        configError={configError}
        theme={theme}
        onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        inert={inertWhenModal}
      />

      {view !== 'assistant' && (
        <main {...inertWhenModal} className="mx-auto max-w-3xl px-4 py-6">
          {banner && <p role="alert" className="mb-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{banner}</p>}
          {view === 'settings' && prefs && <SettingsPage prefs={prefs} config={config} usage={tasks.usage} onSave={savePreferences} />}
          {view === 'characters' && prefs && (
            <CharactersPage persona={prefs.persona} catalog={catalog} language={voiceLanguage} speechReady={voice.speechReady} onSave={savePreferences} />
          )}
          {view === 'memory' && prefs && <MemoryPage memoryEnabled={prefs.memory_enabled} onToggle={(on) => void savePreferences({ memory_enabled: on }).catch(() => {})} />}
          {!prefs && <p className="text-sm text-slate-500">Loading preferences…</p>}
        </main>
      )}
      <main {...inertWhenModal} hidden={view !== 'assistant'} className={`mx-auto max-w-7xl gap-6 px-4 py-6 lg:grid-cols-[1fr_320px] ${view === 'assistant' ? 'grid' : 'hidden'}`}>
        <div className="flex min-w-0 flex-col gap-6">
          {banner && (
            <div
              role="alert"
              className="flex items-start justify-between gap-3 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800"
            >
              <p>{banner}</p>
              <button
                type="button"
                onClick={() => setBanner(null)}
                className="shrink-0 font-semibold underline hover:text-red-900"
              >
                Dismiss
              </button>
            </div>
          )}
          <CommandPanel
            prefs={prefs}
            catalog={catalog}
            config={config}
            usage={tasks.usage}
            voiceState={voiceState}
            activeTask={activeTask}
            heard={voice.heard}
            lastReply={voice.lastReply}
            speaking={voice.speaking}
            avatarLevel={voice.avatarLevel}
            handsFree={voice.handsFree}
            listening={voice.listening}
            voiceMessage={voice.voiceMessage}
            voiceLanguage={voiceLanguage}
            onVoiceLanguage={setVoiceLanguage}
            command={command}
            onCommand={setCommand}
            submitting={submitting}
            onSubmit={tasks.submitCommand}
            onToggleVoice={input.toggleVoiceInput}
            onStopSpeaking={input.stopSpeaking}
            inputRef={commandInputRef}
          />
          <TaskPanel
            task={activeTask}
            busy={busy}
            busyAction={tasks.busyAction}
            transitional={tasks.transitional}
            mutate={tasks.mutate}
            approveThenRun={tasks.approveThenRun}
            pauseRun={tasks.pauseRun}
            undoChanges={tasks.undoChanges}
            refreshTask={tasks.refreshTask}
            setBanner={setBanner}
          />
        </div>
        <TaskHistory
          history={tasks.history}
          historyError={tasks.historyError}
          activeId={activeTask?.id}
          onReload={() => void tasks.loadHistory()}
          onSelect={tasks.selectTask}
        />
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <p className="mx-auto max-w-7xl px-4 py-4 text-xs text-slate-500">
          Agent X AI · Local desktop assistant
        </p>
      </footer>
    </div>
  );
}
