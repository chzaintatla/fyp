import type { TaskOut } from '../api';
import { prettyStatus, statusClasses } from '../taskFormat';

interface Props {
  history: TaskOut[];
  historyError: string | null;
  activeId: string | undefined;
  onReload: () => void;
  onSelect: (task: TaskOut) => void;
}

export function TaskHistory({ history, historyError, activeId, onReload, onSelect }: Props) {
  return (
    <aside className="flex min-w-0 flex-col gap-6">
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">Task history</h2>
          <button
            type="button"
            onClick={onReload}
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
                  onClick={() => onSelect(t)}
                  aria-current={activeId === t.id}
                  className={`w-full rounded-lg border p-3 text-left transition hover:border-blue-300 hover:bg-blue-50 ${
                    activeId === t.id
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
  );
}
