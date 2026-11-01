import type { ActionOut, TaskOut } from '../api';
import { formatValue, prettyStatus, riskClasses, statusClasses } from '../taskFormat';
import { Spinner } from './Spinner';

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

export function ActionCard({ action, taskStatus, busy, onApprove, onReject }: ActionCardProps) {
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
