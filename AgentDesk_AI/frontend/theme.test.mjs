import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import vm from 'node:vm';

const compiled = ts.transpileModule(readFileSync(new URL('./src/theme.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const sandbox = { exports: {}, parseInt, Math };
vm.runInNewContext(compiled, sandbox);
const { contrast } = sandbox.exports;

// Read the real tokens from the stylesheet so the test cannot drift from what ships.
const css = readFileSync(new URL('./src/index.css', import.meta.url), 'utf8');
const block = (selector) => {
  const start = css.indexOf(selector);
  const body = css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start));
  return Object.fromEntries([...body.matchAll(/--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})/g)].map((m) => [m[1], m[2]]));
};
const dark = block(':root {');
const light = block(":root[data-theme='light']");

const PAIRS = [
  ['ink', 'bg'], ['ink', 'surface'], ['ink', 'raised'], ['ink2', 'bg'], ['ink2', 'surface'], ['ink2', 'raised'],
  ['on-accent', 'accent'], ['on-ok', 'ok'], ['warn', 'surface'], ['bad', 'surface'], ['ok', 'surface'], ['accent', 'surface'],
];

for (const [name, tokens] of [['dark', dark], ['light', light]]) {
  test(`${name} theme: every text/background pair is at least 4.5:1`, () => {
    for (const [fg, bg] of PAIRS) {
      assert.ok(tokens[fg] && tokens[bg], `missing token ${fg} or ${bg} in ${name}`);
      const ratio = contrast(tokens[fg], tokens[bg]);
      assert.ok(ratio >= 4.5, `${name}: ${fg} ${tokens[fg]} on ${bg} ${tokens[bg]} is ${ratio.toFixed(2)}:1`);
    }
  });
}

test('every avatar accent stays readable as text on the dark surface', () => {
  for (const color of ['#38E1FF', '#F5C46B', '#FF5CC8', '#4C8DFF', '#A9B6CC', '#3EE69A']) {
    assert.ok(contrast(color, dark.surface) >= 4.5, `${color} on dark surface`);
    assert.ok(contrast(dark['on-accent'], color) >= 4.5, `on-accent on ${color}`);
  }
});
