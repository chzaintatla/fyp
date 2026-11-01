import { api, type TaskOut } from '../api';
import { errMsg, prettyStatus, statusClasses } from '../taskFormat';
import { ActionCard } from './ActionCard';
import { Spinner } from './Spinner';

interface Props {
  task: TaskOut | null;
  busy: boolean;
  busyAction: string | null;
  transitional: boolean;
  mutate: (label: string, fn: () => Promise<TaskOut>) => Promise<void>;
  approveThenRun: (id: string, actionIds?: string[]) => Promise<TaskOut>;
  pauseRun: () => Promise<void>;
  undoChanges: () => void;
  refreshTask: (id: string) => Promise<void>;
  setBanner: (message: string | null) => void;
}

const SECONDARY = 'rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:opacity-50';
const DANGER = 'rounded-lg bg-white px-4 py-2 text-sm font-semibold text-red-700 ring-1 ring-inset ring-red-300 hover:bg-red-50 disabled:opacity-50';

export function TaskPanel({ task, busy, busyAction, transitional, mutate, approveThenRun, pauseRun, undoChanges, refreshTask, setBanner }: Props) {
  if (!task) {
    return (
      <section className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center shadow-sm">
        <p className="text-sm text-slate-500">
          No task yet. Type a command above and press <strong>Run command</strong> to see the
          AI-generated plan here.
        </p>
      </section>
    );
  }
  const doneCount = task.actions.filter((a) => a.status === 'succeeded').length;
  const totalCount = task.actions.length;
  return (
    <section aria-live="polite" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900">Current task</h2>
          <p className="mt-1 break-words text-sm text-slate-700">
            <span className="font-medium">Command:</span> “{task.command}”
          </p>
          <p className="mt-1 font-mono text-xs text-slate-400">id: {task.id}</p>
        </div>
        <span
          data-testid="task-status"
          data-status={task.status}
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${statusClasses(task.status)}`}
        >
          {(task.status === 'executing' || task.status === 'awaiting_approval') && <Spinner />}
          {prettyStatus(task.status)}
        </span>
      </div>

      {task.plan_note && (
        <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
          <span className="font-medium text-slate-700">Plan: </span>
          {task.plan_note}
        </p>
      )}

      {task.provider_note && <p className="mt-3 rounded bg-blue-50 p-3 text-sm">{task.provider_note}</p>}
      {task.status === 'executing' && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg bg-blue-50 p-3 text-sm text-blue-900" role="status">
          <Spinner label="Running…" />
          <button type="button" onClick={() => void pauseRun()} className="rounded-md bg-white px-3 py-1.5 font-medium ring-1 ring-inset ring-blue-300 hover:bg-blue-100">
            Pause
          </button>
        </div>
      )}
      {task.status === 'paused' && (
        <div className="mt-3 rounded-lg bg-amber-50 p-4 text-sm" role="status">
          <p>
            Task paused. The remaining approved actions have not run.
            {task.actions.some((a) => a.status === 'paused') && ' A download was stopped part-way; Resume continues it from where it stopped.'}
          </p>
          <button type="button" disabled={busy} onClick={() => void mutate('resume', () => api.run(task.id))} className="mt-2 rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-50">Resume task</button>
        </div>
      )}
      {task.partial && (
        <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="status" data-testid="partial-banner">
          <p className="font-semibold">Only part of this task was completed</p>
          <p>
            {task.partial.succeeded} succeeded, {task.partial.failed} failed, {task.partial.not_run} did not run.
            {task.partial.changes_remain.length > 0 && ` Changes already made: ${task.partial.changes_remain.join(', ')}.`}
          </p>
        </div>
      )}
      {task.status === 'waiting_retry' && (
        <div className="mt-3 rounded-lg bg-amber-50 p-4 text-sm" role="status">
          <p>AI limit par task pause hai. Volume, brightness aur simple app commands abhi bhi use kar sakti hain.</p>
          {task.retry_at && <p className="mt-1">Resume after: {new Date(task.retry_at * 1000).toLocaleString()}</p>}
          <button disabled={busy || Date.now() < (task.retry_at ?? 0) * 1000} onClick={() => void mutate('resume', () => api.run(task.id))} className="mt-2 rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-50">Resume task</button>
        </div>
      )}
      {task.error && (
        <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {task.error}
        </p>
      )}

      {totalCount > 0 && (
        <p className="mt-3 text-sm text-slate-600">
          Progress: {doneCount} of {totalCount} actions succeeded
        </p>
      )}

      <h3 className="mt-4 text-sm font-semibold text-slate-900">Planned actions</h3>
      {task.actions.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">
          Reply above with the missing details to continue.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-3">
          {task.actions
            .slice()
            .sort((a, b) => a.seq - b.seq)
            .map((a) => (
              <ActionCard
                key={a.id}
                action={a}
                taskStatus={task.status}
                busy={busy}
                onApprove={(actionId) => void mutate(`approve-${actionId}`, () => approveThenRun(task.id, [actionId]))}
                onReject={(actionId) => void mutate(`reject-${actionId}`, () => api.reject(task.id, [actionId]))}
              />
            ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {task.status === 'awaiting_approval' && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => void mutate('approve-all', () => approveThenRun(task.id))}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busyAction === 'approve-all' ? <Spinner label="Approving…" /> : task.actions.some((a) => a.status === 'pending' && a.risk === 'high') ? 'Approve all except high-risk' : 'Approve all'}
            </button>
            <button type="button" disabled={busy} onClick={() => void mutate('reject-all', () => api.reject(task.id))} className={DANGER}>
              Reject
            </button>
          </>
        )}
        {task.status === 'approved' && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void mutate('run', () => api.run(task.id))}
            className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
          >
            {busyAction === 'run' ? <Spinner label="Starting…" /> : 'Execute'}
          </button>
        )}
        {!['executing', 'awaiting_approval', 'approved', 'paused'].includes(task.status) &&
          task.actions.some((a) => a.rollback_status === 'available' || a.rollback_status === 'failed') && (
            <button type="button" disabled={busy} onClick={undoChanges} className={DANGER}>
              {busyAction === 'rollback' ? <Spinner label="Undoing…" /> : 'Undo changes'}
            </button>
          )}
        {transitional && (
          <button type="button" disabled={busy} onClick={() => void mutate('cancel', () => api.cancel(task.id))} className={SECONDARY}>
            Cancel task
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setBanner(null);
            void refreshTask(task.id).catch((e) => setBanner(errMsg(e)));
          }}
          className={SECONDARY}
        >
          Refresh
        </button>
      </div>
    </section>
  );
}
