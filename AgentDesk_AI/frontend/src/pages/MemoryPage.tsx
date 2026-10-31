import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type MemoryItem, type MemoryOut } from '../api';

const KINDS: MemoryItem['kind'][] = ['fact', 'preference', 'alias'];

/** What the assistant remembers: view, add, edit, delete; forget the conversation. */
export function MemoryPage({ memoryEnabled, onToggle }: { memoryEnabled: boolean; onToggle: (on: boolean) => void }) {
  const [data, setData] = useState<MemoryOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [kind, setKind] = useState<MemoryItem['kind']>('fact');
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState('');

  const load = useCallback(async () => {
    try {
      setData(await api.memory());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    void act(async () => { await api.addMemory(kind, draft.trim()); setDraft(''); });
  };

  return (
    <div className="flex flex-col gap-6" data-testid="memory-page">
      <h2 className="text-lg font-semibold">Memory</h2>
      {error && <p role="alert" className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={memoryEnabled} onChange={(e) => onToggle(e.target.checked)} />
        Use memory when understanding my commands
      </label>

      <section aria-labelledby="saved-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="saved-heading" className="font-semibold">Things I remember</h3>
        <p className="mt-1 text-xs text-slate-500">Never store passwords, PINs or keys here; they are refused.</p>
        <form onSubmit={add} className="mt-3 flex flex-wrap gap-2">
          <label htmlFor="memory-kind" className="sr-only">Kind</label>
          <select id="memory-kind" value={kind} onChange={(e) => setKind(e.target.value as MemoryItem['kind'])} className="rounded border p-2 text-sm">
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <label htmlFor="memory-text" className="sr-only">New memory</label>
          <input id="memory-text" value={draft} maxLength={500} onChange={(e) => setDraft(e.target.value)}
            placeholder='e.g. "6th sem" means D:\6th sem' className="min-w-0 flex-1 rounded border p-2 text-sm" />
          <button className="rounded bg-blue-700 px-3 py-2 text-sm font-semibold text-white">Remember</button>
        </form>
        {data && data.items.length === 0 && <p className="mt-3 text-sm text-slate-500">Nothing saved yet.</p>}
        <ul className="mt-3 flex flex-col gap-2" aria-label="Saved memories">
          {data?.items.map((item) => (
            <li key={item.id} className="rounded-lg border p-3 text-sm" data-testid="memory-item">
              {editing === item.id ? (
                <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void act(async () => { await api.editMemory(item.id, item.kind, editText); setEditing(null); }); }}>
                  <label htmlFor={`edit-${item.id}`} className="sr-only">Edit memory</label>
                  <input id={`edit-${item.id}`} autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} className="min-w-0 flex-1 rounded border p-1" />
                  <button className="rounded bg-blue-700 px-2 text-white">Save</button>
                  <button type="button" onClick={() => setEditing(null)} className="rounded border px-2">Cancel</button>
                </form>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <p><span className="mr-2 rounded bg-slate-100 px-1.5 text-xs">{item.kind}</span>{item.text}</p>
                  <div className="flex shrink-0 gap-2">
                    <button type="button" onClick={() => { setEditing(item.id); setEditText(item.text); }}
                      className="text-blue-700 underline" aria-label={`Edit: ${item.text}`}>Edit</button>
                    <button type="button" onClick={() => { if (window.confirm('Delete this memory?')) void act(() => api.deleteMemory(item.id)); }}
                      className="text-red-700 underline" aria-label={`Delete: ${item.text}`}>Delete</button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="wipe-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 id="wipe-heading" className="font-semibold">Delete everything I remember</h3>
        <p className="mt-1 text-xs text-slate-500">Removes every saved note above. Task history is not touched. This cannot be undone.</p>
        <button type="button" data-testid="delete-all-memory" disabled={!data?.items.length}
          onClick={() => { if (window.confirm('Delete ALL remembered notes? This cannot be undone.')) void act(() => api.deleteAllMemory()); }}
          className="mt-2 rounded border border-red-300 px-3 py-1 text-sm text-red-700 disabled:opacity-50">Delete all memory</button>
      </section>

      <section aria-labelledby="turns-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <h3 id="turns-heading" className="font-semibold">Recent conversation</h3>
          <button type="button" onClick={() => { if (window.confirm('Forget the conversation so far? Task history stays.')) void act(() => api.forgetConversation()); }}
            className="rounded border border-red-300 px-3 py-1 text-sm text-red-700">Forget conversation</button>
        </div>
        <p className="mt-1 text-xs text-slate-500">Used to understand “close it” or “the second one”. Last 10 commands.</p>
        <ol className="mt-3 flex flex-col gap-1 text-sm" data-testid="recent-turns">
          {data?.recent_turns.length === 0 && <li className="text-slate-500">Nothing yet.</li>}
          {data?.recent_turns.map((t) => <li key={t.task_id}>“{t.command}” <span className="text-xs text-slate-500">({t.status})</span></li>)}
        </ol>
      </section>
    </div>
  );
}
