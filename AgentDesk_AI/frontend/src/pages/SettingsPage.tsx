import { useEffect, useState } from 'react';
import { apiToken, mailApi, type MailStatus, type ConfigStatus, type Persona, type Preferences, type PreferencesUpdate, type UsageOut } from '../api';
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

function EmailSection({ prefs, onSave }: { prefs: Preferences; onSave: (patch: PreferencesUpdate) => Promise<void> }) {
  const [status, setStatus] = useState<MailStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = () => { void mailApi.status().then(setStatus).catch(() => setStatus(null)); };
  useEffect(() => { load(); const t = window.setInterval(load, 4000); return () => window.clearInterval(t); }, []);
  const chosen = prefs.mail_provider ?? 'none';
  const connect = (provider: 'gmail' | 'outlook') => {
    setNote(null);
    void mailApi.connect(provider).then((r) => { window.open(r.url, '_blank', 'noopener'); setNote('Finish signing in in the new tab, then come back.'); }).catch((e: Error) => setNote(e.message.replace(/^Request failed \(\d+\): /, '')));
  };
  return (
    <section aria-labelledby="email-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 id="email-heading" className="font-semibold">Email</h3>
      <p className="mt-1 text-xs text-slate-500">Write and send e-mail by voice. You sign in on Google's or Microsoft's own page; the assistant never sees your password and keeps its access only in the Windows Credential Manager. It sends nothing until it has read the recipient, subject and first line back and you say "yes, send it".</p>
      <div className="mt-3 flex flex-col gap-3">
        <Radio name="mail" value="none" current={chosen} label="Off" help="No e-mail by voice." onChange={(v) => void onSave({ mail_provider: v })} />
        {(['gmail', 'outlook'] as const).map((p) => {
          const s = status?.providers[p];
          return (
            <div key={p} className="flex flex-wrap items-start gap-3">
              <div className="min-w-[14rem] flex-1">
                <Radio name="mail" value={p} current={chosen} label={p === 'gmail' ? 'Gmail' : 'Outlook / Microsoft 365'}
                  help={s?.connected ? `Connected${s.account ? ` as ${s.account}` : ''}.` : s?.configured === false ? 'Not set up: add the OAuth client id to backend\\.env (docs/EMAIL_SETUP.md).' : 'Not connected yet.'}
                  onChange={(v) => void onSave({ mail_provider: v })} />
              </div>
              {s?.connected
                ? <button type="button" className="rounded border border-slate-300 px-3 py-1 text-sm" onClick={() => void mailApi.disconnect(p).then(load)}>Disconnect</button>
                : <button type="button" className="rounded bg-blue-700 px-3 py-1 text-sm font-semibold text-white disabled:opacity-50" disabled={s?.configured === false} data-testid={`connect-${p}`} onClick={() => connect(p)}>Connect</button>}
            </div>
          );
        })}
      </div>
      {note && <p role="status" className="mt-3 text-sm text-slate-700">{note}</p>}
    </section>
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
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={prefs.anything_else !== false} data-testid="anything-else"
            onChange={(e) => void save({ anything_else: e.target.checked }, 'Saved.')} />
          <span>Ask "Anything else?" after a finished task. <span className="block text-slate-500">The conversation then stays open until you say "no" or "that's all", or stay silent for the follow-up window.</span></span>
        </label>
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={prefs.cloud_answers === true} data-testid="cloud-answers"
            onChange={(e) => void save({ cloud_answers: e.target.checked }, 'Privacy setting saved.')} />
          <span>Answer questions with the cloud AI (Groq). <span className="block text-slate-500">When on, the question you ask (and, for web answers, text of the pages read) is sent to Groq. Commands for this PC are not affected. {prefs.cloud_answers === null && 'You have not chosen yet, so questions stay on this PC.'}</span></span>
        </label>
        <p className="ml-6 mt-1 text-xs text-slate-500" data-testid="search-status">
          Web search: Wikipedia {config?.search?.wikipedia ? 'ready' : 'off (needs WIKIPEDIA_CONTACT_EMAIL in backend/.env)'}; Tavily{' '}
          {config?.search?.tavily ? 'ready' : 'off (needs a key: see docs/SEARCH_SETUP.md)'}.
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={prefs.daily_greeting} data-testid="daily-greeting"
            onChange={(e) => void save({ daily_greeting: e.target.checked }, 'Greeting setting saved.')} />
          Say a greeting the first time the app opens each day
        </label>
        <label className="mt-3 flex items-center gap-3 text-sm">
          <span className="w-44">Speech volume</span>
          <input type="range" min={0} max={100} value={prefs.speech_volume} aria-label="Speech volume" data-testid="speech-volume"
            onChange={(e) => void save({ speech_volume: Number(e.target.value) }, 'Volume saved.')} />
          <span className="w-8 text-right">{prefs.speech_volume}</span>
        </label>
      </section>

      <EmailSection prefs={prefs} onSave={onSave} />

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
