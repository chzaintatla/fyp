import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import vm from 'node:vm';

const compiled = ts.transpileModule(readFileSync(new URL('./src/speech/vad.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const sandbox = { exports: {}, Float32Array, Uint8Array, ArrayBuffer, DataView, Math };
vm.runInNewContext(compiled, sandbox);
const { VadSegmenter, rms, downsample, encodeWav, DEFAULT_VAD } = sandbox.exports;

const run = (vad, levels, frameMs = 20) => levels.map((l) => vad.feed(l, frameMs)).filter(Boolean);

test('speech start then end after the configured silence', () => {
  const vad = new VadSegmenter(DEFAULT_VAD);
  const loud = Array(20).fill(0.1); // 400 ms
  const quiet = Array(40).fill(0.001); // 800 ms
  assert.deepEqual(run(vad, [...loud, ...quiet]), ['speech_start', 'speech_end']);
});

test('a short click is not speech', () => {
  const vad = new VadSegmenter(DEFAULT_VAD);
  assert.deepEqual(run(vad, [0.2, 0.2, ...Array(60).fill(0)]), []);
});

test('a pause shorter than the end silence does not split a sentence', () => {
  const vad = new VadSegmenter(DEFAULT_VAD);
  const events = run(vad, [...Array(20).fill(0.1), ...Array(20).fill(0), ...Array(20).fill(0.1), ...Array(40).fill(0)]);
  assert.deepEqual(events, ['speech_start', 'speech_end']);
});

test('a higher threshold ignores quiet leakage of the assistant voice', () => {
  const vad = new VadSegmenter({ ...DEFAULT_VAD, threshold: DEFAULT_VAD.threshold * 2.2 });
  assert.deepEqual(run(vad, Array(50).fill(0.03)), []);
});

test('rms of silence is 0 and of a full-scale square wave is 1', () => {
  assert.equal(rms(new Float32Array(10)), 0);
  assert.equal(rms(new Float32Array(10).fill(1)), 1);
  assert.equal(rms(new Float32Array(0)), 0);
});

test('downsample 48k to 16k keeps one third of the samples', () => {
  assert.equal(downsample(new Float32Array(4800), 48000).length, 1600);
  const same = new Float32Array(10);
  assert.equal(downsample(same, 16000), same);
});

test('encodeWav writes a valid 16-bit mono header and clamps samples', () => {
  const wav = encodeWav(new Float32Array([0, 1, -1, 2]), 16000);
  const text = (a, b) => String.fromCharCode(...wav.slice(a, b));
  const view = new DataView(wav.buffer);
  assert.equal(text(0, 4), 'RIFF');
  assert.equal(text(8, 12), 'WAVE');
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint32(40, true), 8);
  assert.equal(wav.length, 44 + 8);
  assert.equal(view.getInt16(46, true), 32767);
  assert.equal(view.getInt16(48, true), -32768);
  assert.equal(view.getInt16(50, true), 32767);
});
