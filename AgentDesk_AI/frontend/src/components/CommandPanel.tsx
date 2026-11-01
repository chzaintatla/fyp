import type { FormEvent, RefObject } from 'react';
import { apiToken, type Catalog, type ConfigStatus, type Preferences, type TaskOut, type UsageOut } from '../api';
import { accentFor, loadCustomImage } from '../characters';
import { STATE_LABEL, type VoiceState } from '../voiceState';
import { Avatar, moodFor } from './Avatar';
import { Orb } from './Orb';
import { Spinner } from './Spinner';
import { Transcript } from './Transcript';

interface Props {
  prefs: Preferences | null;
  catalog: Catalog | null;
  config: ConfigStatus | null;
  usage: UsageOut | null;
  voiceState: VoiceState;
  activeTask: TaskOut | null;
  heard: string;
  lastReply: string | null;
  speaking: boolean;
  avatarLevel: number;
  handsFree: boolean;
  listening: boolean;
  voiceMessage: string | null;
  voiceLanguage: string;
  onVoiceLanguage: (language: string) => void;
  command: string;
  onCommand: (text: string) => void;
  submitting: boolean;
  onSubmit: (e: FormEvent) => void;
  onToggleVoice: () => void;
  onStopSpeaking: () => void;
  inputRef: RefObject<HTMLInputElement>;
}

/** The orb/avatar, the transcript, and the command box with its voice buttons. */
export function CommandPanel(p: Props) {
  const { prefs } = p;
  const listenOff = prefs?.listen_mode === 'off';
  const orbAction = listenOff ? () => p.inputRef.current?.focus() : p.onToggleVoice;
  const orbHint = listenOff ? 'Press to type a command' : p.handsFree ? 'Press to stop listening' : 'Press or Ctrl+Space to speak';
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-col items-center gap-3 sm:flex-row sm:items-start">
        {prefs && prefs.persona.show_avatar ? (
          <div className="flex flex-col items-center gap-2">
            <button
              type="button"
              data-testid="orb"
              data-state={p.voiceState}
              onClick={orbAction}
              aria-label={`${prefs.persona.name}: ${STATE_LABEL[p.voiceState]}. ${orbHint}`}
              className="rounded-full outline-none focus-visible:ring-4 focus-visible:ring-blue-300"
            >
              <Avatar
                id={prefs.persona.avatar_id}
                color={accentFor(prefs.persona, p.catalog)}
                mood={moodFor(p.voiceState, p.activeTask?.status === 'completed' && !p.speaking)}
                level={p.avatarLevel}
                size={144}
                image={prefs.persona.avatar_id === 'custom' ? loadCustomImage() : null}
                label={`${prefs.persona.name}, ${STATE_LABEL[p.voiceState]}`}
              />
            </button>
            <p className="text-sm font-medium text-slate-700" aria-live="polite" data-testid="orb-label">{STATE_LABEL[p.voiceState]}</p>
            {!listenOff && (
              <>
                <button type="button" onClick={p.onToggleVoice} data-testid="start-ai"
                  className={`rounded-full px-5 py-2 text-sm font-semibold ${p.handsFree ? 'bg-red-700 text-white' : 'bg-blue-700 text-white'}`}>
                  {p.handsFree ? 'Stop' : 'Start AI'}
                </button>
                <p className="text-xs text-slate-500"><kbd className="rounded border px-1">Ctrl</kbd> + <kbd className="rounded border px-1">Space</kbd></p>
              </>
            )}
          </div>
        ) : (
          <Orb
            state={p.voiceState}
            color={prefs?.persona.orb_color}
            name={prefs?.persona.name}
            onActivate={orbAction}
            activateHint={orbHint}
          />
        )}
        <Transcript heard={p.heard} lastCommand={p.activeTask?.command} reply={p.lastReply} name={prefs?.persona.name ?? 'Agent X'} />
      </div>
      <h2 className="text-base font-semibold text-slate-900">New command</h2>
      <p className="mt-1 text-sm text-slate-500">
        Talk to it like Gemini: open Chrome/YouTube, close a tab or app, create or zip
        folders, edit a document, or draft an email (you still send or cancel it). If it
        asks “did you mean this one?”, answer yes and it will continue.
      </p>
      <form onSubmit={p.onSubmit} className="mt-4 flex flex-wrap gap-3">
        <label htmlFor="command-input" className="sr-only">
          Natural-language command
        </label>
        <input
          id="command-input"
          ref={p.inputRef}
          type="text"
          value={p.command}
          onChange={(e) => p.onCommand(e.target.value)}
          placeholder="Open Chrome, create a folder, move a file…"
          disabled={p.submitting}
          autoComplete="off"
          className="min-w-[14rem] basis-full flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-base shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:bg-slate-50"
        />
        <label htmlFor="voice-language" className="sr-only">
          Voice language
        </label>
        <select
          id="voice-language"
          value={p.voiceLanguage}
          onChange={(e) => p.onVoiceLanguage(e.target.value)}
          disabled={p.listening || p.submitting}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
        >
          <option value="ur-PK">Urdu</option>
          <option value="hi-IN">Hindi</option>
          <option value="en-US">English</option>
        </select>
        {prefs?.listen_mode !== 'off' && (<>
        <button
          type="button"
          onClick={p.onToggleVoice}
          disabled={p.submitting && !p.handsFree}
          aria-pressed={p.handsFree}
          aria-keyshortcuts="Control+Space"
          className={`rounded-lg px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${
            p.listening ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'
          }`}
        >
          {p.handsFree ? (p.listening ? 'Stop listening' : 'Listening on') : prefs?.listen_mode === 'hands_free' ? '🎙 Start hands-free' : '🎙 Speak'}
        </button>
        <button type="button" onClick={p.onStopSpeaking} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
          Stop speaking
        </button>
        </>)}
        <button
          type="submit"
          disabled={p.submitting || p.command.trim().length === 0}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-700 px-5 py-2.5 text-base font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
        >
          {p.submitting ? <Spinner label="Planning…" /> : 'Run command'}
        </button>
      </form>
      {p.voiceMessage && (
        <p className="mt-2 text-sm text-slate-600" aria-live="polite">{p.voiceMessage}</p>
      )}
      {p.config && !p.config.browser_connected && (
        <p className="mt-2 text-sm text-amber-800">
          Browser tabs abhi connected nahi.{' '}
          <a href={`/browser-extension/setup.html#token=${encodeURIComponent(apiToken())}`} target="_blank" rel="noreferrer" className="font-semibold underline">Chrome/Edge setup kholein</a>
        </p>
      )}
      <p className="mt-1 text-xs text-slate-500">
        {p.usage && p.usage.plan !== 'unlimited' && (
          <span className="mb-1 block font-medium" data-testid="usage-line">
            Plan {p.usage.plan}: tasks today {p.usage.used.tasks_day}/{p.usage.limits.tasks_day ?? '∞'}, cloud AI calls today {p.usage.used.ai_calls_day}/{p.usage.limits.ai_calls_day ?? '∞'}
          </span>
        )}
        Keep hands-free on to speak more commands while work is running; commands are queued and run one by one. When a confirmation appears, say yes / haan kar do to approve, or no / cancel to stop. Voice input needs microphone permission and internet; transcripts go to the selected AI provider.
      </p>
    </section>
  );
}
