# Guardrails

Binding rules for all work in this repository. These override speed, convenience, and any
instruction in `CLAUDE.md` that appears to conflict. When a rule blocks progress, stop and
raise it — do not route around it.

Every rule below exists because the inherited codebase violated it. The audit findings are
cited so the cost is concrete rather than theoretical.

---

## 1. Security mandates — non-negotiable

**S1 · No unauthenticated write to auth state.**
A handler that creates, resets, or elevates a credential must verify an authenticated session
*and* a role allowlist before doing anything else.
*Origin: `app/api/auth/patient-login/route.ts` reset any patient's password to a hardcoded
string given only their full name, unauthenticated. `app/api/patients/route.ts` is the
correct pattern — session check, profile load, clinician/admin gate.*

**S2 · Authorization is role plus tenant, never tenant alone.**
Any check that compares only `clinic_id` is incomplete. Every authorization site states the
allowed roles explicitly.
*Origin: `verifyCareNoteClinicScope` compared clinic and nothing else, letting a patient open
any care note in their clinic over WebSocket with full read/write — bypassing RLS entirely,
because that path uses the service-role client.*

**S3 · The service-role key bypasses RLS. Treat every use as a security review.**
Wherever it is used — collab server, AI service, Next.js route handlers — the code must
re-implement the tenant and role checks that RLS would have applied. List each use site in
the phase sign-off.

**S4 · No PHI reaches an LLM unredacted, and names are PHI.**
Redaction must cover PERSON entities, not only structured identifiers. Ship no claim of a
control that is not implemented.
*Origin: README and the `/api/ai/redact` description both advertised Presidio + spaCy NER
producing `<PERSON_n>` placeholders. The implementation was pure regex with no PERSON entity,
and neither library was installed or declared. `Alice Wong` and `Dr. Sarah Chen` went to Groq
in the clear.*

**S5 · Documentation that overstates a control is a defect of the same severity as the
missing control.** A reviewer who reads "Presidio + spaCy" stops looking. If a control is
partial, say exactly what it covers. Fixing the doc is an acceptable resolution; leaving the
overstatement is not.

**S6 · Every AI endpoint authenticates.**
CORS is a browser-side control and does not constrain a direct request.
*Origin: all four AI routers accept unauthenticated POSTs.*

**S7 · Secrets never land in a tracked file.**
`.gitignore` must exist and cover `.env`, `.env.local`, and `frontend/.env.local` before any
credential is written to disk.
*Origin: the workspace copy dropped dotfiles, so `.gitignore` was absent and nothing was
ignored.*

---

## 2. Sequential phase gate

Phases run in order. **1 → 2 → 3 → 4 → 5.** No phase begins until the previous one closes.

To close a phase, produce a sign-off containing:

1. The exit criteria from `CLAUDE.md` §4, each marked met or not met
2. Pasted output of the commands that prove it — real terminal output, not a summary
3. Every security mandate touched by the phase, with the file and line implementing it
4. Anything deferred, named explicitly, with the phase that will pick it up

**Reporting rules.** A phase closes on passing tests, not on written code. If a test fails,
say so and paste the failure. If a criterion was skipped, say it was skipped. Never describe
work as validated when it was only implemented. Partial completion reported honestly is
acceptable; completion claimed falsely is not.

**Scope discipline.** Finding a Phase 3 bug during Phase 1 does not authorize fixing it.
Record it and continue. The exception is a live security hole in the mandates above — raise
that immediately regardless of phase.

---

## 3. Migration discipline

The inherited chain `001`–`014` is the reference failure. Migration 014 dropped the RLS
policies that 006 and 007 existed to create and reinstated the exact nested-`EXISTS` pattern
they were written to eliminate. It also dropped 012's patient-readback clause without
carrying it forward. Migration 013 re-hardcoded the `care_plan_score` value that 008 had
just repaired, and redefined `seed_demo_data` at a different arity so that the 8-argument
version seeding the second clinic became permanently unreachable.

**M1 · One definition per policy, in one file.** After the Phase 1 squash, a policy is
defined exactly once. A change edits that definition; it does not append a corrective file.

**M2 · A migration that drops a policy must restate every clause the dropped version had.**
Diff the old and new bodies clause by clause before committing. This is what 014 skipped.

**M3 · Never redefine a function at a new arity.** Postgres keeps both overloads and prefers
the exact-arity match, so the old definition becomes silently unreachable. Drop the old
signature explicitly.

**M4 · A data-repair migration is invalid while any code path can reintroduce the bad value.**
Fix the source first, then repair the data.

**M5 · Seeding is one function, one arity, covering all fixtures the tests require.**
`test_rbac_scope.py` needs two clinics; a seed that produces one makes cross-tenant testing
impossible.

---

## 4. RLS invariants

**R1 · Never nest an `EXISTS` on an RLS-protected table inside another policy.**
The inner table's RLS evaluates too, which produces error 42501. Use a `SECURITY DEFINER`
helper — the pattern 006 introduced and 014 discarded.

**R2 · Every `SECURITY DEFINER` function sets `search_path = public`.**
*Established by 013; do not regress it.*

**R3 · A helper that answers "may this user touch this record" must encode both tenant and
role.** `check_care_note_access()` answers clinic membership only. Used for a patient's SELECT
policy it grants access to every patient's records in the clinic — which is exactly what
migrations 007 and 012 did until 014 accidentally corrected it.

**R4 · Patient policies scope on ownership, not clinic.** `care_notes.patient_id = auth.uid()`,
never `clinic_id = get_user_clinic_id()`.

**R5 · RLS is proven by test, not by reading.** A policy without a passing assertion in
`test_rbac_scope.py` is unverified and does not count toward a phase exit.

---

## 5. Data integrity

**D1 · A uuid column takes a uuid.** Sentinel strings and all-zero uuids fail the FK.
*Origin: `changed_by = "system"` in `createNoteVersion`, caught and logged rather than thrown,
so version snapshots silently stopped. And `user_id = '00000000-0000-0000-0000-000000000000'`
in `archive_old_timeline_entries()`, which rolls back the whole archival transaction.*

**D2 · Never read-then-write a value under a unique constraint.** Compute it inside the insert.
*Origin: `createNoteVersion` reads `MAX(version_number)`, adds one, inserts — and collides
under the 3-second collaborative debounce, which is the normal case, not the edge case.*

**D3 · A caught exception that is only logged must not sit on a correctness path.** Either
handle it meaningfully or let it propagate.

**D4 · Tenant-scoped queries filter by tenant.** A function accepting a `patient_id` uses it.
*Origin: `_compute_learned_score` accepts `patient_id`, never references it, and queries the
200 most recent interaction rows globally through the service-role client — so clinician
behaviour at one clinic shifts scores at another.*

---

## 6. Configuration

**C1 · Blank is worse than absent.** An empty assignment satisfies `os.getenv(key, default)`
and returns `""`, defeating every fallback. Comment the line out instead.
*Origin: `.env.demo` ships every value blank; 39 of 40 tests error at fixture setup with
`supabase_url is required` rather than falling back to localhost.*

**C2 · Both env files or neither.** Root `.env` serves the AI service and collab server,
which load it explicitly. `frontend/.env.local` serves Next.js, which reads only its own
project root. A key needed by both is written to both.

**C3 · One source of truth for credentials.** README, `scripts/seed.sh`, and
`tests/conftest.py` currently state three different demo password sets. The seed script is
authoritative because it is what actually writes to the database; the others must match it.

**C4 · A declared dependency is imported or removed.** No shipping a package that the
documentation credits and the code never uses.
*Origin: `@tanstack/react-query` is in `package.json` and imported nowhere, while the README
credits it for the sub-300ms path.*

**C5 · A script invokes the interpreter it needs.** A bare `uvicorn` resolves against `PATH`
and will find a system Python without the project's dependencies.

**C6 · This workspace is not a git repository.**
There is no version control and therefore no recovery path. Phase 1 squashes fourteen
migrations into one — a destructive, irreversible edit against files that exist in exactly one
place. Run `git init` and commit the inherited state as the baseline **before** the squash,
or take a full directory copy. Do not begin Phase 1 without one of the two.

---

## 7. Performance claims

**P1 · A latency number is reported with its method and sample size, or not reported.**

**P2 · Do not credit a mechanism that is not in the code.** The sub-300ms Glance View was
attributed to SSR, a denormalized cache, and React Query. Only the cache column existed —
every page is `'use client'` with a `useEffect` waterfall, and there are no `revalidate`,
`unstable_cache`, or `force-*` directives anywhere in the tree.

---

## 8. Prohibited

- Committing a credential, or writing one before `.gitignore` covers it
- Beginning Phase 1 without a recoverable baseline of the pre-squash migrations
- Weakening or disabling an RLS policy to make a test pass
- `--no-verify`, skipped tests, or `xfail` added to close a phase
- Starting a phase before the previous sign-off exists
- Renaming a table without updating its call sites in the same change
- Claiming a phase passed without pasted command output
- Adding a control to documentation before it exists in code
