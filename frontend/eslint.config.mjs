import { dirname } from 'path';
import { fileURLToPath } from 'url';
import { FlatCompat } from '@eslint/eslintrc';

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

/**
 * ESLint was declared in package.json and installed, with no config file — so
 * it never ran, in the build or out of it. `next build` only lints when ESLint
 * is configured, which is why the build was "clean": the check was absent, not
 * passing. That is also a guardrails C4 violation (a declared dependency that
 * is neither imported nor removed).
 *
 * react-hooks/exhaustive-deps matters most here. This session found three
 * effects that wrote state after an await with no cancellation guard, one of
 * which could merge another patient's CRDT state into an open editor. That
 * class of bug is exactly what the hooks rules are for.
 */
const config = [
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'] },
  {
    rules: {
      // A leading underscore means "deliberately discarded". The codebase
      // already uses it for exactly that — patientSafeGlanceCache destructures
      // `_topItems` and `_changes` off a glance cache precisely so those keys
      // cannot reach a patient. Flagging that as dead code would push someone
      // to "tidy" away a privacy control.
      '@typescript-eslint/no-unused-vars': ['warn', {
        varsIgnorePattern: '^_',
        argsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
        destructuredArrayIgnorePattern: '^_',
      }],
    },
  },
];

export default config;
