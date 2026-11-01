/**
 * Typed fetch client for the AgentDesk AI FastAPI backend.
 * Base URL points at the local backend; no secrets are ever stored here.
 */

export type TaskStatus =
  | 'created'
  | 'planned'
  | 'awaiting_approval'
  | 'awaiting_input'
  | 'approved'
  | 'executing'
  | 'completed'
  | 'partially_completed'
  | 'waiting_retry'
  | 'paused'
  | 'failed'
  | 'cancelled'
  | 'rejected';

export type ActionStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled'
  | 'paused';

export type RiskLevel = 'low' | 'medium' | 'high';

export interface ActionOut {
  id: string;
  seq: number;
  tool_name: string;
  args: Record<string, unknown>;
  risk: RiskLevel;
  status: ActionStatus;
  result?: unknown;
  verification?: unknown;
  error?: string | null;
  rollback_status?: 'available' | 'unsupported' | 'done' | 'failed' | null;
  rollback_detail?: string | null;
}

export interface TaskOut {
  id: string;
  command: string;
  status: TaskStatus;
  plan_note?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  retry_at?: number | null;
  provider_note?: string | null;
  partial?: {
    succeeded: number;
    not_run: number;
    failed: number;
    changes_remain: string[];
    rollback_available: string[];
  } | null;
  actions: ActionOut[];
}

export interface ConfigStatus {
  ai_provider: string;
  ai_model: string;
  ai_configured: boolean;
  mock_mode: boolean;
  groq_configured?: boolean;
  speech_ready?: boolean;
  search?: { wikipedia: boolean; tavily: boolean };
  agent_max_steps?: number;
  fallback_provider?: string;
  fallback_configured?: boolean;
  local_commands?: boolean;
  cloud_mode?: string;
  browser_connected?: boolean;
  browser_profiles?: number;
  allowed_directories: string[];
  approved_applications: string[];
  platform: string;
  plan?: string;
}

export interface UsageOut {
  plan: string;
  limits: Record<string, number>;
  used: Record<string, number>;
  remaining: Record<string, number>;
  resets_at_utc: string;
}

export type ListenMode = 'push_to_talk' | 'hands_free' | 'off';
export type OrbColor = 'indigo' | 'emerald' | 'rose' | 'amber' | 'slate';

export interface Persona {
  name: string;
  language: 'ur-PK' | 'en-US' | 'en-GB' | 'hi-IN';
  style: 'friendly' | 'brief' | 'formal';
  speak_replies: boolean;
  orb_color: OrbColor;
  user_name: string;
  avatar_id: AvatarId;
  show_avatar: boolean;
  gender: 'female' | 'male' | 'neutral';
  voice_en: string;
  voice_ur: string;
  voice_hi: string;
  personality: PersonalityId;
  custom_description: string;
  formality: number;
  humour: number;
  verbosity: number;
  wake_word: string;
}

export type AvatarId = 'nova' | 'aria' | 'zara' | 'orion' | 'atlas' | 'kai' | 'custom';
export type PersonalityId = 'assistant' | 'mentor' | 'friend' | 'coach' | 'custom';

export interface CatalogAvatar {
  id: Exclude<AvatarId, 'custom'>;
  name: string;
  gender: 'female' | 'male';
  style: string;
  color: string;
  voice_en: string;
  voice_ur: string;
  voice_hi: string;
}

export interface CatalogPersonality {
  id: PersonalityId;
  label: string;
  tone: string;
  style: string;
  direction: string;
}

export interface Catalog {
  avatars: CatalogAvatar[];
  personalities: CatalogPersonality[];
  voices: { english_female: string[]; english_male: string[]; urdu: string[]; hindi: string[] };
}

export interface Preferences {
  persona: Persona;
  listen_mode: ListenMode;
  provider_mode: 'auto' | 'local_only';
  risk_policy: 'standard' | 'strict';
  memory_enabled: boolean;
  onboarded: boolean;
  follow_up_seconds: number;
  speech_volume: number;
  daily_greeting: boolean;
  cloud_answers: boolean | null;
  conversation_cutoff?: string | null;
}

export type PreferencesUpdate = Partial<Omit<Preferences, 'conversation_cutoff'>>;

export interface MemoryItem {
  id: string;
  kind: 'fact' | 'preference' | 'alias';
  text: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryOut {
  enabled: boolean;
  items: MemoryItem[];
  recent_turns: { task_id: string; command: string; status: TaskStatus; created_at: string }[];
  conversation_cutoff?: string | null;
}

export interface HealthOut {
  status: string;
  version: string;
}

// Use the page's own origin. Vite proxies API paths during development;
// the normal launcher serves both the UI and API from the backend.
const BASE_URL = '';

// The launcher opens the UI as /#token=...; the fragment is never sent to the
// server. Keep the token for this tab only and remove it from the address bar.
const TOKEN_KEY = 'agentdesk_api_token';
export function apiToken(): string {
  try {
    const match = /(?:^#|&)token=([^&]+)/.exec(window.location.hash);
    if (match) {
      sessionStorage.setItem(TOKEN_KEY, decodeURIComponent(match[1]));
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
    return sessionStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export const hasApiToken = (): boolean => apiToken() !== '';

/** The backend refused the request (401): this tab has no token, or an old one. */
export class AuthError extends Error {}
export const authMessage = (): string => hasApiToken()
  ? 'Access token purana ya galat hai. START_AGENT_X.cmd se app dobara kholein.'
  : 'Is tab mein access token nahi hai (naya tab ya refresh se token chala jata hai). START_AGENT_X.cmd se app kholein.';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(apiToken() ? { Authorization: `Bearer ${apiToken()}` } : {}),
        ...(options.headers ?? {}),
      },
    });
  } catch {
    throw new Error(
      'Backend se connection nahi ho raha. START_AGENT_X.cmd se project kholein; connection wapas aane par screen khud reconnect karegi.',
    );
  }
  if (res.status === 401) {
    throw new AuthError(authMessage());
  }
  if (!res.ok) {
    let detail = '';
    try {
      const body = (await res.json()) as { detail?: unknown };
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body);
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`Request failed (${res.status}): ${detail || res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  usage: () => request<UsageOut>('/usage'),

  preferences: () => request<Preferences>('/preferences'),
  savePreferences: (patch: PreferencesUpdate) =>
    request<Preferences>('/preferences', { method: 'PUT', body: JSON.stringify(patch) }),

  memory: () => request<MemoryOut>('/memory'),
  addMemory: (kind: MemoryItem['kind'], text: string) =>
    request<MemoryItem>('/memory', { method: 'POST', body: JSON.stringify({ kind, text }) }),
  editMemory: (id: string, kind: MemoryItem['kind'], text: string) =>
    request<MemoryItem>(`/memory/${id}`, { method: 'PUT', body: JSON.stringify({ kind, text }) }),
  deleteMemory: (id: string) => request<{ deleted: string }>(`/memory/${id}`, { method: 'DELETE' }),
  deleteAllMemory: () => request<{ deleted: number }>('/memory', { method: 'DELETE' }),
  forgetConversation: () => request<Preferences>('/memory/forget-conversation', { method: 'POST' }),

  pause: (id: string) => request<TaskOut>(`/tasks/${id}/pause`, { method: 'POST' }),

  rollback: (id: string) => request<TaskOut>(`/tasks/${id}/rollback`, { method: 'POST' }),

  replyTask: (id: string, message: string) => request<TaskOut>(`/tasks/${id}/reply`, { method: 'POST', body: JSON.stringify({ message }) }),

  health: () => request<HealthOut>('/health'),
  config: () => request<ConfigStatus>('/config/status'),

  createTask: (command: string, idempotency_key?: string) =>
    request<TaskOut>('/tasks', {
      method: 'POST',
      body: JSON.stringify({
        command,
        ...(idempotency_key ? { idempotency_key } : {}),
      }),
    }),

  listTasks: (limit = 20) => request<TaskOut[]>(`/tasks?limit=${limit}`),

  getTask: (id: string) => request<TaskOut>(`/tasks/${id}`),

  approve: (id: string, action_ids?: string[]) =>
    request<TaskOut>(`/tasks/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ action_ids }),
    }),

  reject: (id: string, action_ids?: string[]) =>
    request<TaskOut>(`/tasks/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ action_ids }),
    }),

  run: (id: string) => request<TaskOut>(`/tasks/${id}/run`, { method: 'POST' }),

  cancel: (id: string) => request<TaskOut>(`/tasks/${id}/cancel`, { method: 'POST' }),
};

// ---------------------------------------------------------------- M2 voice endpoints
export interface VoiceOptions {
  language: string;
  gender: 'female' | 'male';
  voice_en: string;
  voice_ur?: string | null;
  voice_hi?: string | null;
  direction?: string | null;
}

async function voiceFetch(path: string, init: RequestInit): Promise<Response> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...(apiToken() ? { Authorization: `Bearer ${apiToken()}` } : {}), ...(init.headers ?? {}) },
  });
  if (res.status === 401) throw new AuthError(authMessage());
  if (!res.ok) throw new Error(`Voice request failed (${res.status})`);
  return res;
}

const jsonInit = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const voiceApi = {
  chunks: async (text: string, direction?: string | null) =>
    ((await (await voiceFetch('/voice/chunks', jsonInit({ text, direction }))).json()) as { chunks: string[] }).chunks,
  tts: async (text: string, options: VoiceOptions) => {
    const res = await voiceFetch('/voice/tts', jsonInit({ text, ...options }));
    return { blob: await res.blob(), engine: res.headers.get('X-TTS-Engine') ?? '' };
  },
  /** The phrase to speak. A task yields its closing line ("Done" only if verified); a tool yields the acknowledgement. */
  greeting: async () => (await (await voiceFetch('/voice/greeting', { method: 'GET' })).json()) as { enabled: boolean; text: string },
  catalog: async () => (await (await voiceFetch('/voice/catalog', { method: 'GET' })).json()) as Catalog,
  line: async (body: { task?: TaskOut; tool?: string; args?: Record<string, unknown>; event?: string; language: string; personality?: string; user_name?: string; formality?: number; humour?: number }) =>
    ((await (await voiceFetch('/voice/line', jsonInit(body))).json()) as { text: string }).text,
  stt: async (wav: Uint8Array, language?: string, prompt?: string) => {
    const form = new FormData();
    form.append('audio', new Blob([wav as BlobPart], { type: 'audio/wav' }), 'speech.wav');
    if (language) form.append('language', language);
    if (prompt) form.append('prompt', prompt);
    return ((await (await voiceFetch('/voice/stt', { method: 'POST', body: form })).json()) as { text: string }).text;
  },
  latency: (kind: 'local' | 'planned' | 'chat', ms: Record<string, number>) =>
    voiceFetch('/voice/latency', jsonInit({ kind, ms })).catch(() => undefined),
};
