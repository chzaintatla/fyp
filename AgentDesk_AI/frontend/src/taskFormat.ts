import type { RiskLevel } from './api';

/** Task statuses that are still in flight; the active task is polled while in one of these. */
export const TRANSITIONAL = new Set([
  'created',
  'planned',
  'awaiting_approval',
  'approved',
  'executing',
  'waiting_retry',
]);

export const POLL_INTERVAL_MS = 2000;

export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function prettyStatus(status: string): string {
  if (status === 'awaiting_input') return 'Aapke jawab ka intezar';
  return status.replace(/_/g, ' ');
}

export function riskClasses(risk: RiskLevel): string {
  switch (risk) {
    case 'low':
      return 'bg-emerald-100 text-emerald-800 border-emerald-300';
    case 'medium':
      return 'bg-amber-100 text-amber-800 border-amber-300';
    case 'high':
      return 'bg-red-100 text-red-800 border-red-300';
  }
}

export function statusClasses(status: string): string {
  if (status === 'succeeded' || status === 'completed' || status === 'approved')
    return 'bg-emerald-100 text-emerald-800 border-emerald-300';
  if (status === 'failed' || status === 'rejected')
    return 'bg-red-100 text-red-800 border-red-300';
  if (status === 'running' || status === 'executing')
    return 'bg-blue-100 text-blue-800 border-blue-300';
  if (status === 'pending' || status === 'awaiting_approval' || status === 'planned' || status === 'partially_completed' || status === 'paused')
    return 'bg-amber-100 text-amber-800 border-amber-300';
  return 'bg-slate-100 text-slate-700 border-slate-300';
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
