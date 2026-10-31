import { useEffect, useState } from 'react';
import { apiToken, type ConfigStatus, type Persona, type Preferences, type PreferencesUpdate, type UsageOut } from '../api';
import { desktopApi } from '../desktop';

interface SettingsPageProps {
  prefs: Preferences;
  config: ConfigStatus | null;
  usage: UsageOut | null;
  onSave: (patch: PreferencesUpdate) => Promise<void>;
}

function Radio<T extends string>({ name, value, current, label, help, onChange }: {
  name: string; value: T; current: T; label: string; help: string; onChange: (v: T) => void;
}) {
  const id = `${name}-${value}`;
  return (
    <div className="flex items-start gap-2">
      <input id={id} type="radio" name={name} value={value} checked={current === value} onChange={() => onChange(value)} className="mt-1" />
      <label htmlFor={id} className="text-sm">
        <span className="font-medium">{label}</span>
        <span className="block text-slate-500">{help}</span>
      </label>
    </div>
  );
}

export function SettingsPage({ prefs, config, usage, onSave }: SettingsPageProps) {
  const [persona, setPersona] = useState<Persona>(prefs.persona);
  const [saved, setSaved] = useState<string | null>(null);
  // Choices update at once; the server copy follows (and wins if the save fails).
  const [choice, setChoice] = useState({ listen_mode: prefs.listen_mode, provider_mode: prefs.provider_mode, risk_policy: prefs.risk_policy });
  useEffect(() => setChoice({ listen_mode: prefs.listen_mode, provider_mode: prefs.provider_mode, risk_policy: prefs.risk_policy }),
    [prefs.listen_mode, prefs.provider_mode, prefs.risk_policy]);
  const [autostart, setAutostart] = useState<boolean | null>(null);
  const desktop = desktopApi();

  useEffect(() => setPersona(prefs.persona), [prefs.persona]);
  useEffect(() => {
    void desktop?.get_autostart?.().then(setAutostart).catch(() => setAutostart(null));
  }, [desktop]);

  const save = async (patch: PreferencesUpdate, message: string) => {
    if (patch.listen_mode || patch.provider_mode || patch.risk_policy) setChoice((c) => ({ ...c, ...patch }));
    setSaved(null);
    try {
      await onSave(patch);
      setSaved(message);
    } catch {
      setChoice({ listen_mode: prefs.listen_mode, provider_mode: prefs.provider_mode, risk_policy: prefs.risk_policy });
    }
  };

  return (
    <div className="flex flex-col gap-6" data-testid="settings-page">
      <h2 className="text-lg font-semibold">Settings</h2>
      {saved && <p role="status" className="rounded bg-emerald-50 p-2 text-sm text-emerald-800">{saved}</p>}

      <section aria-labelledby="persona-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="persona-heading" className="font-semibold">Persona</h3>
        <form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save({ persona }, 'Persona saved.'); }}>
          <label className="text-sm">Assistant name
            <input value={persona.name} maxLength={40} required onChange={(e) => setPersona({ ...persona, name: e.target.value })}
              className="mt-1 w-full rounded border p-2" />
          </label>
          <label className="text-sm">Speaking language
            <select value={persona.language} onChange={(e) => setPersona({ ...persona, language: e.target.value as Persona['language'] })}
              className="mt-1 w-full rounded border p-2">
              <option value="ur-PK">Urdu / Roman Urdu</option>
              <option value="hi-IN">Hindi</option>
              <option value="en-US">English (US)</option>
              <option value="en-GB">English (UK)</option>
            </select>
          </label>
          <label className="text-sm">Reply style
            <select value={persona.style} onChange={(e) => setPersona({ ...persona, style: e.target.value as Persona['style'] })}
              className="mt-1 w-full rounded border p-2">
              <option value="friendly">Friendly</option>
              <option value="brief">Brief</option>
              <option value="formal">Formal</option>
            </select>
          </label>
          <label className="text-sm">Orb colour
            <select value={persona.orb_color} onChange={(e) => setPersona({ ...persona, orb_color: e.target.value as Persona['orb_color'] })}
              className="mt-1 w-full rounded border p-2">
              {(['indigo', 'emerald', 'rose', 'amber', 'slate'] as const).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input type="checkbox" checked={persona.speak_replies} onChange={(e) => setPersona({ ...persona, speak_replies: e.target.checked })} />
            Speak replies out loud
          </label>
          <button className="w-fit rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white">Save persona</button>
        </form>
      </section>

      <section aria-labelledby="listen-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="listen-heading" className="font-semibold">Listen mode</h3>
        <fieldset className="mt-3 flex flex-col gap-2">
          <legend className="sr-only">Listen mode</legend>
          <Radio name="listen" value="push_to_talk" current={choice.listen_mode} label="Push to talk"
            help="Press the orb or Ctrl+Space, say one command; the microphone turns off after it is sent."
            onChange={(v) => void save({ listen_mode: v }, 'Listen mode saved.')} />
          <Radio name="listen" value="hands_free" current={choice.listen_mode} label="Hands-free"
            help="The microphone stays on; commands are queued and run one by one."
            onChange={(v) => void save({ listen_mode: v }, 'Listen mode saved.')} />
          <Radio name="listen" value="off" current={choice.listen_mode} label="Off" help="Typing only; the microphone is never used."
            onChange={(v) => void save({ listen_mode: v }, 'Listen mode saved.')} />
        </fieldset>
      </section>

      <section aria-labelledby="speaking-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="speaking-heading" className="font-semibold">Listening and speaking</h3>
        <label className="mt-3 flex items-center gap-3 text-sm">
          <span className="w-44">Follow-up window (seconds)</span>
          <input type="range" min={0} max={15} value={prefs.follow_up_seconds} aria-label="Follow-up window in seconds" data-testid="follow-up"
            onChange={(e) => void save({ follow_up_seconds: Number(e.target.value) }, 'Follow-up window saved.')} />
          <span className="w-8 text-right" data-testid="follow-up-value">{prefs.follow_up_seconds}</span>
        </label>
        <p className="ml-1 text-xs text-slate-500">After a spoken reply the microphone stays open this long, so you can say "and close Notepad" without pressing the hotkey. 0 turns it off.</p>
        <label className="mt-3 flex items-center gap-3 text-sm">
          <span className="w-44">Speech volume</span>
          <input type="range" min={0} max={100} value={prefs.speech_volume} aria-label="Speech volume" data-testid="speech-volume"
            onChange={(e) => void save({ speech_volume: Number(e.target.value) }, 'Volume saved.')} />
          <span className="w-8 text-right">{prefs.speech_volume}</span>
        </label>
      </section>

      <section aria-labelledby="provider-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="provider-heading" className="font-semibold">AI provider</h3>
        <p className="mt-1 text-sm text-slate-600">
          Configured: {config ? `${config.ai_provider} (${config.ai_configured ? 'key present' : 'no key'})` : '…'}
          {config?.fallback_configured ? `, backup ${config.fallback_provider}` : ''}. Keys live in the backend environment and are never shown here.
        </p>
        <fieldset className="mt-3 flex flex-col gap-2">
          <legend className="sr-only">Provider mode</legend>
          <Radio name="provider" value="auto" current={choice.provider_mode} label="Auto"
            help="Common PC commands run locally; unclear requests may use the cloud planner."
            onChange={(v) => void save({ provider_mode: v }, 'Provider mode saved.')} />
          <Radio name="provider" value="local_only" current={choice.provider_mode} label="Local only"
            help="Nothing is sent to a cloud AI. Unclear requests ask you to rephrase."
            onChange={(v) => void save({ provider_mode: v }, 'Provider mode saved.')} />
        </fieldset>
      </section>

      <section aria-labelledby="risk-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="risk-heading" className="font-semibold">Approval policy</h3>
        <fieldset className="mt-3 flex flex-col gap-2">
          <legend className="sr-only">Approval policy</legend>
          <Radio name="risk" value="standard" current={choice.risk_policy} label="Standard"
            help="Low-risk actions (open, read) run at once; changes need approval; high risk needs your click."
            onChange={(v) => void save({ risk_policy: v }, 'Approval policy saved.')} />
          <Radio name="risk" value="strict" current={choice.risk_policy} label="Strict"
            help="Every action, even opening an app, waits for your approval."
            onChange={(v) => void save({ risk_policy: v }, 'Approval policy saved.')} />
        </fieldset>
        <p className="mt-2 text-xs text-slate-500">There is no setting that skips approvals for changes.</p>
      </section>

      <section aria-labelledby="usage-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="usage-heading" className="font-semibold">Usage</h3>
        {usage ? (
          <dl className="mt-2 grid grid-cols-2 gap-1 text-sm" data-testid="usage">
            <dt className="text-slate-500">Plan</dt><dd>{usage.plan}</dd>
            <dt className="text-slate-500">Tasks today</dt><dd>{usage.used.tasks_day}{usage.limits.tasks_day ? ` / ${usage.limits.tasks_day}` : ''}</dd>
            <dt className="text-slate-500">Cloud AI calls today</dt><dd>{usage.used.ai_calls_day}{usage.limits.ai_calls_day ? ` / ${usage.limits.ai_calls_day}` : ''}</dd>
            <dt className="text-slate-500">Cloud AI calls this month</dt><dd>{usage.used.ai_calls_month}</dd>
          </dl>
        ) : <p className="mt-2 text-sm text-slate-500">Loading…</p>}
      </section>

      <section aria-labelledby="desktop-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="desktop-heading" className="font-semibold">Desktop</h3>
        {desktop ? (
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={autostart === true} disabled={autostart === null}
              onChange={(e) => void desktop.set_autostart?.(e.target.checked).then(setAutostart)} />
            Start {prefs.persona.name} when I sign in to Windows
          </label>
        ) : (
          <p className="mt-2 text-sm text-slate-600">Autostart and the always-on-top orb are available in the desktop app (AgentDesk.exe / desktop shell).</p>
        )}
        {config && (
          <p className="mt-2 text-sm">
            Browser tab control: {config.browser_connected ? 'connected' : 'not connected'} ·{' '}
            <a className="text-blue-700 underline" href={`/browser-extension/setup.html#token=${encodeURIComponent(apiToken())}`} target="_blank" rel="noreferrer">Chrome / Edge setup</a>
          </p>
        )}
        {config && (
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer">Allowed folders and apps</summary>
            <ul className="mt-1 list-disc pl-5 font-mono text-xs">
              {config.allowed_directories.map((d) => <li key={d} className="break-all">{d}</li>)}
            </ul>
            <p className="mt-1 text-xs">Apps: {config.approved_applications.join(', ') || 'none'}</p>
          </details>
        )}
      </section>
    </div>
  );
}
