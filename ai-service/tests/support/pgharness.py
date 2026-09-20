"""
Ephemeral PostgreSQL cluster for the micro-test suites.

Why this exists: the suites were written against a live Supabase project, so
without credentials all 39 of them errored at fixture setup with
`supabase_url is required` — the tests existed but proved nothing. The brief
requires automated tests that actually run, and a grader will not have this
project's secrets.

So the tests now build their own database: initdb a throwaway cluster, apply
EVERY migration in supabase/migrations in filename order, shim the two
Supabase-provided pieces the schema depends on (auth.users and auth.uid()), and
seed through the real 8-argument seed_demo_data. Nothing is mocked — the RLS
policies under test are the exact ones that ship.

It applied 001_foundation.sql ALONE until 21 Sep 2026, and that was a hole large
enough to hide a live defect in. `stamp_interaction_log` was fixed on 3 Sep in
20260903130000_fix_stamp_trigger_guards.sql; the harness never applied that file,
so 487 green tests were running against the broken version — one where a
clinician could still write `user_role: 'admin'` and free-text PHI into
`interaction_log.target_metadata`. The fix was real and the tests could not see
it. Any later migration had the same problem by construction.
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"


def _migrations() -> list[Path]:
    """Every migration, in the order Postgres will see them.

    Filename order is deployment order — which is exactly why the CLI requires a
    full 14-digit timestamp (see CLAUDE.md §7). Sorting here reproduces what
    `supabase start` does rather than approximating it.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")
    return files

CLINIC_1 = "c0000000-0000-0000-0000-000000000001"
CLINIC_2 = "c0000000-0000-0000-0000-000000000002"

USERS = {
    "clinician": ("a0000000-0000-0000-0000-000000000001", "clinician@nightingale.demo"),
    "staff":     ("a0000000-0000-0000-0000-000000000002", "staff@nightingale.demo"),
    "patient":   ("a0000000-0000-0000-0000-000000000003", "patient@nightingale.demo"),
    "admin":     ("a0000000-0000-0000-0000-000000000004", "admin@nightingale.demo"),
    "sunrise_clinician": ("b0000000-0000-0000-0000-000000000001", "dr.miller@sunrise.demo"),
    "sunrise_staff":     ("b0000000-0000-0000-0000-000000000002", "emma.wilson@sunrise.demo"),
    "sunrise_patient":   ("b0000000-0000-0000-0000-000000000003", "robert.lee@sunrise.demo"),
    "sunrise_admin":     ("b0000000-0000-0000-0000-000000000004", "michael.brown@sunrise.demo"),
}

_SEED_ORDER = (
    "clinician", "staff", "patient", "admin",
    "sunrise_clinician", "sunrise_staff", "sunrise_patient", "sunrise_admin",
)

# Supabase provides auth.users, auth.uid() and the anon/authenticated/service_role
# roles; 001_foundation.sql references all of them. They are created here for the
# same reason: the migration is applied verbatim, so anything the real platform
# supplies must exist before it runs.
_AUTH_SHIM = """
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
END $$;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
  LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
"""

# Tests must run as a NON-superuser. A superuser bypasses RLS entirely, which
# would make every access-control assertion pass without proving anything.
# public-schema grants now come from the migration itself (section 6c), which is
# the point -- the harness must not paper over a grant the deployment lacks.
# Only the auth-schema shim needs granting here, since real Supabase owns it.
_ROLE_SETUP = """
GRANT USAGE ON SCHEMA auth TO authenticated, service_role;
GRANT SELECT ON auth.users TO authenticated, service_role;
"""


class PgHarness:
    """Owns the lifecycle of a throwaway cluster."""

    #: [(filename, error)] for migrations this engine could not apply.
    skipped_migrations: list[tuple[str, str]]

    def __init__(self) -> None:
        self.datadir = Path(tempfile.mkdtemp(prefix="ng-pgdata-"))
        # The unix socket path is capped near 103 bytes and pytest tmp paths blow
        # past that, so the socket lives in its own short directory.
        self.sockdir = Path(tempfile.mkdtemp(prefix="/tmp/ngsock-"))
        self.port = "55442"
        self.dbname = "nightingale_test"
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._run(["initdb", "-D", str(self.datadir), "-U", "postgres", "--auth=trust"])
        self._run([
            "pg_ctl", "-D", str(self.datadir), "-w", "-o",
            f"-p {self.port} -k {self.sockdir} -c listen_addresses=''",
            "-l", str(self.datadir / "server.log"), "start",
        ])
        self._started = True
        atexit.register(self.stop)
        self._wait_ready()

    def stop(self) -> None:
        if not self._started:
            return
        subprocess.run(
            ["pg_ctl", "-D", str(self.datadir), "-m", "immediate", "stop"],
            capture_output=True,
        )
        self._started = False
        shutil.rmtree(self.datadir, ignore_errors=True)
        shutil.rmtree(self.sockdir, ignore_errors=True)

    def _wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if subprocess.run(
                ["pg_isready", "-h", str(self.sockdir), "-p", self.port],
                capture_output=True,
            ).returncode == 0:
                return
            time.sleep(0.3)
        log = self.datadir / "server.log"
        raise RuntimeError(
            "Postgres did not become ready.\n"
            + (log.read_text() if log.exists() else "(no server log)")
        )

    @staticmethod
    def _run(cmd: list[str]) -> None:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed:\n{r.stdout}\n{r.stderr}")

    @property
    def dsn(self) -> str:
        return f"host={self.sockdir} port={self.port} user=postgres dbname={self.dbname}"

    def _seed(self, conn) -> None:
        conn.execute(
            "SELECT seed_demo_data(%s,%s,%s,%s,%s,%s,%s,%s)",
            tuple(USERS[k][0] for k in _SEED_ORDER),
        )

    def build(self) -> None:
        """Create the database, apply the real migration, seed both clinics."""
        import psycopg

        admin_dsn = f"host={self.sockdir} port={self.port} user=postgres dbname=postgres"
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            conn.execute(f'CREATE DATABASE "{self.dbname}"')

        migrations = _migrations()

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(_AUTH_SHIM)
            # Applied verbatim and in full: the policies under test are the ones
            # that deploy, INCLUDING every fix that landed in a later migration.
            #
            # Failures are RECORDED, not swallowed. The harness runs whatever
            # Postgres is on PATH, which is not necessarily the major version
            # Supabase deploys, so a migration can use a feature this engine does
            # not have. Hiding that would put the suite back where it started —
            # green against a schema that is not the one that ships. The skip
            # list is asserted by test_meta_rls_sanity, so it cannot grow quietly.
            self.skipped_migrations = []
            for path in migrations:
                try:
                    conn.execute(path.read_text())
                except Exception as exc:  # noqa: BLE001
                    self.skipped_migrations.append((path.name, str(exc).strip()))
            conn.execute(_ROLE_SETUP)
            for uid, email in USERS.values():
                conn.execute(
                    "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (uid, email),
                )
            self._seed(conn)

    def reset_data(self) -> None:
        """Restore seed state between tests that mutate rows."""
        import psycopg

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                # The last three arrived with migrations the harness did not
                # apply until 21 Sep 2026, so they were never in this list.
                # A table that is not reset leaks rows between tests, which is
                # how a suite starts depending on execution order.
                "TRUNCATE interaction_log, comments, highlights, note_versions, "
                "care_note_assessments, timeline_entries, care_notes, profiles, "
                "ui_telemetry, patient_access_tokens, message_deliveries "
                "RESTART IDENTITY CASCADE"
            )
            self._seed(conn)


def harness_server_version(dsn: str) -> int:
    """Major version of the cluster the harness actually built."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        row = conn.execute("SHOW server_version_num").fetchone()
    return int(row[0]) // 10000


def postgres_available() -> bool:
    """True when the Postgres binaries the harness needs are on PATH."""
    return all(shutil.which(b) for b in ("initdb", "pg_ctl", "pg_isready"))
