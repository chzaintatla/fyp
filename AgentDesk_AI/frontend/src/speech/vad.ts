// Pure helpers for the microphone loop (no DOM), so they can be tested without a browser.

export type VadEvent = 'speech_start' | 'speech_end' | 'too_short';

export interface VadOptions {
  /** RMS level (0..1) that counts as speech. */
  threshold: number;
  /** Speech must last this long before `speech_start` fires (filters clicks and coughs). */
  minSpeechMs: number;
  /** Silence this long after speech ends the utterance. */
  endSilenceMs: number;
}

export const DEFAULT_VAD: VadOptions = { threshold: 0.018, minSpeechMs: 160, endSilenceMs: 650 };

/** Energy-based end-of-speech detector. Feed it one RMS value per audio frame. */
export class VadSegmenter {
  private speechMs = 0;
  private silenceMs = 0;
  private active = false;

  constructor(public options: VadOptions = DEFAULT_VAD) {}

  get inSpeech(): boolean {
    return this.active;
  }

  reset(): void {
    this.speechMs = 0;
    this.silenceMs = 0;
    this.active = false;
  }

  feed(rms: number, frameMs: number): VadEvent | null {
    const loud = rms >= this.options.threshold;
    if (!this.active) {
      this.speechMs = loud ? this.speechMs + frameMs : 0;
      if (this.speechMs >= this.options.minSpeechMs) {
        this.active = true;
        this.silenceMs = 0;
        return 'speech_start';
      }
      return null;
    }
    this.silenceMs = loud ? 0 : this.silenceMs + frameMs;
    if (this.silenceMs >= this.options.endSilenceMs) {
      this.reset();
      return 'speech_end';
    }
    return null;
  }
}

export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/** Average-decimate to `target` Hz (speech models use 16 kHz; a smaller upload is a faster upload). */
export function downsample(input: Float32Array, from: number, target = 16000): Float32Array {
  if (from <= target) return input;
  const ratio = from / target;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

/** 16-bit mono PCM WAV. */
export function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}
