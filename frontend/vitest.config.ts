import { defineConfig } from 'vitest/config';
import path from 'node:path';

/**
 * Two kinds of test live here and they need different environments.
 *
 * `lib/**` are pure-function tests with no DOM and stay on `environment:
 * 'node'`, which is the default below — nothing about them changes.
 *
 * `components/**` mount React. Those files opt in per-file with a
 * `@vitest-environment jsdom` docblock rather than a global switch, so one
 * component test cannot silently drag every pure test into a DOM it does not
 * need. jest-dom matchers are imported by the test file itself for the same
 * reason: no global setup that only half the suite uses.
 *
 * JSX is transformed by vitest's own esbuild rather than @vitejs/plugin-react.
 * The plugin depends on a newer vite than vitest 2.1.9 carries, and installing
 * it put a second copy of vite under frontend/node_modules — which typechecks
 * as two incompatible `Plugin` types. esbuild's automatic runtime needs no
 * plugin and no second vite.
 *
 * The `@/` alias is required because the app imports through it; without it a
 * module with a runtime `@/` import fails at collection with "Failed to load
 * url", which reads like a missing file rather than a missing alias.
 */
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    include: ['lib/**/*.test.ts', 'components/**/*.test.tsx'],
    environment: 'node',
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, '.') },
  },
});
