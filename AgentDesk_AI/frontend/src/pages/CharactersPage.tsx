import { useEffect, useMemo, useRef, useState } from 'react';
import { type Catalog, type CatalogAvatar, type Persona, type PersonalityId, type PreferencesUpdate } from '../api';
import { Avatar } from '../components/Avatar';
import { SpeechPlayer } from '../speech/player';
import {
  applyAvatar, englishVoicesFor, loadCustomImage, saveCustomImage, sampleLine, voiceOptions,
} from '../characters';

interface Props {
  persona: Persona;
  catalog: Catalog | null;
  language: string;
  speechReady: boolean;
  onSave: (patch: PreferencesUpdate) => Promise<void>;
}

type Filter = 'all' | 'female' | 'male';

/** Pick the character (look + voice), its personality, and the names it uses. Changes the manner, never the actions. */
export function CharactersPage({ persona, catalog, language, speechReady, onSave }: Props) {
  const [filter, setFilter] = useState<Filter>('all');
  const [draft, setDraft] = useState<Persona>(persona);
  const [image, setImage] = useState<string | null>(() => loadCustomImage());
  const [speaking, setSpeaking] = useState(false);
  const [level, setLevel] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const playerRef = useRef<SpeechPlayer | null>(null);
  if (playerRef.current === null) playerRef.current = new SpeechPlayer(() => voiceOptions(draftRef.current, language));

  useEffect(() => setDraft(persona), [persona]);
  useEffect(() => {
    if (!speaking) return;
    let frame = 0;
    const tick = () => {
      setLevel(playerRef.current?.level() ?? 0);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [speaking]);
  useEffect(() => () => playerRef.current?.stop(), []);

  const save = async (next: Persona) => {
    setDraft(next);
    try {
      await onSave({ persona: next });
      setNote('Saved.');
    } catch {
      setNote('Could not save. Check the message at the top.');
    }
  };

  const play = async (text: string, as: Persona) => {
    if (!speechReady) {
      setNote('Voice samples need the speech service (add a Groq key or the cloud brain). The browser voice is used instead.');
    }
    draftRef.current = as;
    setSpeaking(true);
    try {
      await playerRef.current?.speak(text);
    } finally {
      setSpeaking(false);
      setLevel(0);
    }
  };

  const avatars = useMemo(
    () => (catalog?.avatars ?? []).filter((a) => filter === 'all' || a.gender === filter),
    [catalog, filter],
  );
  if (!catalog) return <p className="text-sm text-slate-600" role="status">Loading characters…</p>;

  const accent = catalog.avatars.find((a) => a.id === draft.avatar_id)?.color ?? '#38E1FF';
  const choose = (a: CatalogAvatar) => void save(applyAvatar(draft, a, catalog));
  const upload = (file: File | undefined) => {
    if (!file || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 256;
        const s = Math.min(img.width, img.height);
        canvas.getContext('2d')?.drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, 256, 256);
        const url = canvas.toDataURL('image/jpeg', 0.85);
        saveCustomImage(url);
        setImage(url);
        void save({ ...draft, avatar_id: 'custom' });
      };
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  };

  const english = englishVoicesFor(draft.gender, catalog);
  return (
    <div className="space-y-6" data-testid="characters-page">
      <section aria-labelledby="char-preview" className="flex flex-col items-center gap-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:flex-row">
        <Avatar id={draft.avatar_id} color={accent} mood={speaking ? 'speaking' : 'idle'} level={level} size={140}
          image={draft.avatar_id === 'custom' ? image : null} label={`${draft.name}, preview`} />
        <div className="flex-1">
          <h2 id="char-preview" className="text-lg font-semibold">{draft.name}</h2>
          <p className="text-sm text-slate-600">
            {catalog.personalities.find((p) => p.id === draft.personality)?.label} · {draft.gender} voice · {draft.voice_en}
          </p>
          <button type="button" className="mt-3 rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white"
            onClick={() => void play(sampleLine(draft.personality, draft.user_name, draft.name), draft)} disabled={speaking}>
            {speaking ? 'Speaking…' : 'Hear a sample'}
          </button>
          {speaking && <button type="button" className="ml-2 rounded-lg px-3 py-2 text-sm underline" onClick={() => playerRef.current?.stop()}>Stop</button>}
          <p className="mt-2 text-xs text-slate-500" role="status" aria-live="polite">{note}</p>
        </div>
      </section>

      <section aria-labelledby="char-avatars" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="char-avatars" className="text-base font-semibold">Character</h2>
          <div role="group" aria-label="Filter characters" className="flex gap-1">
            {(['all', 'female', 'male'] as const).map((f) => (
              <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
                className={`rounded-md px-3 py-1 text-sm ring-1 ring-inset ${filter === f ? 'bg-blue-700 text-white ring-blue-700' : 'bg-white ring-slate-300'}`}>
                {f === 'all' ? 'All' : f === 'female' ? 'Female' : 'Male'}
              </button>
            ))}
          </div>
        </div>
        <ul className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3" data-testid="avatar-grid">
          {avatars.map((a) => (
            <li key={a.id}>
              <div className={`rounded-xl border p-3 text-center ${draft.avatar_id === a.id ? 'border-blue-700 ring-2 ring-blue-200' : 'border-slate-200'}`}>
                <button type="button" aria-pressed={draft.avatar_id === a.id} onClick={() => choose(a)}
                  data-testid={`avatar-${a.id}`} className="flex w-full flex-col items-center rounded-lg outline-none focus-visible:ring-4 focus-visible:ring-blue-300">
                  <Avatar id={a.id} color={a.color} size={96} label={`${a.name}, ${a.gender}, ${a.style}`} />
                  <span className="mt-1 font-semibold">{a.name}</span>
                  <span className="text-xs text-slate-500">{a.gender === 'female' ? 'Female' : 'Male'} · {a.style}</span>
                </button>
                <button type="button" className="mt-2 rounded px-2 py-1 text-xs underline"
                  onClick={() => void play(`Hello, I'm ${a.name}.`, applyAvatar(draft, a, catalog))}>
                  Play sample voice
                </button>
              </div>
            </li>
          ))}
          {filter === 'all' && (
            <li>
              <div className={`flex h-full flex-col items-center rounded-xl border p-3 text-center ${draft.avatar_id === 'custom' ? 'border-blue-700 ring-2 ring-blue-200' : 'border-slate-200'}`}>
                <Avatar id="custom" color="#9AA8C7" size={96} image={image} label="Your own picture" />
                <span className="mt-1 font-semibold">Your own</span>
                <label className="mt-1 cursor-pointer text-xs underline">
                  Upload a picture
                  <input type="file" accept="image/*" className="sr-only" data-testid="custom-image" onChange={(e) => upload(e.target.files?.[0])} />
                </label>
                <span className="mt-1 text-xs text-slate-500">Only a picture you own. It stays in this browser.</span>
                {image && draft.avatar_id !== 'custom' && (
                  <button type="button" className="mt-1 text-xs underline" onClick={() => void save({ ...draft, avatar_id: 'custom' })}>Use it</button>
                )}
              </div>
            </li>
          )}
        </ul>
        <label className="mt-4 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!draft.show_avatar} onChange={(e) => void save({ ...draft, show_avatar: !e.target.checked })} />
          Orb only (no face)
        </label>
      </section>

      <section aria-labelledby="char-personality" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 id="char-personality" className="text-base font-semibold">Personality</h2>
        <p className="mt-1 text-sm text-slate-600">Changes how it talks. It never changes what it does: approvals and checks stay exactly the same.</p>
        <div role="radiogroup" aria-labelledby="char-personality" className="mt-3 grid gap-3 md:grid-cols-2">
          {catalog.personalities.map((p) => (
            <label key={p.id} className={`cursor-pointer rounded-xl border p-3 ${draft.personality === p.id ? 'border-blue-700 ring-2 ring-blue-200' : 'border-slate-200'}`}>
              <input type="radio" name="personality" className="sr-only" checked={draft.personality === p.id}
                onChange={() => void save({ ...draft, personality: p.id as PersonalityId })} data-testid={`personality-${p.id}`} />
              <span className="font-semibold">{p.label}</span>
              <span className="block text-sm text-slate-600">{p.tone}</span>
              <span className="block text-xs text-slate-500">{p.style}</span>
            </label>
          ))}
        </div>
        {draft.personality === 'custom' && (
          <div className="mt-4 space-y-3" data-testid="custom-personality">
            <label className="block text-sm font-medium" htmlFor="custom-desc">Describe it (max 500 characters)</label>
            <textarea id="custom-desc" maxLength={500} rows={3} value={draft.custom_description} className="w-full rounded border p-2"
              onChange={(e) => setDraft({ ...draft, custom_description: e.target.value })}
              onBlur={() => void save(draft)} />
            {(['formality', 'humour', 'verbosity'] as const).map((k) => (
              <label key={k} className="flex items-center gap-3 text-sm">
                <span className="w-20 capitalize">{k}</span>
                <input type="range" min={0} max={100} value={draft[k]} aria-label={k}
                  onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })} onMouseUp={() => void save(draftRef.current)}
                  onKeyUp={() => void save(draftRef.current)} />
                <span className="w-8 text-right">{draft[k]}</span>
              </label>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="char-voice" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 id="char-voice" className="text-base font-semibold">Voice and names</h2>
        <div className="mt-3 grid gap-4 md:grid-cols-3">
          <label className="text-sm">English voice
            <select className="mt-1 w-full rounded border p-2" value={draft.voice_en} onChange={(e) => void save({ ...draft, voice_en: e.target.value })}>
              {english.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label className="text-sm">Urdu voice
            <select className="mt-1 w-full rounded border p-2" value={draft.voice_ur} onChange={(e) => void save({ ...draft, voice_ur: e.target.value })}>
              {catalog.voices.urdu.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label className="text-sm">Hindi voice
            <select className="mt-1 w-full rounded border p-2" value={draft.voice_hi} onChange={(e) => void save({ ...draft, voice_hi: e.target.value })}>
              {catalog.voices.hindi.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        </div>
        {draft.avatar_id === 'custom' && (
          <label className="mt-3 block text-sm">Voice type for your own character
            <select className="mt-1 block rounded border p-2" value={draft.gender} onChange={(e) => void save({ ...draft, gender: e.target.value as Persona['gender'] })}>
              <option value="female">Female</option><option value="male">Male</option><option value="neutral">Neutral (all voices)</option>
            </select>
          </label>
        )}
        <button type="button" className="mt-3 rounded-lg px-3 py-2 text-sm underline"
          onClick={() => void play(sampleLine(draft.personality, draft.user_name, draft.name), draft)}>
          Play sample in the selected language
        </button>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          {([['name', 'Assistant name', 40], ['user_name', 'Your name', 40], ['wake_word', 'Wake phrase (saved; always-listening wake word is not built yet)', 30]] as const).map(([k, label, max]) => (
            <label key={k} className="text-sm">{label}
              <input className="mt-1 w-full rounded border p-2" maxLength={max} value={draft[k]}
                onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                onBlur={() => { if (k !== 'name' || draft.name.trim()) void save(draft); }} />
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}
