"""
A Supabase-client-shaped facade over psycopg, scoped to one role.

The five micro-test suites were written against supabase-py's fluent builder
(`client.table(...).select(...).eq(...).execute().data`). Rather than rewrite
every assertion — and risk quietly weakening what they check — this reproduces
the slice of that API the suites use, backed by the ephemeral Postgres cluster.

Crucially it does NOT bypass RLS. Every statement runs in a transaction that
first does:

    SET LOCAL ROLE authenticated;
    SET LOCAL request.jwt.claim.sub = '<user id>';

so the policies in 001_foundation.sql evaluate exactly as they do in production
against a Supabase JWT. A denied read returns no rows; a denied write raises
postgrest.exceptions.APIError, which is what the suites already expect.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from postgrest.exceptions import APIError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Response:
    """Mirrors supabase-py's APIResponse: carries `.data` and optional `.count`."""

    def __init__(
        self,
        data: list[dict[str, Any]] | dict[str, Any] | None,
        count: int | None = None,
    ):
        self.data = data
        self.count = count

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Response data={self.data!r}>"


class _AuthUser:
    def __init__(self, user_id: str):
        self.id = user_id


class _AuthResult:
    def __init__(self, user_id: str):
        self.user = _AuthUser(user_id)


class _Auth:
    def __init__(self, user_id: str):
        self._user_id = user_id

    def get_user(self) -> _AuthResult:
        return _AuthResult(self._user_id)


# Columns that are jsonb in the schema; dicts bound to them need explicit
# adaptation or psycopg cannot infer the type.
_JSONB_COLUMNS = {
    "content", "provenance_pointer", "metadata", "glance_cache",
    "target_metadata", "anchor_data", "content_snapshot", "settings",
}


class QueryBuilder:
    """Accumulates a query, then runs it under the caller's role on execute()."""

    def __init__(self, client: "PgClient", table: str):
        self._client = client
        self._table = table
        self._op = "select"
        self._columns = "*"
        self._filters: list[tuple[str, Any]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._single = False
        self._count_mode: str | None = None
        self._payload: dict[str, Any] | list[dict[str, Any]] | None = None

    # -- builder -----------------------------------------------------------

    def select(self, columns: str = "*", count: str | None = None, **_: Any) -> "QueryBuilder":
        # PostgREST embed syntax (e.g. "*, author:profiles!fk(*)") has no direct
        # SQL equivalent here; the base columns are what the assertions read.
        self._columns = "*" if "(" in columns else columns
        self._count_mode = count
        if self._op != "insert":
            self._op = "select"
        return self

    def insert(self, payload: dict[str, Any] | list[dict[str, Any]], **_: Any) -> "QueryBuilder":
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any], **_: Any) -> "QueryBuilder":
        self._op = "update"
        self._payload = payload
        return self

    def delete(self, **_: Any) -> "QueryBuilder":
        self._op = "delete"
        return self

    def eq(self, column: str, value: Any) -> "QueryBuilder":
        self._filters.append((column, value))
        return self

    def order(self, column: str, desc: bool = False, **_: Any) -> "QueryBuilder":
        self._order.append((column, desc))
        return self

    def limit(self, count: int, **_: Any) -> "QueryBuilder":
        self._limit = count
        return self

    def single(self) -> "QueryBuilder":
        self._single = True
        self._limit = self._limit or 1
        return self

    maybe_single = single

    # -- execution ---------------------------------------------------------

    def _where(self) -> tuple[str, list[Any]]:
        if not self._filters:
            return "", []
        clauses = " AND ".join(f'"{c}" = %s' for c, _ in self._filters)
        return f" WHERE {clauses}", [v for _, v in self._filters]

    @staticmethod
    def _bind(column: str, value: Any) -> Any:
        if isinstance(value, (dict, list)) and column in _JSONB_COLUMNS:
            return Jsonb(value)
        if isinstance(value, dict):
            return Jsonb(value)
        return value

    def _sql(self) -> tuple[str, list[Any]]:
        where, wargs = self._where()

        if self._op == "select":
            sql = f'SELECT {self._columns} FROM "{self._table}"{where}'
            if self._order:
                sql += " ORDER BY " + ", ".join(
                    f'"{c}" {"DESC" if d else "ASC"}' for c, d in self._order
                )
            if self._limit is not None:
                sql += f" LIMIT {int(self._limit)}"
            return sql, wargs

        if self._op == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            rows = [r for r in rows if r is not None]
            if not rows:
                return "", []
            cols = list(rows[0].keys())
            placeholders = ", ".join(
                "(" + ", ".join(["%s"] * len(cols)) + ")" for _ in rows
            )
            args: list[Any] = []
            for r in rows:
                args.extend(self._bind(c, r.get(c)) for c in cols)
            collist = ", ".join(f'"{c}"' for c in cols)
            return (
                f'INSERT INTO "{self._table}" ({collist}) VALUES {placeholders} RETURNING *',
                args,
            )

        if self._op == "update":
            assert isinstance(self._payload, dict)
            cols = list(self._payload.keys())
            setlist = ", ".join(f'"{c}" = %s' for c in cols)
            args = [self._bind(c, self._payload[c]) for c in cols] + wargs
            return f'UPDATE "{self._table}" SET {setlist}{where} RETURNING *', args

        if self._op == "delete":
            return f'DELETE FROM "{self._table}"{where} RETURNING *', wargs

        raise ValueError(f"Unsupported operation {self._op}")

    def execute(self) -> Response:
        sql, args = self._sql()
        if not sql:
            return Response([])
        rows = self._client._run(sql, args)

        total: int | None = None
        if self._count_mode:
            # PostgREST's exact count ignores LIMIT, so ask separately.
            where, wargs = self._where()
            total_rows = self._client._run(
                f'SELECT count(*) AS n FROM "{self._table}"{where}', wargs
            )
            total = int(total_rows[0]["n"]) if total_rows else 0

        if self._single:
            if not rows:
                # supabase-py raises when .single() matches nothing.
                raise APIError({
                    "message": "JSON object requested, multiple (or no) rows returned",
                    "code": "PGRST116",
                })
            return Response(rows[0], total)
        return Response(rows, total)


class PgClient:
    """A database handle bound to one user id, subject to RLS."""

    def __init__(self, dsn: str, user_id: str | None, *, service_role: bool = False):
        self._dsn = dsn
        self._user_id = user_id
        self._service_role = service_role
        self.auth = _Auth(user_id or "")

    def table(self, name: str) -> QueryBuilder:
        return QueryBuilder(self, name)

    from_ = table

    def _run(self, sql: str, args: list[Any]) -> list[dict[str, Any]]:
        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    if not self._service_role:
                        # Drop to a non-superuser and adopt the caller's identity
                        # so RLS applies. SET LOCAL scopes both to this
                        # transaction only.
                        cur.execute("SET LOCAL ROLE authenticated")
                        cur.execute(
                            "SELECT set_config('request.jwt.claim.sub', %s, true)",
                            (self._user_id or "",),
                        )
                    cur.execute(sql, args)
                    rows = cur.fetchall() if cur.description else []
                    conn.commit()
                    return [self._normalise(r) for r in rows]
        except psycopg.errors.InsufficientPrivilege as exc:
            # RLS denial on a write. The suites assert on APIError.
            raise APIError({
                "message": str(exc),
                "code": "42501",
                "details": "new row violates row-level security policy",
            }) from exc
        except psycopg.Error as exc:
            raise APIError({
                "message": str(exc),
                "code": getattr(exc, "sqlstate", None) or "P0000",
            }) from exc

    @staticmethod
    def _normalise(row: dict[str, Any]) -> dict[str, Any]:
        """Match PostgREST's JSON shape: uuids/timestamps as strings."""
        out: dict[str, Any] = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                out[k] = v.isoformat()
            elif isinstance(v, (dict, list)) or v is None:
                out[k] = v
            elif type(v).__name__ == "UUID":
                out[k] = str(v)
            else:
                out[k] = v
        return out
