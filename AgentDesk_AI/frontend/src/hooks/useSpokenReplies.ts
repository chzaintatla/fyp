import { useEffect, useRef } from 'react';
import { voiceApi, type Preferences, type TaskOut } from '../api';
import { finishOnce, spokenReply } from '../voice';
import type { VoiceCore } from './useVoiceCore';

interface Params {
  voice: VoiceCore;
  activeTask: TaskOut | null;
  prefs: Preferences | null;
  voiceLanguage: string;
  beginFollowUp: () => void;
}

/** Speaks the closing line of a task, and a short acknowledgement / progress line while a slow one runs. */
export function useSpokenReplies({ voice: v, activeTask, prefs, voiceLanguage, beginFollowUp }: Params) {
  const spokenTaskStates = useRef(new Set<string>());
  const ackedTasks = useRef(new Set<string>());
  // The acknowledgement of the command being run (spoken in parallel with the task) and when it started.
  const earlyRef = useRef<Promise<void> | null>(null);
  const earlyAtRef = useRef(0);

  /** Speak "Opening Chrome." the moment a command is sent, while the task is still being planned and run. */
  const earlyAck = (command: string) => {
    if (!v.speechReadyRef.current || prefs?.persona.speak_replies === false || v.voiceSpeakingRef.current) return;
    const asked = performance.now();
    earlyAtRef.current = Date.now();
    earlyRef.current = (async () => {
      try {
        const text = await voiceApi.ack({ command, ...v.wording() });
        if (!text) return;
        await v.playerRef.current?.speak(text, () => {
          const base = v.speechEndedAtRef.current;
          void voiceApi.latency('local', { ack_first_word: base > 0 ? performance.now() - base : performance.now() - asked });
        });
      } catch {
        // an acknowledgement is optional
      }
    })();
  };

  useEffect(() => {
    if (!activeTask) return;
    if (!['awaiting_input', 'awaiting_approval', 'waiting_retry', 'completed', 'partially_completed', 'failed', 'rejected', 'cancelled'].includes(activeTask.status)) return;
    const stateKey = `${activeTask.id}:${activeTask.status}`;
    if (spokenTaskStates.current.has(stateKey)) return;
    spokenTaskStates.current.add(stateKey);
    const isTerminal = ['completed', 'partially_completed', 'failed', 'rejected', 'cancelled'].includes(activeTask.status);
    const finishVoiceTask = () => {
      if ((isTerminal || activeTask.status === 'awaiting_input' || activeTask.status === 'awaiting_approval' || activeTask.status === 'waiting_retry') && v.voiceTaskActiveRef.current) {
        v.voiceWaitingForTaskRef.current = false;
        v.voiceTaskActiveRef.current = false;
        v.drainVoiceQueueRef.current();
      }
      v.startListeningRef.current();
    };
    const message = spokenReply(activeTask, voiceLanguage);
    v.setLastReply(activeTask.plan_note && activeTask.status === 'completed' ? activeTask.plan_note : message);
    const serverVoice = v.speechReadyRef.current;
    if ((!serverVoice && !('speechSynthesis' in window)) || prefs?.persona.speak_replies === false) {
      if (v.handsFreeRef.current) finishVoiceTask();
      return;
    }

    const epoch = ++v.speechEpochRef.current;
    if (!earlyRef.current) {  // an acknowledgement still being spoken is finished first, not cut off
      v.playerRef.current?.stop();
      v.playerRef.current?.stop();
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    }
    v.spokenTextRef.current = message;
    v.voiceSpeakingRef.current = true;
    v.setSpeaking(true);
    const finish = finishOnce(() => {
      v.setSpeaking(false);
      if (epoch !== v.speechEpochRef.current) return;
      v.voiceSpeakingRef.current = false;
      if (v.handsFreeRef.current) finishVoiceTask();
      else if (serverVoice && v.followUpMs > 0 && v.listenModeRef.current === 'push_to_talk' && v.voiceTaskActiveRef.current && isTerminal) {
        // "No, that's all" closed the session: go back to idle instead of listening again. Any other reply keeps the conversation open.
        void voiceApi.session().then((s) => { if (s.open) beginFollowUp(); }).catch(() => beginFollowUp());
      }
    });
    v.speechFinishRef.current = finish;
    if (v.handsFreeRef.current) v.startListeningRef.current();

    if (serverVoice) {
      // The phrase comes from the server so "Done" can only be said for a task the verifier confirmed.
      void (async () => {
        let text = message;
        try {
          text = await voiceApi.line({ task: activeTask, ...v.wording() });
        } catch {
          // keep the short built-in phrase
        }
        if (earlyRef.current) {
          await Promise.race([earlyRef.current, new Promise((resolve) => window.setTimeout(resolve, 2500))]);
          earlyRef.current = null;
          v.playerRef.current?.stop();
        }
        if (epoch !== v.speechEpochRef.current) return;
        v.spokenTextRef.current = text;
        v.setLastReply(activeTask.plan_note && activeTask.status === 'completed' ? activeTask.plan_note : text);
        try {
          await v.playerRef.current?.speak(text, () => {
            const started = v.speechEndedAtRef.current;
            if (started > 0) {
              v.speechEndedAtRef.current = 0;
              void voiceApi.latency('local', { first_word_played: performance.now() - started });
            }
          });
        } finally {
          finish();
        }
      })();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(message);
    utterance.lang = voiceLanguage;
    utterance.onend = finish;
    utterance.onerror = finish;
    try {
      window.speechSynthesis.speak(utterance);
    } catch {
      // A broken audio device must never take the UI down; the reply is still on screen.
      finish();
    }
  }, [activeTask, voiceLanguage, prefs?.persona.speak_replies]);

  // Spoken acknowledgement for tasks that take a moment ("Opening Chrome."), and a progress line for slow ones.
  // Fast tasks never trigger either: the timer is cleared as soon as the task finishes.
  useEffect(() => {
    if (!v.speechReady || prefs?.persona.speak_replies === false || !activeTask) return;
    if (!['planned', 'approved', 'executing'].includes(activeTask.status)) return;
    const speakLine = async (key: string, body: Parameters<typeof voiceApi.line>[0]) => {
      if (ackedTasks.current.has(key) || v.voiceSpeakingRef.current) return;
      ackedTasks.current.add(key);
      try {
        await v.playerRef.current?.speak(await voiceApi.line(body));
      } catch {
        // an acknowledgement is optional
      }
    };
    const first = activeTask.actions[0];
    const ack = window.setTimeout(() => {
      if (earlyRef.current || Date.now() - earlyAtRef.current < 8000) return;  // already acknowledged in parallel
      void speakLine(`${activeTask.id}:ack`, first ? { tool: first.tool_name, args: first.args, ...v.wording() } : { event: 'acknowledge', ...v.wording() });
    }, 900);
    const progress = window.setTimeout(() => {
      void speakLine(`${activeTask.id}:progress`, { event: 'progress', ...v.wording() });
    }, 4500);
    return () => {
      window.clearTimeout(ack);
      window.clearTimeout(progress);
    };
  }, [activeTask?.id, activeTask?.status, v.speechReady, voiceLanguage, prefs?.persona.speak_replies]);

  return { earlyAck };
}
