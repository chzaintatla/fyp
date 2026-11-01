import { useEffect, useRef, useState } from 'react';
import { voiceApi, type ConfigStatus, type Preferences, type VoiceOptions } from '../api';
import { voiceOptions } from '../characters';
import { SpeechPlayer } from '../speech/player';
import type { SpeechRecognitionLike } from '../speech/recognitionTypes';

interface Params {
  prefs: Preferences | null;
  config: ConfigStatus | null;
  voiceLanguage: string;
}

/** The voice state and refs shared by listening (useVoiceInput) and speaking (useSpokenReplies). */
export function useVoiceCore({ prefs, config, voiceLanguage }: Params) {
  const [heard, setHeard] = useState('');
  const [speaking, setSpeaking] = useState(false);
  const [lastReply, setLastReply] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [voiceMessage, setVoiceMessage] = useState<string | null>(null);
  const [avatarLevel, setAvatarLevel] = useState(0);

  const listenModeRef = useRef<Preferences['listen_mode']>('push_to_talk');
  listenModeRef.current = prefs?.listen_mode ?? 'push_to_talk';
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const handsFreeRef = useRef(false);
  const voiceWaitingForTaskRef = useRef(false);
  const voiceTaskActiveRef = useRef(false);
  const voiceSpeakingRef = useRef(false);
  const spokenTextRef = useRef('');
  const speechFinishRef = useRef<() => void>(() => {});
  const speechEpochRef = useRef(0);
  const interruptedSpeechRef = useRef(false);
  const voiceQueueRef = useRef<string[]>([]);
  const voiceCommandRef = useRef('');
  const voiceDebounceRef = useRef<number | null>(null);
  const voiceRestartRef = useRef<number | null>(null);
  const voiceNetworkErrorsRef = useRef(0);
  const startListeningRef = useRef<() => void>(() => {});
  const drainVoiceQueueRef = useRef<() => void>(() => {});
  const speechEndedAtRef = useRef(0);
  const followUpTimerRef = useRef<number | null>(null);

  // Server speech (Whisper in, Orpheus / edge-tts / SAPI out) when the backend has a speech service; else the browser's.
  const speechReady = Boolean(config?.speech_ready);
  const speechReadyRef = useRef(false);
  speechReadyRef.current = speechReady;
  const voiceOptionsRef = useRef<VoiceOptions>({ language: voiceLanguage, gender: 'female', voice_en: 'diana' });
  voiceOptionsRef.current = prefs ? voiceOptions(prefs.persona, voiceLanguage) : { ...voiceOptionsRef.current, language: voiceLanguage };
  const playerRef = useRef<SpeechPlayer | null>(null);
  if (playerRef.current === null) playerRef.current = new SpeechPlayer(() => voiceOptionsRef.current);
  const followUpMs = (prefs?.follow_up_seconds ?? 8) * 1000; // after a spoken reply, keep listening this long without a hotkey
  if (playerRef.current) playerRef.current.volume = (prefs?.speech_volume ?? 100) / 100;

  // How the character speaks (wording only): sent with every phrase request.
  const wording = () => ({
    language: voiceLanguage,
    personality: prefs?.persona.personality ?? 'assistant',
    user_name: prefs?.persona.user_name ?? '',
    formality: prefs?.persona.formality ?? 50,
    humour: prefs?.persona.humour ?? 50,
  });

  // Optional daily greeting: once per calendar day, only when the server voice is on and the user finished onboarding.
  useEffect(() => {
    if (!prefs?.onboarded || !prefs.daily_greeting || !speechReady || prefs.persona.speak_replies === false) return;
    const today = new Date().toDateString();
    try {
      if (localStorage.getItem('agentdesk_greeted') === today) return;
      localStorage.setItem('agentdesk_greeted', today);
    } catch {
      return;
    }
    void voiceApi.greeting().then((g) => (g.enabled && g.text ? playerRef.current?.speak(g.text) : undefined)).catch(() => undefined);
  }, [prefs?.onboarded, prefs?.daily_greeting, speechReady]);

  useEffect(() => {
    if (!speaking) {
      setAvatarLevel(0);
      return;
    }
    let frame = 0;
    const tick = () => {
      setAvatarLevel(playerRef.current?.level() ?? 0);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [speaking]);

  useEffect(
    () => () => {
      handsFreeRef.current = false;
      voiceQueueRef.current = [];
      voiceTaskActiveRef.current = false;
      if (voiceDebounceRef.current !== null) window.clearTimeout(voiceDebounceRef.current);
      if (voiceRestartRef.current !== null) window.clearTimeout(voiceRestartRef.current);
      recognitionRef.current?.abort();
      playerRef.current?.stop();
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    },
    [],
  );

  return {
    heard, setHeard, speaking, setSpeaking, lastReply, setLastReply, listening, setListening,
    handsFree, setHandsFree, voiceMessage, setVoiceMessage, avatarLevel,
    listenModeRef, recognitionRef, handsFreeRef, voiceWaitingForTaskRef, voiceTaskActiveRef, voiceSpeakingRef,
    spokenTextRef, speechFinishRef, speechEpochRef, interruptedSpeechRef, voiceQueueRef, voiceCommandRef,
    voiceDebounceRef, voiceRestartRef, voiceNetworkErrorsRef, startListeningRef, drainVoiceQueueRef,
    speechEndedAtRef, followUpTimerRef, speechReady, speechReadyRef, playerRef, followUpMs, wording,
  };
}

export type VoiceCore = ReturnType<typeof useVoiceCore>;
