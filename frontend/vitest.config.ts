import { defineConfig } from 'vitest/config';
import path from 'node:path';

/**
 * Minimal config so tests can resolve the `@/` alias the app uses.
 *
 * Without it, only type-only `@/` imports work (TypeScript erases those), and a
 * module with a runtime `@/` import fails at collection with "Failed to load
 * url" — which reads like a missing file rather than a missing alias.
 *
 * `include` is scoped to lib/ deliberately: these are pure-function tests with
 * no DOM. Rendering the workspace needs jsdom and a testing-library setup, which
 * this project does not have — and pretending otherwise by widening the glob
 * would make an absent harness look present.
 */
export default defineConfig({
  test: {
    include: ['lib/**/*.test.ts'],
    environment: 'node',
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, '.') },
  },
});
