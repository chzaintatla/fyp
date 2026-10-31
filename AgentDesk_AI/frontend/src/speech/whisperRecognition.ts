import { voiceApi } from '../api';
import { DEFAULT_VAD, VadSegmenter, downsample, encodeWav, rms } from './vad';

/** The few fields of the browser's SpeechRecognition result that App.tsx reads. */
interface Alt {
  transcript: string;
}
interface Result {
  readonly isFinal: boolean;
  readonly [index: number]: Alt;
}
export interface WhisperResultEvent extends Event {
  readonly resultIndex: number;
  readonly results: ArrayLike<Result>;
}
export interface WhisperErrorEvent extends Event {
  readonly error: string;
  readonly message?: string;
}

const PRE_ROLL_MS = 300;
const MAX_UTTERANCE_MS = 30_000;
const SPEAKING_THRESHOLD_FACTOR = 2.2; // while the assistant talks, only clearly louder speech counts

/**
 * Drop-in for `webkitSpeechRecognition` that records the microphone, finds the end of each sentence locally
 * (energy VAD) and sends only that sentence to Groq Whisper through `/voice/stt`.
 * `onspeechstart` fires as soon as the user begins to talk, which is how barge-in stops the assistant at once.
 */
export class WhisperRecognition {
  lang = 'en-US';
  continuous = true;
  interimResults = false;
  prompt = '';
  /** Set while the assistant is speaking so its own voice leaking into the mic is not taken as the user. */
  assistantSpeaking = false;
  /** Last end-of-speech time (performance.now()), for the latency log. */
  lastSpeechEndAt = 0;
  onresult: ((event: WhisperResultEvent) => void) | null = null;
  onerror: ((event: WhisperErrorEvent) => void) | null = null;
  onend: (() => void) | null = null;
  onspeechstart: (() => void) | null = null;
  onlevel: ((level: number) => void) | null = null;

  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: ScriptProcessorNode | null = null;
  private vad = new VadSegmenter(DEFAULT_VAD);
  private preRoll: Float32Array[] = [];
  private utterance: Float32Array[] = [];
  private sampleRate = 48000;
  private stopped = true;

  static get supported(): boolean {
    return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof AudioContext !== 'undefined';
  }

  async start(): Promise<void> {
    this.stopped = false;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (error) {
      const denied = error instanceof DOMException && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
      this.fail(denied ? 'not-allowed' : 'audio-capture');
      return;
    }
    if (this.stopped) return this.release();
    this.ctx = new AudioContext();
    this.sampleRate = this.ctx.sampleRate;
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(2048, 1, 1);
    this.node.onaudioprocess = (e) => this.onFrame(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(this.node);
    this.node.connect(this.ctx.destination); // required for the node to run; its output is silent
  }

  abort(): void {
    this.stopped = true;
    this.release();
    this.onend?.();
  }

  stop(): void {
    this.abort();
  }

  private release(): void {
    this.node?.disconnect();
    this.node = null;
    void this.ctx?.close();
    this.ctx = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.vad.reset();
    this.preRoll = [];
    this.utterance = [];
  }

  private onFrame(frame: Float32Array): void {
    if (this.stopped) return;
    const frameMs = (frame.length / this.sampleRate) * 1000;
    const level = rms(frame);
    this.onlevel?.(level);
    this.vad.options = {
      ...DEFAULT_VAD,
      threshold: DEFAULT_VAD.threshold * (this.assistantSpeaking ? SPEAKING_THRESHOLD_FACTOR : 1),
    };
    const wasSpeaking = this.vad.inSpeech;
    const event = this.vad.feed(level, frameMs);
    if (wasSpeaking || event === 'speech_start') {
      this.utterance.push(frame);
    } else {
      this.preRoll.push(frame);
      while (this.preRoll.length * frameMs > PRE_ROLL_MS) this.preRoll.shift();
    }
    if (event === 'speech_start') {
      this.utterance = [...this.preRoll, ...this.utterance];
      this.preRoll = [];
      this.onspeechstart?.();
    } else if (event === 'speech_end' || (this.vad.inSpeech && this.utterance.length * frameMs > MAX_UTTERANCE_MS)) {
      this.lastSpeechEndAt = performance.now();
      const frames = this.utterance;
      this.utterance = [];
      if (event !== 'speech_end') this.vad.reset();
      void this.transcribe(frames);
    }
  }

  private async transcribe(frames: Float32Array[]): Promise<void> {
    const total = frames.reduce((n, f) => n + f.length, 0);
    const merged = new Float32Array(total);
    let offset = 0;
    for (const f of frames) {
      merged.set(f, offset);
      offset += f.length;
    }
    const wav = encodeWav(downsample(merged, this.sampleRate), Math.min(this.sampleRate, 16000));
    try {
      const text = (await voiceApi.stt(wav, this.lang.slice(0, 2), this.prompt || undefined)).trim();
      if (!text || this.stopped) return;
      const result: Result = Object.assign([{ transcript: text }], { isFinal: true }) as unknown as Result;
      const event = Object.assign(new Event('result'), { resultIndex: 0, results: [result] }) as WhisperResultEvent;
      this.onresult?.(event);
    } catch (error) {
      this.fail('network', error instanceof Error ? error.message : undefined);
    }
  }

  private fail(error: string, message?: string): void {
    this.onerror?.(Object.assign(new Event('error'), { error, message }) as WhisperErrorEvent);
  }
}
