import type { Catalog, CatalogAvatar, Persona, PersonalityId, VoiceOptions } from './api';

// Mirrors backend/app/voice/personas.py `resolve_style`; a test keeps the two in step.
export function resolveStyle(personality: PersonalityId, formality = 50, humour = 50): Exclude<PersonalityId, 'custom'> {
  if (personality === 'custom') {
    if (humour >= 60 && formality < 60) return 'friend';
    return formality >= 60 ? 'assistant' : 'mentor';
  }
  return personality;
}

const DIRECTION: Record<Exclude<PersonalityId, 'custom'>, string> = {
  assistant: 'professionally',
  mentor: 'warm',
  friend: 'cheerful',
  coach: 'confidently',
};

export function directionFor(persona: Pick<Persona, 'personality' | 'formality' | 'humour'>): string {
  return DIRECTION[resolveStyle(persona.personality, persona.formality, persona.humour)];
}

/** The persona's voice settings in the shape the speech player sends to /voice/tts. */
export function voiceOptions(persona: Persona, language: string): VoiceOptions {
  return {
    language,
    gender: persona.gender === 'male' ? 'male' : 'female',
    voice_en: persona.voice_en,
    voice_ur: persona.voice_ur,
    voice_hi: persona.voice_hi,
    direction: directionFor(persona),
  };
}

/** Picking an avatar sets its gender and default voices; the display name follows unless the user renamed it. */
export function applyAvatar(persona: Persona, avatar: CatalogAvatar, catalog: Catalog): Persona {
  const defaultNames = new Set(['Agent X', ...catalog.avatars.map((a) => a.name)]);
  return {
    ...persona,
    avatar_id: avatar.id,
    gender: avatar.gender,
    voice_en: avatar.voice_en,
    voice_ur: avatar.voice_ur,
    voice_hi: avatar.voice_hi,
    name: defaultNames.has(persona.name) ? avatar.name : persona.name,
  };
}

export function accentFor(persona: Persona, catalog: Catalog | null): string {
  return catalog?.avatars.find((a) => a.id === persona.avatar_id)?.color ?? '#38E1FF';
}

export function englishVoicesFor(gender: Persona['gender'], catalog: Catalog): string[] {
  if (gender === 'male') return catalog.voices.english_male;
  if (gender === 'female') return catalog.voices.english_female;
  return [...catalog.voices.english_female, ...catalog.voices.english_male];
}

export function sampleLine(personality: PersonalityId, name: string, assistant: string): string {
  const who = name ? `, ${name}` : '';
  switch (resolveStyle(personality)) {
    case 'mentor': return `Hello${who}. I'm here to help, and I'll explain things as we go.`;
    case 'friend': return `Hey${who}! Ready when you are.`;
    case 'coach': return `Ready${who}. What's the goal today?`;
    default: return `Hello${who}. I'm ${assistant}. How can I help?`;
  }
}

const CUSTOM_KEY = 'agentdesk_custom_avatar';
export function loadCustomImage(): string | null {
  try { return localStorage.getItem(CUSTOM_KEY); } catch { return null; }
}
export function saveCustomImage(dataUrl: string | null): void {
  try {
    if (dataUrl) localStorage.setItem(CUSTOM_KEY, dataUrl);
    else localStorage.removeItem(CUSTOM_KEY);
  } catch { /* the picture is optional; the avatar then shows without it */ }
}
