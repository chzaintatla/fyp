import { useEffect, useState } from 'react';
import type { Catalog, Persona, PersonalityId } from '../api';
import { applyAvatar } from '../characters';
import { Avatar } from './Avatar';
import { useFocusTrap } from './useFocusTrap';

interface OnboardingProps {
  cloudAnswers: boolean | null;
  persona: Persona;
  catalog: Catalog | null;
  micSupported: boolean;
  memoryEnabled: boolean;
  onFinish: (persona: Persona, memoryEnabled: boolean, cloudAnswers: boolean | null, startVoice: boolean) => void;
}

const EXAMPLE: Record<Persona['language'], string> = {
  'ur-PK': 'Documents mein Demo naam ka folder banao',
  'hi-IN': 'Documents mein Demo naam ka folder banao',
  'en-US': 'Create a folder called Demo in Documents',
  'en-GB': 'Create a folder called Demo in Documents',
};

const STEPS = 6;

/** Microphone test: shows the live level and confirms when it hears speech. Nothing is recorded or sent. */
function MicTest({ supported, name }: { supported: boolean; name: string }) {
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!supported || !navigator.mediaDevices?.getUserMedia) return;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let frame = 0;
    let loudFrames = 0;
    let cancelled = false;
    navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => {
      if (cancelled) return s.getTracks().forEach((t) => t.stop());
      stream = s;
      ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(s).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
        setLevel(peak);
        loudFrames = peak > 0.08 ? loudFrames + 1 : Math.max(0, loudFrames - 1);
        if (loudFrames > 12) setHeard(true);
        frame = requestAnimationFrame(tick);
      };
      tick();
    }).catch(() => setError('The microphone is blocked or missing. You can still type every command.'));
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close();
    };
  }, [supported]);
  if (!supported) return <p className="mt-2 text-sm text-slate-600">Voice input is not available in this browser. You can still type every command.</p>;
  return (
    <div className="mt-3">
      <p className="text-sm text-slate-600">Allow the microphone when asked, then say: <strong>“Hello{name ? ` ${name}` : ''}”</strong></p>
      <div className="mt-3 h-3 w-full overflow-hidden rounded bg-slate-200" role="meter" aria-label="Microphone level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(level * 100)}>
        <div className="h-full bg-emerald-500 transition-[width] duration-75" style={{ width: `${Math.min(100, level * 160)}%` }} />
      </div>
      <p className="mt-2 text-sm" role="status" aria-live="polite" data-testid="mic-status">
        {error ?? (heard ? 'I can hear you. The microphone works.' : 'Waiting for your voice…')}
      </p>
    </div>
  );
}

/** First run: you, the character, the personality, the microphone, then one guided command. */
export function Onboarding({ persona, catalog, micSupported, memoryEnabled, cloudAnswers, onFinish }: OnboardingProps) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Persona>(persona);
  const [memory, setMemory] = useState(memoryEnabled);
  const [cloud, setCloud] = useState<boolean | null>(cloudAnswers);
  const [filter, setFilter] = useState<'all' | 'female' | 'male'>('all');
  const ref = useFocusTrap<HTMLElement>(true, () => onFinish(draft, memory, cloud, false));
  const avatars = (catalog?.avatars ?? []).filter((a) => filter === 'all' || a.gender === filter);
  const personalities = catalog?.personalities.filter((p) => p.id !== 'custom') ?? [];
  const finish = (voice: boolean) => onFinish({ ...draft, name: draft.name.trim() || 'Agent X' }, memory, cloud, voice);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4">
      <section ref={ref} role="dialog" aria-modal="true" aria-labelledby="onboarding-title" data-testid="onboarding"
        className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <p className="text-xs font-semibold text-slate-500">Step {step + 1} of {STEPS}</p>
        {step === 0 && (
          <>
            <h2 id="onboarding-title" className="mt-1 text-xl font-semibold">Welcome to {persona.name}</h2>
            <p className="mt-2 text-sm text-slate-600">
              Speak or type what you want done on this PC. Anything that changes files or settings is shown to you first
              and runs only after you approve it.
            </p>
            <label htmlFor="onboarding-name" className="mt-4 block text-sm font-medium">What should I call you?</label>
            <input id="onboarding-name" value={draft.user_name} maxLength={40} onChange={(e) => setDraft({ ...draft, user_name: e.target.value })}
              className="mt-1 w-full rounded border p-2" placeholder="Your name (optional)" />
            <label htmlFor="onboarding-language" className="mt-4 block text-sm font-medium">Language you will speak</label>
            <select id="onboarding-language" value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value as Persona['language'] })}
              className="mt-1 w-full rounded border p-2">
              <option value="ur-PK">Urdu / Roman Urdu</option>
              <option value="hi-IN">Hindi</option>
              <option value="en-US">English</option>
            </select>
            <label className="mt-4 flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={memory} onChange={(e) => setMemory(e.target.checked)} />
              <span>Remember my name, favourite apps and folders on this PC. You can view, edit and delete everything on the Memory page.</span>
            </label>
          </>
        )}
        {step === 1 && (
          <>
            <h2 id="onboarding-title" className="mt-1 text-xl font-semibold">Choose your character</h2>
            <div role="group" aria-label="Filter characters" className="mt-3 flex gap-1">
              {(['all', 'female', 'male'] as const).map((f) => (
                <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
                  className={`rounded-md px-3 py-1 text-sm ring-1 ring-inset ${filter === f ? 'bg-blue-700 text-white ring-blue-700' : 'bg-white ring-slate-300'}`}>
                  {f === 'all' ? 'All' : f === 'female' ? 'Female' : 'Male'}
                </button>
              ))}
            </div>
            {!catalog && <p className="mt-3 text-sm text-slate-600" role="status">Loading characters…</p>}
            <ul className="mt-3 grid grid-cols-3 gap-2">
              {avatars.map((a) => (
                <li key={a.id}>
                  <button type="button" aria-pressed={draft.avatar_id === a.id} data-testid={`onboarding-avatar-${a.id}`}
                    onClick={() => catalog && setDraft(applyAvatar(draft, a, catalog))}
                    className={`flex w-full flex-col items-center rounded-xl border p-2 outline-none focus-visible:ring-4 focus-visible:ring-blue-300 ${draft.avatar_id === a.id ? 'border-blue-700 ring-2 ring-blue-200' : 'border-slate-200'}`}>
                    <Avatar id={a.id} color={a.color} size={72} label={`${a.name}, ${a.gender}`} />
                    <span className="text-sm font-semibold">{a.name}</span>
                    <span className="text-xs text-slate-500">{a.gender === 'female' ? 'Female voice' : 'Male voice'}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        {step === 2 && (
          <>
            <h2 id="onboarding-title" className="mt-1 text-xl font-semibold">Choose a personality</h2>
            <p className="mt-1 text-sm text-slate-600">It changes how {draft.name} talks, never what it does. You can add a custom one later.</p>
            <div role="radiogroup" aria-label="Personality" className="mt-3 space-y-2">
              {personalities.map((p) => (
                <label key={p.id} className={`block cursor-pointer rounded-xl border p-3 ${draft.personality === p.id ? 'border-blue-700 ring-2 ring-blue-200' : 'border-slate-200'}`}>
                  <input type="radio" name="onboarding-personality" className="sr-only" checked={draft.personality === p.id}
                    onChange={() => setDraft({ ...draft, personality: p.id as PersonalityId })} data-testid={`onboarding-personality-${p.id}`} />
                  <span className="font-semibold">{p.label}</span>
                  <span className="block text-sm text-slate-600">{p.tone}</span>
                </label>
              ))}
            </div>
          </>
        )}
        {step === 3 && (
          <>
            <h2 id="onboarding-title" className="mt-1 text-xl font-semibold">Questions and privacy</h2>
            <p className="mt-2 text-sm text-slate-600">
              Commands for this PC (open an app, move a file) are understood on this PC first. To answer a general question or look
              something up on the web, the question, and for web answers some text from the pages read, must be sent to the cloud AI (Groq).
              Do you allow that?
            </p>
            <div role="radiogroup" aria-label="Cloud answers" className="mt-3 space-y-2">
              {([[true, 'Yes, answer questions with the cloud AI'], [false, 'No, keep everything on this PC (questions will not be answered)']] as const).map(([value, label]) => (
                <label key={String(value)} className={`block cursor-pointer rounded-xl border p-3 text-sm ${cloud === value ? 'border-blue-700 ring-2 ring-blue-200' : 'border-slate-200'}`}>
                  <input type="radio" name="cloud-answers" className="sr-only" checked={cloud === value} onChange={() => setCloud(value)}
                    data-testid={value ? 'onboarding-cloud-yes' : 'onboarding-cloud-no'} />
                  {label}
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-slate-500">You can change this any time in Settings. If you skip it, questions stay on this PC.</p>
          </>
        )}
        {step === 4 && (
          <>
            <h2 id="onboarding-title" className="mt-1 text-xl font-semibold">Microphone</h2>
            <MicTest supported={micSupported} name={draft.user_name} />
          </>
        )}
        {step === 5 && (
          <>
            <h2 id="onboarding-title" className="mt-1 text-xl font-semibold">Try one command</h2>
            <p className="mt-2 text-sm text-slate-600">
              Press <kbd className="rounded border px-1">Ctrl</kbd>+<kbd className="rounded border px-1">Space</kbd> or the <strong>Start</strong> button, then say:
            </p>
            <p className="mt-2 rounded-lg bg-indigo-50 p-3 font-medium text-indigo-900" data-testid="onboarding-example">“{EXAMPLE[draft.language]}”</p>
            <p className="mt-2 text-sm text-slate-600">A card shows what will happen. Press <strong>Approve</strong> and watch it get checked.</p>
          </>
        )}
        <div className="mt-6 flex justify-between gap-2">
          <button type="button" onClick={() => finish(false)} className="rounded px-3 py-2 text-sm text-slate-600 underline">
            Skip
          </button>
          <div className="flex gap-2">
            {step > 0 && (
              <button type="button" onClick={() => setStep(step - 1)} className="rounded-lg px-3 py-2 text-sm ring-1 ring-slate-300">Back</button>
            )}
            {step < STEPS - 1 ? (
              <button type="button" data-autofocus onClick={() => setStep(step + 1)} className="rounded-lg bg-blue-700 px-4 py-2 font-semibold text-white">
                Next
              </button>
            ) : (
              <button type="button" data-autofocus onClick={() => finish(micSupported)} className="rounded-lg bg-emerald-700 px-4 py-2 font-semibold text-white">
                {micSupported ? 'Start' : 'Done'}
              </button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
