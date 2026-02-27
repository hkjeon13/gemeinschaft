#!/usr/bin/env python3
"""Simple SQL migration runner for Postgres."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

MIGRATION_FILE_RE = re.compile(
    r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.(?P<direction>up|down)\.sql$"
)


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up_path: Path
    down_path: Path


def discover_migrations(migrations_dir: Path) -> list[Migration]:
    grouped: dict[str, dict[str, Path | str]] = {}
    for path in migrations_dir.glob("*.sql"):
        match = MIGRATION_FILE_RE.match(path.name)
        if not match:
            continue
        version = match.group("version")
        entry = grouped.setdefault(version, {"name": match.group("name")})
        expected_name = entry["name"]
        current_name = match.group("name")
        if expected_name != current_name:
            raise ValueError(
                f"Migration version {version} has inconsistent names: "
                f"{expected_name!r} vs {current_name!r}"
            )
        direction = match.group("direction")
        if direction in entry:
            raise ValueError(f"Duplicate migration file for {version}:{direction}")
        entry[direction] = path

    migrations: list[Migration] = []
    for version in sorted(grouped):
        item = grouped[version]
        up_path = item.get("up")
        down_path = item.get("down")
        if up_path is None or down_path is None:
            raise ValueError(f"Migration {version} must include both up/down SQL files")
        migrations.append(
            Migration(
                version=version,
                name=str(item["name"]),
                up_path=Path(up_path),
                down_path=Path(down_path),
            )
        )
    return migrations


def load_psycopg() -> Any:
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        raise RuntimeError(
            "psycopg is required for migrations. Install with: "
            "python -m pip install -e \".[dev]\""
        ) from exc
    return psycopg


def connect(database_url: str) -> Any:
    psycopg = load_psycopg()
    return psycopg.connect(database_url)


def ensure_migration_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(MIGRATION_TABLE_SQL)
    conn.commit()


def applied_versions(conn: Any) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        rows = cur.fetchall()
    return [row[0] for row in rows]


def apply_sql(conn: Any, sql_path: Path) -> None:
    sql_text = sql_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql_text)


def migrate_up(conn: Any, migrations: list[Migration], steps: int | None) -> int:
    applied = set(applied_versions(conn))
    pending = [m for m in migrations if m.version not in applied]
    if steps is not None:
        pending = pending[:steps]

    for migration in pending:
        print(f"[migrate] applying {migration.version}_{migration.name}")
        try:
            apply_sql(conn, migration.up_path)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO schema_migrations (version, name)
                    VALUES (%s, %s)
                    """,
                    (migration.version, migration.name),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(pending)


def migrate_down(conn: Any, migrations: list[Migration], steps: int) -> int:
    if steps < 1:
        raise ValueError("--steps for down must be >= 1")

    version_map = {migration.version: migration for migration in migrations}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT version
            FROM schema_migrations
            ORDER BY version DESC
            LIMIT %s
            """,
            (steps,),
        )
        rows = cur.fetchall()

    versions = [row[0] for row in rows]
    if not versions:
        return 0

    for version in versions:
        migration = version_map.get(version)
        if migration is None:
            raise RuntimeError(
                f"Applied migration {version} is missing from db/migrations files"
            )
        print(f"[migrate] reverting {migration.version}_{migration.name}")
        try:
            apply_sql(conn, migration.down_path)
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM schema_migrations WHERE version = %s",
                    (migration.version,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(versions)


def print_status(conn: Any, migrations: list[Migration]) -> None:
    applied = set(applied_versions(conn))
    for migration in migrations:
        marker = "applied" if migration.version in applied else "pending"
        print(f"{migration.version}_{migration.name}: {marker}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Postgres SQL migrations.")
    parser.add_argument(
        "command",
        choices=["up", "down", "status"],
        help="Migration operation to run",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Number of migration steps (default: all for up, 1 for down)",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Postgres connection URL (falls back to DATABASE_URL env var)",
    )
    parser.add_argument(
        "--migrations-dir",
        default="db/migrations",
        help="Path to migration SQL files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise RuntimeError("DATABASE_URL is required (via env or --database-url).")

    migrations = discover_migrations(Path(args.migrations_dir))
    conn = connect(args.database_url)
    try:
        ensure_migration_table(conn)
        if args.command == "up":
            applied_count = migrate_up(conn, migrations, args.steps)
            print(f"[migrate] applied {applied_count} migration(s)")
        elif args.command == "down":
            steps = 1 if args.steps is None else args.steps
            reverted_count = migrate_down(conn, migrations, steps)
            print(f"[migrate] reverted {reverted_count} migration(s)")
        else:
            print_status(conn, migrations)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
