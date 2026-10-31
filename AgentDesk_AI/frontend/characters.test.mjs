import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import vm from 'node:vm';

const compiled = ts.transpileModule(readFileSync(new URL('./src/characters.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const sandbox = { exports: {}, require: () => ({}), localStorage: undefined };
vm.runInNewContext(compiled, sandbox);
const { resolveStyle, directionFor, voiceOptions, applyAvatar, englishVoicesFor, accentFor, sampleLine } = sandbox.exports;

const catalog = {
  avatars: [
    { id: 'nova', name: 'Nova', gender: 'female', color: '#38E1FF', voice_en: 'diana', voice_ur: 'ur-PK-UzmaNeural', voice_hi: 'hi-IN-SwaraNeural' },
    { id: 'orion', name: 'Orion', gender: 'male', color: '#4C8DFF', voice_en: 'daniel', voice_ur: 'ur-PK-AsadNeural', voice_hi: 'hi-IN-MadhurNeural' },
  ],
  voices: { english_female: ['autumn', 'diana', 'hannah'], english_male: ['austin', 'daniel', 'troy'] },
};
const persona = { name: 'Agent X', avatar_id: 'nova', gender: 'female', voice_en: 'diana', voice_ur: 'ur-PK-UzmaNeural',
  voice_hi: 'hi-IN-SwaraNeural', personality: 'assistant', formality: 50, humour: 50, user_name: '' };

test('custom personality resolves like the backend (same table as test_m3_personas.py)', () => {
  assert.equal(resolveStyle('custom', 90, 50), 'assistant');
  assert.equal(resolveStyle('custom', 20, 90), 'friend');
  assert.equal(resolveStyle('custom', 40, 20), 'mentor');
  assert.equal(resolveStyle('coach'), 'coach');
});

test('vocal directions per personality', () => {
  const d = (personality) => directionFor({ personality, formality: 50, humour: 50 });
  assert.deepEqual(['assistant', 'mentor', 'friend', 'coach'].map(d), ['professionally', 'warm', 'cheerful', 'confidently']);
});

test('picking a male avatar switches to a male voice and renames only default names', () => {
  const orion = applyAvatar(persona, catalog.avatars[1], catalog);
  assert.equal(orion.gender, 'male');
  assert.equal(orion.voice_en, 'daniel');
  assert.equal(orion.voice_ur, 'ur-PK-AsadNeural');
  assert.equal(orion.name, 'Orion');
  assert.equal(applyAvatar({ ...persona, name: 'Jarvis' }, catalog.avatars[1], catalog).name, 'Jarvis');
  assert.equal(voiceOptions(orion, 'en-US').gender, 'male');
});

test('voice options carry the persona voices and the direction', () => {
  assert.deepEqual({ ...voiceOptions({ ...persona, personality: 'friend' }, 'ur-PK') }, {
    language: 'ur-PK', gender: 'female', voice_en: 'diana', voice_ur: 'ur-PK-UzmaNeural', voice_hi: 'hi-IN-SwaraNeural', direction: 'cheerful' });
});

test('english voice list follows the gender; neutral offers all', () => {
  assert.deepEqual([...englishVoicesFor('male', catalog)], ['austin', 'daniel', 'troy']);
  assert.equal(englishVoicesFor('neutral', catalog).length, 6);
});

test('accent colour comes from the avatar, with a fallback', () => {
  assert.equal(accentFor({ avatar_id: 'orion' }, catalog), '#4C8DFF');
  assert.equal(accentFor({ avatar_id: 'custom' }, catalog), '#38E1FF');
  assert.equal(accentFor({ avatar_id: 'nova' }, null), '#38E1FF');
});

test('sample lines differ by personality and use the name only when there is one', () => {
  assert.equal(sampleLine('friend', 'Nabiha', 'Nova'), 'Hey, Nabiha! Ready when you are.');
  assert.equal(sampleLine('friend', '', 'Nova'), 'Hey! Ready when you are.');
  assert.notEqual(sampleLine('coach', '', 'Nova'), sampleLine('mentor', '', 'Nova'));
});
