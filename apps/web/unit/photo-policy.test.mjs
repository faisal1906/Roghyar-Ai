import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const readJson = name => JSON.parse(readFileSync(new URL('../' + name, import.meta.url), 'utf8'));

test('frontend runtime dependency inventory contains no photo-processing capability', () => {
  const manifest = readJson('package.json');
  const lock = readJson('package-lock.json');
  assert.deepEqual(Object.keys(manifest.dependencies).sort(), ['react', 'react-dom']);
  assert.deepEqual(
    Object.keys(manifest.devDependencies).sort(),
    ['@axe-core/playwright', '@playwright/test', '@types/node', '@types/react', '@types/react-dom',
      'openapi-typescript', 'typescript', 'vite', 'vite-plugin-pwa'],
  );
  const runtime = Object.entries(lock.packages)
    .filter(([path, pkg]) => path && !pkg.dev)
    .map(([path]) => path).sort();
  assert.deepEqual(runtime, ['node_modules/react', 'node_modules/react-dom', 'node_modules/scheduler']);
});
