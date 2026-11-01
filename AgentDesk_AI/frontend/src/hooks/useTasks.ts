import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, type TaskOut, type UsageOut } from '../api';
import { errMsg, POLL_INTERVAL_MS, TRANSITIONAL } from '../taskFormat';

interface Params {
  setBanner: (message: string | null) => void;
  /** Called when creating the task failed, so a voice command that was waiting for it can move on. */
  onCommandFailed: () => void;
  /** Called with a new command the moment it is sent, before the task exists: the acknowledgement is spoken while the task runs. */
  onCommandStart?: (command: string) => void;
}

/** The active task, the history list and every action that changes a task (run, approve, reject, undo, cancel...). */
export function useTasks({ setBanner, onCommandFailed, onCommandStart }: Params) {
  const [command, setCommand] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [activeTask, setActiveTask] = useState<TaskOut | null>(null);
  const [history, setHistory] = useState<TaskOut[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [usage, setUsage] = useState<UsageOut | null>(null);
  const submittingRef = useRef(submitting);
  submittingRef.current = submitting;
  const runCommandRef = useRef<(text: string) => Promise<void>>(async () => {});
  const busy = busyAction !== null || submitting;

  const upsertHistory = useCallback((t: TaskOut) => {
    setHistory((prev) => {
      const idx = prev.findIndex((x) => x.id === t.id);
      if (idx === -1) return [t, ...prev].slice(0, 20);
      const next = prev.slice();
      next[idx] = t;
      return next;
    });
  }, []);

  const activeStatus = activeTask?.status;
  useEffect(() => {
    // Refresh local plan usage whenever a task is created or changes state.
    void api.usage().then(setUsage).catch(() => setUsage(null));
  }, [activeTask?.id, activeStatus]);

  const loadHistory = useCallback(async () => {
    try {
      const list = await api.listTasks(20);
      setHistory(list);
      setHistoryError(null);
    } catch (e) {
      setHistoryError(errMsg(e));
    }
  }, []);

  const refreshTask = useCallback(
    async (id: string) => {
      const t = await api.getTask(id);
      setActiveTask(t);
      upsertHistory(t);
    },
    [upsertHistory],
  );

  const activeId = activeTask?.id ?? null;
  const transitional = activeTask ? TRANSITIONAL.has(activeTask.status) : false;

  useEffect(() => {
    if (!activeId || !(transitional || busyAction)) return;
    const timer = window.setInterval(() => {
      void refreshTask(activeId).catch((e) => setBanner(errMsg(e)));
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeId, transitional, busyAction, refreshTask]);

  async function mutate(label: string, fn: () => Promise<TaskOut>) {
    setBusyAction(label);
    setBanner(null);
    try {
      const t = await fn();
      setActiveTask(t);
      upsertHistory(t);
    } catch (e) {
      setBanner(errMsg(e));
    } finally {
      setBusyAction(null);
    }
  }

  const pauseRun = async () => {
    if (!activeTask) return;
    try {
      setActiveTask(await api.pause(activeTask.id));
    } catch (e) {
      setBanner(errMsg(e));
    }
  };

  const undoChanges = () => {
    if (!activeTask) return;
    const n = activeTask.actions.filter((a) => a.rollback_status === 'available' || a.rollback_status === 'failed').length;
    if (!window.confirm(`Undo ${n} completed change(s)? Anything edited since the action will be left alone and reported.`)) return;
    void mutate('rollback', () => api.rollback(activeTask.id));
  };

  const approveThenRun = async (id: string, actionIds?: string[]) => {
    const approved = await api.approve(id, actionIds);
    if (approved.status === 'approved') {
      return api.run(id);
    }
    return approved;
  };

  const runCommand = async (rawCommand: string) => {
    const trimmed = rawCommand.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setBanner(null);
    try {
      const isReply = !!activeTask && ['awaiting_approval', 'awaiting_input'].includes(activeTask.status);
      if (!isReply) onCommandStart?.(trimmed);
      const t = isReply
        ? await api.replyTask(activeTask!.id, trimmed)
        : await api.createTask(trimmed, crypto.randomUUID());
      setActiveTask(t);
      upsertHistory(t);
      setCommand('');
    } catch (e) {
      setBanner(errMsg(e));
      onCommandFailed();
    } finally {
      setSubmitting(false);
    }
  };
  runCommandRef.current = runCommand;

  const submitCommand = (e: FormEvent) => {
    e.preventDefault();
    void runCommand(command);
  };

  const selectTask = (t: TaskOut) => {
    setBanner(null);
    setActiveTask(t);
    void refreshTask(t.id).catch((e) => setBanner(errMsg(e)));
  };

  return {
    command, setCommand, submitting, activeTask, history, historyError, busyAction, busy, usage, transitional,
    submittingRef, runCommandRef, loadHistory, refreshTask, mutate, pauseRun, undoChanges, approveThenRun,
    runCommand, submitCommand, selectTask,
  };
}
