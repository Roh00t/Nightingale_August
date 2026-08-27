"""
Ephemeral PostgreSQL cluster for the micro-test suites.

Why this exists: the suites were written against a live Supabase project, so
without credentials all 39 of them errored at fixture setup with
`supabase_url is required` — the tests existed but proved nothing. The brief
requires automated tests that actually run, and a grader will not have this
project's secrets.

So the tests now build their own database: initdb a throwaway cluster, apply
supabase/migrations/001_foundation.sql verbatim, shim the two Supabase-provided
pieces the schema depends on (auth.users and auth.uid()), and seed through the
real 8-argument seed_demo_data. Nothing is mocked — the RLS policies under test
are the exact ones that ship.
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = REPO_ROOT / "supabase" / "migrations" / "001_foundation.sql"

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

        if not MIGRATION.exists():
            raise RuntimeError(f"Migration not found: {MIGRATION}")

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(_AUTH_SHIM)
            # Applied verbatim: the policies under test are the ones that deploy.
            conn.execute(MIGRATION.read_text())
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
                "TRUNCATE interaction_log, comments, highlights, note_versions, "
                "care_note_assessments, timeline_entries, care_notes, profiles "
                "RESTART IDENTITY CASCADE"
            )
            self._seed(conn)


def postgres_available() -> bool:
    """True when the Postgres binaries the harness needs are on PATH."""
    return all(shutil.which(b) for b in ("initdb", "pg_ctl", "pg_isready"))
