import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { spokenReply, isSpeechEcho, afterInterruption, finishOnce } from './voice';
import { SpeechPlayer } from './speech/player';
import { WhisperRecognition } from './speech/whisperRecognition';
import { voiceApi, type Catalog, type VoiceOptions } from './api';
import { Avatar, moodFor } from './components/Avatar';
import { CharactersPage } from './pages/CharactersPage';
import { accentFor, loadCustomImage, voiceOptions } from './characters';
import { ActionPreview } from './components/ActionPreview';
import { Onboarding } from './components/Onboarding';
import { Orb } from './components/Orb';
import { Rail, type View } from './components/Rail';
import { applyTheme, loadTheme, type Theme } from './theme';
import { Transcript } from './components/Transcript';
import { MemoryPage } from './pages/MemoryPage';
import { SettingsPage } from './pages/SettingsPage';
import { deriveVoiceState, STATE_LABEL } from './voiceState';
import {
  api,
  type ActionOut,
  type ConfigStatus,
  type Preferences,
  type PreferencesUpdate,
  type RiskLevel,
  type TaskOut,
  type UsageOut,
  apiToken,
} from './api';

/** Task statuses that are still in flight; the active task is polled while in one of these. */
const TRANSITIONAL = new Set([
  'created',
  'planned',
  'awaiting_approval',
  'approved',
  'executing',
  'waiting_retry',
]);

const POLL_INTERVAL_MS = 2000;

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly 0: { readonly transcript: string };
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function prettyStatus(status: string): string {
  if (status === 'awaiting_input') return 'Aapke jawab ka intezar';
  return status.replace(/_/g, ' ');
}

function riskClasses(risk: RiskLevel): string {
  switch (risk) {
    case 'low':
      return 'bg-emerald-100 text-emerald-800 border-emerald-300';
    case 'medium':
      return 'bg-amber-100 text-amber-800 border-amber-300';
    case 'high':
      return 'bg-red-100 text-red-800 border-red-300';
  }
}

function statusClasses(status: string): string {
  if (status === 'succeeded' || status === 'completed' || status === 'approved')
    return 'bg-emerald-100 text-emerald-800 border-emerald-300';
  if (status === 'failed' || status === 'rejected')
    return 'bg-red-100 text-red-800 border-red-300';
  if (status === 'running' || status === 'executing')
    return 'bg-blue-100 text-blue-800 border-blue-300';
  if (status === 'pending' || status === 'awaiting_approval' || status === 'planned' || status === 'partially_completed' || status === 'paused')
    return 'bg-amber-100 text-amber-800 border-amber-300';
  return 'bg-slate-100 text-slate-700 border-slate-300';
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5" role="status" aria-label={label ?? 'loading'}>
      <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
        />
      </svg>
      {label ? <span className="text-sm">{label}</span> : null}
    </span>
  );
}

function Verification({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="text-slate-400">Not verified yet</span>;
  if (typeof value === 'object') {
    const v = value as { passed?: unknown; checks?: unknown; message?: unknown };
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        {v.passed === true && (
          <span className="rounded-full border border-emerald-300 bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">
            verified
          </span>
        )}
        {v.passed === false && (
          <span className="rounded-full border border-red-300 bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">
            verification failed
          </span>
        )}
        {v.message !== undefined && v.message !== null && (
          <span className="text-sm">{formatValue(v.message)}</span>
        )}
        {Array.isArray(v.checks) && v.checks.length > 0 && (
          <ul className="w-full list-disc pl-5 text-sm text-slate-600">
            {v.checks.map((c, i) => (
              <li key={i}>{formatValue(c)}</li>
            ))}
          </ul>
        )}
      </span>
    );
  }
  return <span className="text-sm">{formatValue(value)}</span>;
}

function ArgsSummary({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args ?? {});
  if (entries.length === 0) return <span className="text-sm text-slate-400">no arguments</span>;
  return (
    <dl className="grid grid-cols-1 gap-1">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-start gap-2 text-sm">
          <dt className="shrink-0 font-medium text-slate-500">{k}:</dt>
          <dd className="break-all font-mono text-xs text-slate-800">{formatValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

interface ActionCardProps {
  action: ActionOut;
  taskStatus: TaskOut['status'];
  busy: boolean;
  onApprove: (actionId: string) => void;
  onReject: (actionId: string) => void;
}

function ActionCard({ action, taskStatus, busy, onApprove, onReject }: ActionCardProps) {
  const approvable = taskStatus === 'awaiting_approval' && action.status === 'pending';
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold text-slate-900">{action.tool_name}</span>
          <span
            className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${riskClasses(action.risk)}`}
          >
            {action.risk} risk
          </span>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClasses(action.status)}`}
        >
          {(action.status === 'running') && <Spinner />}
          {prettyStatus(action.status)}
        </span>
      </div>

      <div className="mt-2">
        <ArgsSummary args={action.args} />
      </div>

      {action.result !== undefined && action.result !== null && (
        <p className="mt-2 text-sm text-slate-600">
          <span className="font-medium text-slate-700">Result: </span>
          {formatValue(action.result)}
        </p>
      )}

      <div className="mt-2 text-sm">
        <span className="font-medium text-slate-700">Verification: </span>
        <Verification value={action.verification} />
      </div>

      {action.error && (
        <p role="alert" className="mt-2 rounded-md bg-red-50 p-2 text-sm text-red-700">
          {action.error}
        </p>
      )}

      {action.rollback_status && (
        <p
          className={`mt-2 text-xs ${action.rollback_status === 'failed' ? 'text-red-700' : 'text-slate-600'}`}
          data-testid="rollback-status"
        >
          <span className="font-medium">Undo: </span>
          {action.rollback_status === 'available' && 'this change can be undone'}
          {action.rollback_status === 'unsupported' && 'not supported for this kind of action'}
          {action.rollback_status === 'done' && `undone and verified${action.rollback_detail ? ` (${action.rollback_detail})` : ''}`}
          {action.rollback_status === 'failed' && `undo failed: ${action.rollback_detail ?? 'unknown reason'}`}
        </p>
      )}

      {approvable && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onApprove(action.id)}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onReject(action.id)}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-red-700 ring-1 ring-inset ring-red-300 hover:bg-red-50 disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      )}
    </li>
  );
}

function Badge({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${className}`}
    >
      {children}
    </span>
  );
}

export default function App() {
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [command, setCommand] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [activeTask, setActiveTask] = useState<TaskOut | null>(null);
  const [history, setHistory] = useState<TaskOut[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [view, setView] = useState<View>('assistant');
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [heard, setHeard] = useState('');
  const [speaking, setSpeaking] = useState(false);
  const [lastReply, setLastReply] = useState<string | null>(null);
  const listenModeRef = useRef<Preferences['listen_mode']>('push_to_talk');
  const commandInputRef = useRef<HTMLInputElement | null>(null);
  const toggleVoiceRef = useRef<() => void>(() => {});
  const [voiceLanguage, setVoiceLanguage] = useState('ur-PK');
  const [listening, setListening] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [voiceMessage, setVoiceMessage] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const spokenTaskStates = useRef(new Set<string>());
  const handsFreeRef = useRef(false);
  const voiceWaitingForTaskRef = useRef(false);
  const voiceTaskActiveRef = useRef(false);
  const voiceSpeakingRef = useRef(false);
  const spokenTextRef = useRef('');
  const speechFinishRef = useRef<() => void>(() => {});
  const speechEpochRef = useRef(0);
  const interruptedSpeechRef = useRef(false);
  const voiceQueueRef = useRef<string[]>([]);
  const voiceCommandRef = useRef('');
  const voiceDebounceRef = useRef<number | null>(null);
  const voiceRestartRef = useRef<number | null>(null);
  const voiceNetworkErrorsRef = useRef(0);
  const submittingRef = useRef(submitting);
  const startListeningRef = useRef<() => void>(() => {});
  const runCommandRef = useRef<(text: string) => Promise<void>>(async () => {});
  const drainVoiceQueueRef = useRef<() => void>(() => {});
  // Server speech (Whisper in, Orpheus / edge-tts / SAPI out) when the backend has a speech service; else the browser's.
  const speechReady = Boolean(config?.speech_ready);
  const speechReadyRef = useRef(false);
  speechReadyRef.current = speechReady;
  const voiceOptionsRef = useRef<VoiceOptions>({ language: voiceLanguage, gender: 'female', voice_en: 'diana' });
  voiceOptionsRef.current = prefs ? voiceOptions(prefs.persona, voiceLanguage) : { ...voiceOptionsRef.current, language: voiceLanguage };
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  useEffect(() => { void voiceApi.catalog().then(setCatalog).catch(() => setCatalog(null)); }, []);
  // How the character speaks (wording only): sent with every phrase request.
  const wording = () => ({
    language: voiceLanguage,
    personality: prefs?.persona.personality ?? 'assistant',
    user_name: prefs?.persona.user_name ?? '',
    formality: prefs?.persona.formality ?? 50,
    humour: prefs?.persona.humour ?? 50,
  });
  const [theme, setTheme] = useState<Theme>(loadTheme);
  useEffect(() => applyTheme(theme, prefs ? accentFor(prefs.persona, catalog) : undefined), [theme, prefs?.persona.avatar_id, catalog]);
  const [avatarLevel, setAvatarLevel] = useState(0);
  // Optional daily greeting: once per calendar day, only when the server voice is on and the user finished onboarding.
  useEffect(() => {
    if (!prefs?.onboarded || !prefs.daily_greeting || !speechReady || prefs.persona.speak_replies === false) return;
    const today = new Date().toDateString();
    try {
      if (localStorage.getItem('agentdesk_greeted') === today) return;
      localStorage.setItem('agentdesk_greeted', today);
    } catch {
      return;
    }
    void voiceApi.greeting().then((g) => (g.enabled && g.text ? playerRef.current?.speak(g.text) : undefined)).catch(() => undefined);
  }, [prefs?.onboarded, prefs?.daily_greeting, speechReady]);
  useEffect(() => {
    if (!speaking) {
      setAvatarLevel(0);
      return;
    }
    let frame = 0;
    const tick = () => {
      setAvatarLevel(playerRef.current?.level() ?? 0);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [speaking]);
  const playerRef = useRef<SpeechPlayer | null>(null);
  if (playerRef.current === null) playerRef.current = new SpeechPlayer(() => voiceOptionsRef.current);
  const speechEndedAtRef = useRef(0);
  const followUpTimerRef = useRef<number | null>(null);
  const followUpMs = (prefs?.follow_up_seconds ?? 8) * 1000; // after a spoken reply, keep listening this long without a hotkey
  if (playerRef.current) playerRef.current.volume = (prefs?.speech_volume ?? 100) / 100;

  submittingRef.current = submitting;
  const busy = busyAction !== null || submitting;

  const upsertHistory = useCallback((t: TaskOut) => {
    setHistory((prev) => {
      const idx = prev.findIndex((x) => x.id === t.id);
      if (idx === -1) return [t, ...prev].slice(0, 20);
      const next = prev.slice();
      next[idx] = t;
      return next;
    });
  }, []);

  const [usage, setUsage] = useState<UsageOut | null>(null);
  const activeStatus = activeTask?.status;
  useEffect(() => {
    // Refresh local plan usage whenever a task is created or changes state.
    void api.usage().then(setUsage).catch(() => setUsage(null));
  }, [activeTask?.id, activeStatus]);

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

  const loadHistory = useCallback(async () => {
    try {
      const list = await api.listTasks(20);
      setHistory(list);
      setHistoryError(null);
    } catch (e) {
      setHistoryError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    void api.preferences().then((p) => {
      setPrefs(p);
      setVoiceLanguage(p.persona.language);
    }).catch(() => setPrefs(null));
  }, []);
  listenModeRef.current = prefs?.listen_mode ?? 'push_to_talk';

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

  const refreshTask = useCallback(
    async (id: string) => {
      const t = await api.getTask(id);
      setActiveTask(t);
      upsertHistory(t);
    },
    [upsertHistory],
  );

  const activeId = activeTask?.id ?? null;
  const transitional = activeTask ? TRANSITIONAL.has(activeTask.status) : false;

  useEffect(() => {
    if (!activeId || !(transitional || busyAction)) return;
    const timer = window.setInterval(() => {
      void refreshTask(activeId).catch((e) => setBanner(errMsg(e)));
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeId, transitional, busyAction, refreshTask]);

  const pauseRun = async () => {
    if (!activeTask) return;
    try {
      setActiveTask(await api.pause(activeTask.id));
    } catch (e) {
      setBanner(errMsg(e));
    }
  };

  const undoChanges = () => {
    if (!activeTask) return;
    const n = activeTask.actions.filter((a) => a.rollback_status === 'available' || a.rollback_status === 'failed').length;
    if (!window.confirm(`Undo ${n} completed change(s)? Anything edited since the action will be left alone and reported.`)) return;
    void mutate('rollback', () => api.rollback(activeTask.id));
  };

  async function mutate(label: string, fn: () => Promise<TaskOut>) {
    setBusyAction(label);
    setBanner(null);
    try {
      const t = await fn();
      setActiveTask(t);
      upsertHistory(t);
    } catch (e) {
      setBanner(errMsg(e));
    } finally {
      setBusyAction(null);
    }
  }

  const approveThenRun = async (id: string, actionIds?: string[]) => {
    const approved = await api.approve(id, actionIds);
    if (approved.status === 'approved') {
      return api.run(id);
    }
    return approved;
  };

  const runCommand = async (rawCommand: string) => {
    const trimmed = rawCommand.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setBanner(null);
    try {
      const t = activeTask && ['awaiting_approval', 'awaiting_input'].includes(activeTask.status)
        ? await api.replyTask(activeTask.id, trimmed)
        : await api.createTask(trimmed, crypto.randomUUID());
      setActiveTask(t);
      upsertHistory(t);
      setCommand('');
    } catch (e) {
      setBanner(errMsg(e));
      if (voiceWaitingForTaskRef.current) {
        voiceWaitingForTaskRef.current = false;
        voiceTaskActiveRef.current = false;
        if (handsFreeRef.current) {
          voiceRestartRef.current = window.setTimeout(() => {
            voiceRestartRef.current = null;
            drainVoiceQueueRef.current();
            startListeningRef.current();
          }, 1500);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };
  runCommandRef.current = runCommand;

  const submitCommand = (e: FormEvent) => {
    e.preventDefault();
    void runCommand(command);
  };

  const stopHandsFree = (message: string) => {
    const queuedCount = voiceQueueRef.current.length;
    handsFreeRef.current = false;
    setHandsFree(false);
    setListening(false);
    voiceWaitingForTaskRef.current = false;
    voiceTaskActiveRef.current = false;
    voiceSpeakingRef.current = false;
    speechEpochRef.current += 1;
    speechFinishRef.current = () => {};
    interruptedSpeechRef.current = false;
    spokenTextRef.current = '';
    voiceQueueRef.current = [];
    voiceCommandRef.current = '';
    if (voiceDebounceRef.current !== null) window.clearTimeout(voiceDebounceRef.current);
    if (voiceRestartRef.current !== null) window.clearTimeout(voiceRestartRef.current);
    voiceDebounceRef.current = null;
    voiceRestartRef.current = null;
    recognitionRef.current?.abort();
    recognitionRef.current = null;
    playerRef.current?.stop();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    setVoiceMessage(
      queuedCount > 0 ? `${message} Cleared ${queuedCount} queued command(s).` : message,
    );
  };

  const beginFollowUp = () => {
    voiceTaskActiveRef.current = false;
    voiceWaitingForTaskRef.current = false;
    if (recognitionRef.current) return;
    handsFreeRef.current = true;
    setHandsFree(true);
    startListeningRef.current();
    followUpTimerRef.current = window.setTimeout(() => {
      followUpTimerRef.current = null;
      if (voiceCommandRef.current || voiceDebounceRef.current !== null || voiceQueueRef.current.length) return;
      handsFreeRef.current = false;
      setHandsFree(false);
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      setListening(false);
    }, followUpMs);
  };

  const toggleVoiceInput = () => {
    if (handsFreeRef.current) {
      stopHandsFree('Hands-free voice mode stopped.');
      return;
    }
    const speechWindow = window as typeof window & {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    const Recognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    const useWhisper = speechReadyRef.current && WhisperRecognition.supported;
    if (!Recognition && !useWhisper) {
      setVoiceMessage('Voice input is not supported in this browser. Please use Chrome or type your command.');
      return;
    }

    if (listenModeRef.current === 'off') {
      setVoiceMessage('Listen mode is Off (Settings). Type your command instead.');
      return;
    }
    handsFreeRef.current = true;
    setHandsFree(true);
    voiceNetworkErrorsRef.current = 0;
    setVoiceMessage(listenModeRef.current === 'push_to_talk'
      ? 'Listening for one command…'
      : 'Hands-free is on. Speak commands at any time; they will run one by one.');

    drainVoiceQueueRef.current = () => {
      if (
        !handsFreeRef.current ||
        voiceTaskActiveRef.current ||
        voiceQueueRef.current.length === 0 ||
        submittingRef.current
      )
        return;
      const nextCommand = voiceQueueRef.current.shift();
      if (!nextCommand) return;
      voiceTaskActiveRef.current = true;
      voiceWaitingForTaskRef.current = true;
      setCommand(nextCommand);
      setVoiceMessage(
        voiceQueueRef.current.length
          ? `Working on: ${nextCommand}. ${voiceQueueRef.current.length} command(s) queued.`
          : `Working on: ${nextCommand}. Keep speaking to queue more commands.`,
      );
      void runCommandRef.current(nextCommand);
    };

    startListeningRef.current = () => {
      if (!handsFreeRef.current || recognitionRef.current) return;
      if (followUpTimerRef.current !== null) {
        window.clearTimeout(followUpTimerRef.current);
        followUpTimerRef.current = null;
      }
      const recognition = (useWhisper ? new WhisperRecognition() : new Recognition!()) as unknown as SpeechRecognitionLike;
      if (recognition instanceof WhisperRecognition) {
        recognition.prompt = [prefs?.persona.name, 'Chrome, Notepad, Word, Excel, YouTube, WhatsApp'].filter(Boolean).join(', ');
        recognition.onspeechstart = () => {
          // Barge-in: the user began talking while the assistant was speaking.
          if (!voiceSpeakingRef.current && !playerRef.current?.speaking) return;
          interruptedSpeechRef.current = true;
          voiceSpeakingRef.current = false;
          playerRef.current?.stop();
          speechFinishRef.current();
          setVoiceMessage('Ji, sun raha hoon. Agli command batayein.');
        };
      }
      recognition.lang = voiceLanguage;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onresult = (event) => {
        if (!handsFreeRef.current) return;
        const heard = Array.from({ length: event.results.length - event.resultIndex }, (_, offset) => event.results[event.resultIndex + offset][0].transcript.trim()).filter(Boolean).join(' ');
        // Ignore the assistant's own short reply if the microphone hears it.
        if (voiceSpeakingRef.current && isSpeechEcho(heard, spokenTextRef.current)) return;
        if (heard && followUpTimerRef.current !== null) {
          window.clearTimeout(followUpTimerRef.current);
          followUpTimerRef.current = null;
        }
        if (heard) setHeard([voiceCommandRef.current, heard].filter(Boolean).join(' '));
        if (heard && voiceSpeakingRef.current) {
          interruptedSpeechRef.current = true;
          voiceSpeakingRef.current = false;
          speechFinishRef.current();
          playerRef.current?.stop();
          playerRef.current?.stop();
          if ('speechSynthesis' in window) window.speechSynthesis.cancel();
          setVoiceMessage('Ji, sun raha hoon. Agli command batayein.');
        }
        const finalText = Array.from({ length: event.results.length - event.resultIndex }, (_, offset) => {
          const result = event.results[event.resultIndex + offset];
          return result.isFinal ? result[0].transcript.trim() : '';
        }).filter(Boolean).join(' ');
        if (!finalText) return;
        voiceNetworkErrorsRef.current = 0;
        const nextText = interruptedSpeechRef.current ? afterInterruption(finalText) : finalText;
        interruptedSpeechRef.current = false;
        if (!nextText) return;
        voiceCommandRef.current = [voiceCommandRef.current, nextText].filter(Boolean).join(' ').trim();
        setHeard(voiceCommandRef.current);
        setVoiceMessage(`Heard: ${voiceCommandRef.current}. Finish speaking or pause briefly to send.`);
        if (voiceDebounceRef.current !== null) window.clearTimeout(voiceDebounceRef.current);
        voiceDebounceRef.current = window.setTimeout(() => {
          voiceDebounceRef.current = null;
          const transcript = voiceCommandRef.current.trim();
          voiceCommandRef.current = '';
          if (!transcript || !handsFreeRef.current) return;
          voiceQueueRef.current.push(transcript);
          setHeard('');
          setVoiceMessage(
            voiceTaskActiveRef.current
              ? `Queued: ${transcript}. ${voiceQueueRef.current.length} command(s) waiting.`
              : `Starting: ${transcript}`,
          );
          drainVoiceQueueRef.current();
          if (listenModeRef.current === 'push_to_talk') {
            // One command per press: the microphone turns off once the command is sent.
            handsFreeRef.current = false;
            setHandsFree(false);
            recognitionRef.current?.abort();
            recognitionRef.current = null;
            setListening(false);
          }
        }, recognition instanceof WhisperRecognition ? 500 : 2200); // Whisper input is already a finished sentence
        if (recognition instanceof WhisperRecognition) speechEndedAtRef.current = recognition.lastSpeechEndAt;
      };
      recognition.onerror = (event) => {
        if (!handsFreeRef.current) return;
        if (event.error === 'network') {
          voiceNetworkErrorsRef.current += 1;
          setVoiceMessage('Voice service network error. Hands-free will keep retrying; check internet or microphone access.');
          return;
        }
        if (event.error === 'no-speech' || event.error === 'aborted') return;
        stopHandsFree(
          event.error === 'not-allowed' || event.error === 'service-not-allowed'
            ? 'Microphone access was blocked. Allow microphone access for this site and try again.'
            : `Voice input stopped (${event.error}). Check microphone permission and try again.`,
        );
      };
      recognition.onend = () => {
        setListening(false);
        if (recognitionRef.current === recognition) recognitionRef.current = null;
        if (!handsFreeRef.current) return;
        const delay = voiceNetworkErrorsRef.current
          ? Math.min(1000 * 2 ** (voiceNetworkErrorsRef.current - 1), 15000)
          : 350;
        voiceRestartRef.current = window.setTimeout(() => {
          voiceRestartRef.current = null;
          startListeningRef.current();
        }, delay);
      };
      recognitionRef.current = recognition;
      try {
        recognition.start();
        setListening(true);
        setVoiceMessage(listenModeRef.current === 'push_to_talk' ? 'Listening… say one command.' : 'Hands-free is on. Listening… Speak a command.');
      } catch (error) {
        recognitionRef.current = null;
        setListening(false);
        stopHandsFree(`Could not start voice input: ${errMsg(error)}`);
      }
    };
    startListeningRef.current();
  };

  toggleVoiceRef.current = toggleVoiceInput;
  // Hooks for the desktop tray (fixed calls only): start/stop listening, open a page.
  useEffect(() => {
    (window as unknown as { __agentx?: unknown }).__agentx = {
      toggleVoice: () => toggleVoiceRef.current(),
      openView: (v: View) => setView(v),
    };
  }, []);
  // While a dialog is open, the page behind it can't be focused or read (aria-modal alone is not enforced).
  const modalOpen = activeTask?.status === 'awaiting_approval' || (prefs !== null && !prefs.onboarded);
  const inertWhenModal = (modalOpen ? { inert: 'true' } : {}) as Record<string, string>;  // React 18 drops inert=""
  const micSupported = typeof window !== 'undefined' && ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window);
  const voiceState = deriveVoiceState({ listening: listening || handsFree, heard, submitting, speaking, task: activeTask, error: Boolean(banner) });

  useEffect(() => {
    if (!activeTask) return;
    if (!['awaiting_input', 'awaiting_approval', 'waiting_retry', 'completed', 'partially_completed', 'failed', 'rejected', 'cancelled'].includes(activeTask.status)) return;
    const stateKey = `${activeTask.id}:${activeTask.status}`;
    if (spokenTaskStates.current.has(stateKey)) return;
    spokenTaskStates.current.add(stateKey);
    const isTerminal = ['completed', 'partially_completed', 'failed', 'rejected', 'cancelled'].includes(activeTask.status);
    const finishVoiceTask = () => {
      if ((isTerminal || activeTask.status === 'awaiting_input' || activeTask.status === 'awaiting_approval' || activeTask.status === 'waiting_retry') && voiceTaskActiveRef.current) {
        voiceWaitingForTaskRef.current = false;
        voiceTaskActiveRef.current = false;
        drainVoiceQueueRef.current();
      }
      startListeningRef.current();
    };
    const message = spokenReply(activeTask, voiceLanguage);
    setLastReply(activeTask.plan_note && activeTask.status === 'completed' ? activeTask.plan_note : message);
    const serverVoice = speechReadyRef.current;
    if ((!serverVoice && !('speechSynthesis' in window)) || prefs?.persona.speak_replies === false) {
      if (handsFreeRef.current) finishVoiceTask();
      return;
    }

    const epoch = ++speechEpochRef.current;
    playerRef.current?.stop();
    playerRef.current?.stop();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    spokenTextRef.current = message;
    voiceSpeakingRef.current = true;
    setSpeaking(true);
    const finish = finishOnce(() => {
      setSpeaking(false);
      if (epoch !== speechEpochRef.current) return;
      voiceSpeakingRef.current = false;
      if (handsFreeRef.current) finishVoiceTask();
      else if (serverVoice && followUpMs > 0 && listenModeRef.current === 'push_to_talk' && voiceTaskActiveRef.current && isTerminal) beginFollowUp();
    });
    speechFinishRef.current = finish;
    if (handsFreeRef.current) startListeningRef.current();

    if (serverVoice) {
      // The phrase comes from the server so "Done" can only be said for a task the verifier confirmed.
      void (async () => {
        let text = message;
        try {
          text = await voiceApi.line({ task: activeTask, ...wording() });
        } catch {
          // keep the short built-in phrase
        }
        if (epoch !== speechEpochRef.current) return;
        spokenTextRef.current = text;
        setLastReply(activeTask.plan_note && activeTask.status === 'completed' ? activeTask.plan_note : text);
        try {
          await playerRef.current?.speak(text, () => {
            const started = speechEndedAtRef.current;
            if (started > 0) {
              speechEndedAtRef.current = 0;
              void voiceApi.latency('local', { first_word_played: performance.now() - started });
            }
          });
        } finally {
          finish();
        }
      })();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(message);
    utterance.lang = voiceLanguage;
    utterance.onend = finish;
    utterance.onerror = finish;
    try {
      window.speechSynthesis.speak(utterance);
    } catch {
      // A broken audio device must never take the UI down; the reply is still on screen.
      finish();
    }
  }, [activeTask, voiceLanguage, prefs?.persona.speak_replies]);

  // Spoken acknowledgement for tasks that take a moment ("Opening Chrome."), and a progress line for slow ones.
  // Fast tasks never trigger either: the timer is cleared as soon as the task finishes.
  const ackedTasks = useRef(new Set<string>());
  useEffect(() => {
    if (!speechReady || prefs?.persona.speak_replies === false || !activeTask) return;
    if (!['planned', 'approved', 'executing'].includes(activeTask.status)) return;
    const speakLine = async (key: string, body: Parameters<typeof voiceApi.line>[0]) => {
      if (ackedTasks.current.has(key) || voiceSpeakingRef.current) return;
      ackedTasks.current.add(key);
      try {
        await playerRef.current?.speak(await voiceApi.line(body));
      } catch {
        // an acknowledgement is optional
      }
    };
    const first = activeTask.actions[0];
    const ack = window.setTimeout(() => {
      void speakLine(`${activeTask.id}:ack`, first ? { tool: first.tool_name, args: first.args, ...wording() } : { event: 'acknowledge', ...wording() });
    }, 900);
    const progress = window.setTimeout(() => {
      void speakLine(`${activeTask.id}:progress`, { event: 'progress', ...wording() });
    }, 4500);
    return () => {
      window.clearTimeout(ack);
      window.clearTimeout(progress);
    };
  }, [activeTask?.id, activeTask?.status, speechReady, voiceLanguage, prefs?.persona.speak_replies]);

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

  useEffect(
    () => () => {
      handsFreeRef.current = false;
      voiceQueueRef.current = [];
      voiceTaskActiveRef.current = false;
      if (voiceDebounceRef.current !== null) window.clearTimeout(voiceDebounceRef.current);
      if (voiceRestartRef.current !== null) window.clearTimeout(voiceRestartRef.current);
      recognitionRef.current?.abort();
      playerRef.current?.stop();
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    },
    [],
  );

  const selectTask = (t: TaskOut) => {
    setBanner(null);
    setActiveTask(t);
    void refreshTask(t.id).catch((e) => setBanner(errMsg(e)));
  };

  const doneCount =
    activeTask?.actions.filter((a) => a.status === 'succeeded').length ?? 0;
  const totalCount = activeTask?.actions.length ?? 0;

  return (
    <div className="min-h-screen pb-20 md:pb-0 md:pl-[84px]">
      <Rail view={view} onChange={setView} />
      {activeTask?.status === 'awaiting_approval' && (
        <ActionPreview
          task={activeTask}
          busy={busy}
          reply={command}
          onReplyChange={setCommand}
          onReplySubmit={submitCommand}
          onApprove={(ids) => void mutate('confirm', () => approveThenRun(activeTask.id, ids))}
          onReject={() => void mutate('reject-all', () => api.reject(activeTask.id))}
          micLabel={handsFree ? 'Stop microphone' : 'Answer by voice'}
          onMic={prefs?.listen_mode === 'off' ? undefined : toggleVoiceInput}
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
            if (startVoice) toggleVoiceInput();
            else window.setTimeout(() => commandInputRef.current?.focus(), 0);
          }}
        />
      )}
      {/* Header */}
      <header {...inertWhenModal} className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-3">
            <div
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-700 text-lg font-bold text-white"
              aria-hidden="true"
            >
              AX
            </div>
            <div>
              <h1 className="text-lg font-bold leading-tight text-slate-900">{prefs?.persona.name ?? 'Agent X'}</h1>
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
<button type="button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-pressed={theme === 'light'}
              title="Switch between the dark and light theme" className="rounded-md px-3 py-1.5 text-sm font-medium ring-1 ring-inset ring-slate-300">
              {theme === 'dark' ? 'Light theme' : 'Dark theme'}
            </button>
          </div>
        </div>
      </header>

      {view !== 'assistant' && (
        <main {...inertWhenModal} className="mx-auto max-w-3xl px-4 py-6">
          {banner && <p role="alert" className="mb-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{banner}</p>}
          {view === 'settings' && prefs && <SettingsPage prefs={prefs} config={config} usage={usage} onSave={savePreferences} />}
          {view === 'characters' && prefs && (
            <CharactersPage persona={prefs.persona} catalog={catalog} language={voiceLanguage} speechReady={speechReady} onSave={savePreferences} />
          )}
          {view === 'memory' && prefs && <MemoryPage memoryEnabled={prefs.memory_enabled} onToggle={(on) => void savePreferences({ memory_enabled: on }).catch(() => {})} />}
          {!prefs && <p className="text-sm text-slate-500">Loading preferences…</p>}
        </main>
      )}
      <main {...inertWhenModal} hidden={view !== 'assistant'} className={`mx-auto max-w-7xl gap-6 px-4 py-6 lg:grid-cols-[1fr_320px] ${view === 'assistant' ? 'grid' : 'hidden'}`}>
        {/* Main column */}
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

          {/* Command input */}
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex flex-col items-center gap-3 sm:flex-row sm:items-start">
              {prefs && prefs.persona.show_avatar ? (
                <div className="flex flex-col items-center gap-2">
                  <button
                    type="button"
                    data-testid="orb"
                    data-state={voiceState}
                    onClick={prefs.listen_mode === 'off' ? () => commandInputRef.current?.focus() : toggleVoiceInput}
                    aria-label={`${prefs.persona.name}: ${STATE_LABEL[voiceState]}. ${prefs.listen_mode === 'off' ? 'Press to type a command' : handsFree ? 'Press to stop listening' : 'Press or Ctrl+Space to speak'}`}
                    className="rounded-full outline-none focus-visible:ring-4 focus-visible:ring-blue-300"
                  >
                    <Avatar
                      id={prefs.persona.avatar_id}
                      color={accentFor(prefs.persona, catalog)}
                      mood={moodFor(voiceState, activeTask?.status === 'completed' && !speaking)}
                      level={avatarLevel}
                      size={144}
                      image={prefs.persona.avatar_id === 'custom' ? loadCustomImage() : null}
                      label={`${prefs.persona.name}, ${STATE_LABEL[voiceState]}`}
                    />
                  </button>
                  <p className="text-sm font-medium text-slate-700" aria-live="polite" data-testid="orb-label">{STATE_LABEL[voiceState]}</p>
                  {prefs.listen_mode !== 'off' && (
                    <>
                      <button type="button" onClick={toggleVoiceInput} data-testid="start-ai"
                        className={`rounded-full px-5 py-2 text-sm font-semibold ${handsFree ? 'bg-red-700 text-white' : 'bg-blue-700 text-white'}`}>
                        {handsFree ? 'Stop' : 'Start AI'}
                      </button>
                      <p className="text-xs text-slate-500"><kbd className="rounded border px-1">Ctrl</kbd> + <kbd className="rounded border px-1">Space</kbd></p>
                    </>
                  )}
                </div>
              ) : (
                <Orb
                  state={voiceState}
                  color={prefs?.persona.orb_color}
                  name={prefs?.persona.name}
                  onActivate={prefs?.listen_mode === 'off' ? () => commandInputRef.current?.focus() : toggleVoiceInput}
                  activateHint={prefs?.listen_mode === 'off' ? 'Press to type a command' : handsFree ? 'Press to stop listening' : 'Press or Ctrl+Space to speak'}
                />
              )}
              <Transcript heard={heard} lastCommand={activeTask?.command} reply={lastReply} name={prefs?.persona.name ?? 'Agent X'} />
            </div>
            <h2 className="text-base font-semibold text-slate-900">New command</h2>
            <p className="mt-1 text-sm text-slate-500">
              Talk to it like Gemini: open Chrome/YouTube, close a tab or app, create or zip
              folders, edit a document, or draft an email (you still send or cancel it). If it
              asks “did you mean this one?”, answer yes and it will continue.
            </p>
            <form onSubmit={submitCommand} className="mt-4 flex flex-wrap gap-3">
              <label htmlFor="command-input" className="sr-only">
                Natural-language command
              </label>
              <input
                id="command-input"
                ref={commandInputRef}
                type="text"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="Open Chrome, create a folder, move a file…"
                disabled={submitting}
                autoComplete="off"
                className="min-w-[14rem] basis-full flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-base shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:bg-slate-50"
              />
              <label htmlFor="voice-language" className="sr-only">
                Voice language
              </label>
              <select
                id="voice-language"
                value={voiceLanguage}
                onChange={(e) => setVoiceLanguage(e.target.value)}
                disabled={listening || submitting}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
              >
                <option value="ur-PK">Urdu</option>
                <option value="hi-IN">Hindi</option>
                <option value="en-US">English</option>
              </select>
              {prefs?.listen_mode !== 'off' && (<>
              <button
                type="button"
                onClick={toggleVoiceInput}
                disabled={submitting && !handsFree}
                aria-pressed={handsFree}
                aria-keyshortcuts="Control+Space"
                className={`rounded-lg px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${
                  listening ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'
                }`}
              >
                {handsFree ? (listening ? 'Stop listening' : 'Listening on') : prefs?.listen_mode === 'hands_free' ? '🎙 Start hands-free' : '🎙 Speak'}
              </button>
              <button
                type="button"
                onClick={() => {
                  interruptedSpeechRef.current = true;
                  speechFinishRef.current();
                  playerRef.current?.stop();
                  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
                  startListeningRef.current();
                  setVoiceMessage('Bolna rok diya. Agli command batayein.');
                }}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                Stop speaking
              </button>
              </>)}
              <button
                type="submit"
                disabled={submitting || command.trim().length === 0}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-700 px-5 py-2.5 text-base font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
              >
                {submitting ? <Spinner label="Planning…" /> : 'Run command'}
              </button>
            </form>
            {voiceMessage && (
              <p className="mt-2 text-sm text-slate-600" aria-live="polite">{voiceMessage}</p>
            )}
            {config && !config.browser_connected && (
              <p className="mt-2 text-sm text-amber-800">
                Browser tabs abhi connected nahi.{' '}
                <a href={`/browser-extension/setup.html#token=${encodeURIComponent(apiToken())}`} target="_blank" rel="noreferrer" className="font-semibold underline">Chrome/Edge setup kholein</a>
              </p>
            )}
            <p className="mt-1 text-xs text-slate-500">
              {usage && usage.plan !== 'unlimited' && (
                <span className="mb-1 block font-medium" data-testid="usage-line">
                  Plan {usage.plan}: tasks today {usage.used.tasks_day}/{usage.limits.tasks_day ?? '∞'}, cloud AI calls today {usage.used.ai_calls_day}/{usage.limits.ai_calls_day ?? '∞'}
                </span>
              )}
              Keep hands-free on to speak more commands while work is running; commands are queued and run one by one. When a confirmation appears, say yes / haan kar do to approve, or no / cancel to stop. Voice input needs microphone permission and internet; transcripts go to the selected AI provider.
            </p>
          </section>

          {/* Active task */}
          {activeTask ? (
            <section
              aria-live="polite"
              className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="text-base font-semibold text-slate-900">Current task</h2>
                  <p className="mt-1 break-words text-sm text-slate-700">
                    <span className="font-medium">Command:</span> “{activeTask.command}”
                  </p>
                  <p className="mt-1 font-mono text-xs text-slate-400">id: {activeTask.id}</p>
                </div>
                <span
                  data-testid="task-status"
                  data-status={activeTask.status}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${statusClasses(activeTask.status)}`}
                >
                  {(activeTask.status === 'executing' ||
                    activeTask.status === 'awaiting_approval') && <Spinner />}
                  {prettyStatus(activeTask.status)}
                </span>
              </div>

              {activeTask.plan_note && (
                <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                  <span className="font-medium text-slate-700">Plan: </span>
                  {activeTask.plan_note}
                </p>
              )}

              {activeTask.provider_note && <p className="mt-3 rounded bg-blue-50 p-3 text-sm">{activeTask.provider_note}</p>}
              {activeTask.status === 'executing' && (
                <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg bg-blue-50 p-3 text-sm text-blue-900" role="status">
                  <Spinner label="Running…" />
                  <button type="button" onClick={() => void pauseRun()} className="rounded-md bg-white px-3 py-1.5 font-medium ring-1 ring-inset ring-blue-300 hover:bg-blue-100">
                    Pause
                  </button>
                </div>
              )}
              {activeTask.status === 'paused' && (
                <div className="mt-3 rounded-lg bg-amber-50 p-4 text-sm" role="status">
                  <p>
                    Task paused. The remaining approved actions have not run.
                    {activeTask.actions.some((a) => a.status === 'paused') && ' A download was stopped part-way; Resume continues it from where it stopped.'}
                  </p>
                  <button type="button" disabled={busy} onClick={() => void mutate('resume', () => api.run(activeTask.id))} className="mt-2 rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-50">Resume task</button>
                </div>
              )}
              {activeTask.partial && (
                <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="status" data-testid="partial-banner">
                  <p className="font-semibold">Only part of this task was completed</p>
                  <p>
                    {activeTask.partial.succeeded} succeeded, {activeTask.partial.failed} failed, {activeTask.partial.not_run} did not run.
                    {activeTask.partial.changes_remain.length > 0 && ` Changes already made: ${activeTask.partial.changes_remain.join(', ')}.`}
                  </p>
                </div>
              )}
              {activeTask.status === 'waiting_retry' && (
                <div className="mt-3 rounded-lg bg-amber-50 p-4 text-sm" role="status">
                  <p>AI limit par task pause hai. Volume, brightness aur simple app commands abhi bhi use kar sakti hain.</p>
                  {activeTask.retry_at && <p className="mt-1">Resume after: {new Date(activeTask.retry_at * 1000).toLocaleString()}</p>}
                  <button disabled={busy || Date.now() < (activeTask.retry_at ?? 0) * 1000} onClick={() => void mutate('resume', () => api.run(activeTask.id))} className="mt-2 rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-50">Resume task</button>
                </div>
              )}
              {activeTask.error && (
                <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">
                  {activeTask.error}
                </p>
              )}

              {totalCount > 0 && (
                <p className="mt-3 text-sm text-slate-600">
                  Progress: {doneCount} of {totalCount} actions succeeded
                </p>
              )}

              <h3 className="mt-4 text-sm font-semibold text-slate-900">Planned actions</h3>
              {activeTask.actions.length === 0 ? (
                <p className="mt-2 text-sm text-slate-500">
                  Reply above with the missing details to continue.
                </p>
              ) : (
                <ul className="mt-2 flex flex-col gap-3">
                  {activeTask.actions
                    .slice()
                    .sort((a, b) => a.seq - b.seq)
                    .map((a) => (
                      <ActionCard
                        key={a.id}
                        action={a}
                        taskStatus={activeTask.status}
                        busy={busy}
                        onApprove={(actionId) =>
                          void mutate(`approve-${actionId}`, () =>
                            approveThenRun(activeTask.id, [actionId]),
                          )
                        }
                        onReject={(actionId) =>
                          void mutate(`reject-${actionId}`, () =>
                            api.reject(activeTask.id, [actionId]),
                          )
                        }
                      />
                    ))}
                </ul>
              )}

              <div className="mt-4 flex flex-wrap gap-2">
                {activeTask.status === 'awaiting_approval' && (
                  <>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void mutate('approve-all', () => approveThenRun(activeTask.id))
                      }
                      className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      {busyAction === 'approve-all' ? <Spinner label="Approving…" /> : activeTask.actions.some((a) => a.status === 'pending' && a.risk === 'high') ? 'Approve all except high-risk' : 'Approve all'}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void mutate('reject-all', () => api.reject(activeTask.id))
                      }
                      className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-red-700 ring-1 ring-inset ring-red-300 hover:bg-red-50 disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </>
                )}
                {activeTask.status === 'approved' && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void mutate('run', () => api.run(activeTask.id))}
                    className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
                  >
                    {busyAction === 'run' ? <Spinner label="Starting…" /> : 'Execute'}
                  </button>
                )}
                {!['executing', 'awaiting_approval', 'approved', 'paused'].includes(activeTask.status) &&
                  activeTask.actions.some((a) => a.rollback_status === 'available' || a.rollback_status === 'failed') && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={undoChanges}
                      className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-red-700 ring-1 ring-inset ring-red-300 hover:bg-red-50 disabled:opacity-50"
                    >
                      {busyAction === 'rollback' ? <Spinner label="Undoing…" /> : 'Undo changes'}
                    </button>
                  )}
                {transitional && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void mutate('cancel', () => api.cancel(activeTask.id))}
                    className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Cancel task
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setBanner(null);
                    void refreshTask(activeTask.id).catch((e) => setBanner(errMsg(e)));
                  }}
                  className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:opacity-50"
                >
                  Refresh
                </button>
              </div>
            </section>
          ) : (
            <section className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center shadow-sm">
              <p className="text-sm text-slate-500">
                No task yet. Type a command above and press <strong>Run command</strong> to see the
                AI-generated plan here.
              </p>
            </section>
          )}
        </div>

        {/* Sidebar */}
        <aside className="flex min-w-0 flex-col gap-6">
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-slate-900">Task history</h2>
              <button
                type="button"
                onClick={() => void loadHistory()}
                className="text-sm font-medium text-blue-700 hover:text-blue-900"
              >
                Reload
              </button>
            </div>
            {historyError && (
              <p role="alert" className="mt-2 text-sm text-red-600">
                {historyError}
              </p>
            )}
            {history.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">No tasks yet.</p>
            ) : (
              <ul className="mt-3 flex max-h-[480px] flex-col gap-2 overflow-y-auto">
                {history.map((t) => (
                  <li key={t.id}>
                    <button
                      type="button"
                      onClick={() => selectTask(t)}
                      aria-current={activeTask?.id === t.id}
                      className={`w-full rounded-lg border p-3 text-left transition hover:border-blue-300 hover:bg-blue-50 ${
                        activeTask?.id === t.id
                          ? 'border-blue-400 bg-blue-50'
                          : 'border-slate-200 bg-white'
                      }`}
                    >
                      <p className="truncate text-sm font-medium text-slate-900">{t.command}</p>
                      <div className="mt-1.5 flex items-center justify-between gap-2">
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClasses(t.status)}`}
                        >
                          {prettyStatus(t.status)}
                        </span>
                        <span className="text-xs text-slate-400">
                          {new Date(t.created_at).toLocaleString()}
                        </span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <p className="mx-auto max-w-7xl px-4 py-4 text-xs text-slate-500">
          Agent X AI · Local desktop assistant
        </p>
      </footer>
    </div>
  );
}
