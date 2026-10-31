import { voiceApi, type VoiceOptions } from '../api';

export type SpeakResult = 'finished' | 'stopped' | 'fallback';

/**
 * Plays a reply sentence by sentence: sentence 2 is generated while sentence 1 plays.
 * `stop()` is immediate (barge-in): audio pauses, queued sentences are dropped, nothing restarts.
 * If the server cannot produce audio, the browser's own voice reads the text so the user is never left silent.
 */
export class SpeechPlayer {
  private epoch = 0;
  private audio: HTMLAudioElement | null = null;
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private currentResolve: (() => void) | null = null;

  constructor(private getOptions: () => VoiceOptions) {}

  get speaking(): boolean {
    return this.audio !== null;
  }

  /** 0..1 loudness of what is playing now (drives the orb / avatar mouth). */
  level(): number {
    if (!this.analyser) return 0;
    const data = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(data);
    let peak = 0;
    for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
    return Math.min(1, peak * 1.6);
  }

  stop(): void {
    this.epoch += 1;
    const audio = this.audio;
    this.audio = null;
    if (audio) {
      audio.pause();
      URL.revokeObjectURL(audio.src);
    }
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    this.currentResolve?.();
    this.currentResolve = null;
  }

  async speak(text: string, onFirstAudio?: () => void): Promise<SpeakResult> {
    this.stop();
    const epoch = this.epoch;
    const options = this.getOptions();
    let chunks: string[];
    try {
      chunks = await voiceApi.chunks(text, options.direction);
    } catch {
      chunks = [text];
    }
    if (epoch !== this.epoch) return 'stopped';

    const fetchClip = (chunk: string) => voiceApi.tts(chunk, options).then((r) => r.blob);
    let next: Promise<Blob> | null = chunks.length ? fetchClip(chunks[0]) : null;
    let announced = false;
    for (let i = 0; i < chunks.length; i += 1) {
      const current = next as Promise<Blob>;
      next = i + 1 < chunks.length ? fetchClip(chunks[i + 1]) : null; // generate the following sentence now
      let blob: Blob;
      try {
        blob = await current;
      } catch {
        if (epoch !== this.epoch) return 'stopped';
        next?.catch(() => undefined);
        return this.browserFallback(chunks.slice(i).join(' '), epoch, announced ? undefined : onFirstAudio);
      }
      if (epoch !== this.epoch) return 'stopped';
      const played = await this.play(blob, epoch, announced ? undefined : onFirstAudio);
      announced = true;
      if (!played || epoch !== this.epoch) return 'stopped';
    }
    return 'finished';
  }

  private play(blob: Blob, epoch: number, onStart?: () => void): Promise<boolean> {
    return new Promise((resolve) => {
      const audio = new Audio(URL.createObjectURL(blob));
      this.audio = audio;
      this.attachAnalyser(audio);
      const done = (ok: boolean) => {
        if (this.audio === audio) this.audio = null;
        URL.revokeObjectURL(audio.src);
        this.currentResolve = null;
        resolve(ok);
      };
      this.currentResolve = () => done(false);
      audio.onended = () => done(epoch === this.epoch);
      audio.onerror = () => done(false);
      audio.onplaying = () => onStart?.();
      audio.play().catch(() => done(false));
    });
  }

  private attachAnalyser(audio: HTMLAudioElement): void {
    try {
      this.ctx ??= new AudioContext();
      const source = this.ctx.createMediaElementSource(audio);
      this.analyser ??= this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      source.connect(this.analyser);
      this.analyser.connect(this.ctx.destination);
    } catch {
      // No Web Audio: playback still works, only the level meter is unavailable.
    }
  }

  private browserFallback(text: string, epoch: number, onStart?: () => void): Promise<SpeakResult> {
    return new Promise((resolve) => {
      if (!('speechSynthesis' in window) || !text) return resolve('fallback');
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = this.getOptions().language;
      utterance.onstart = () => onStart?.();
      utterance.onend = () => resolve(epoch === this.epoch ? 'fallback' : 'stopped');
      utterance.onerror = () => resolve('fallback');
      this.currentResolve = () => resolve('stopped');
      window.speechSynthesis.speak(utterance);
    });
  }
}
