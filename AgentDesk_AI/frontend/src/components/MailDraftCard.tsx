import { useEffect, useState } from 'react';
import { mailApi, type MailDraft, type TaskOut } from '../api';

const STATE_LABEL: Record<string, string> = {
  to: 'Who is it for?', to_choose: 'Choose the person', subject: 'Say the subject', body: 'Dictating: say the message',
  review: 'Written: say "send it", "read it back", or what to change', confirm: 'Waiting for "yes, send it"',
};

/** The e-mail being written, shown live while it is dictated; nothing here can send it: only the spoken read-back and "yes, send it" do. */
export function MailDraftCard({ activeTask }: { activeTask: TaskOut | null }) {
  const [draft, setDraft] = useState<MailDraft | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => { void mailApi.draft().then((d) => { if (live) setDraft(d.id ? d : null); }).catch(() => { if (live) setDraft(null); }); };
    load();
    const timer = window.setInterval(load, 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [activeTask?.id, activeTask?.status]);

  if (!draft) return null;
  return (
    <section aria-labelledby="mail-draft-heading" data-testid="mail-draft" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <h2 id="mail-draft-heading" className="font-semibold">Email draft</h2>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700" data-testid="mail-state">{STATE_LABEL[draft.state] ?? draft.state}</span>
      </div>
      <dl className="mt-3 grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-slate-500">To</dt>
        <dd>{draft.to.length ? draft.to.map((c) => (c.name ? `${c.name} <${c.email}>` : c.email)).join(', ') : '…'}</dd>
        <dt className="text-slate-500">Subject</dt>
        <dd>{draft.subject || '…'}</dd>
        {draft.attachments.length > 0 && (<><dt className="text-slate-500">Files</dt><dd>{draft.attachments.map((a) => a.name).join(', ')}</dd></>)}
      </dl>
      <p className="mt-3 whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-3 text-sm" data-testid="mail-body">{draft.body || 'The message will appear here as you dictate it.'}</p>
      {draft.candidates.length > 0 && (
        <ol className="mt-3 list-decimal pl-5 text-sm" aria-label="People to choose from">
          {draft.candidates.map((c) => <li key={c.email}>{c.name || c.email} <span className="text-slate-500">{c.email}</span></li>)}
        </ol>
      )}
      <div className="mt-3 flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">Nothing is sent until you hear the read-back and say "yes, send it".</p>
        <button type="button" disabled={busy} data-testid="mail-cancel"
          className="rounded border border-slate-300 px-3 py-1 text-sm" onClick={() => { setBusy(true); void mailApi.cancelDraft().then(() => setDraft(null)).finally(() => setBusy(false)); }}>
          Discard draft
        </button>
      </div>
    </section>
  );
}
