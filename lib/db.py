from __future__ import annotations

import pymysql
import psycopg2
import psycopg2.extras


def connect_mysql(cfg: dict):
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def connect_pg(cfg: dict):
    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        dbname=cfg["database"],
    )
    conn.autocommit = False
    return conn


def ensure_id_map_table(pg) -> None:
    with pg.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_id_map (
                entity_type VARCHAR(50) NOT NULL,
                legacy_pk VARCHAR(100) NOT NULL,
                new_id BIGINT NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (entity_type, legacy_pk)
            )
            """
        )
    pg.commit()


def lookup_id_map(pg, entity_type: str, legacy_pk: str | None) -> int | None:
    if not legacy_pk or not str(legacy_pk).strip():
        return None

    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT new_id FROM migration_id_map
            WHERE entity_type = %s AND legacy_pk = %s
            """,
            (entity_type, legacy_pk.strip()),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def save_id_map(pg, entity_type: str, legacy_pk: str, new_id: int) -> None:
    with pg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO migration_id_map (entity_type, legacy_pk, new_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (entity_type, legacy_pk) DO UPDATE SET new_id = EXCLUDED.new_id
            """,
            (entity_type, legacy_pk, new_id),
        )


def fetch_one(pg, query: str, params=()):
    with pg.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return row[0] if row else None


def fetch_all_dict(mysql, query: str, params=None):
    with mysql.cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchall()


def truncate_master_data(pg, entities: list[str], *, dry_run: bool) -> None:
    """Hapus data master PostgreSQL sebelum migrasi ulang."""
    from config.mapping import SKIP_TRUNCATE, TRUNCATE_ORDER, TRUNCATE_TABLES
    from lib.progress import Spinner

    tables: list[str] = []
    map_types: list[str] = []

    for entity in TRUNCATE_ORDER:
        if entity not in entities:
            continue
        map_types.append(entity)
        if entity in SKIP_TRUNCATE:
            print(f"  [skip] {entity}: tidak dihapus (terhubung ke users / config web)", flush=True)
            continue
        tables.append(TRUNCATE_TABLES[entity])

    if not tables and not map_types:
        return

    if dry_run:
        if tables:
            print(f"  [dry-run] TRUNCATE: {', '.join(tables)} RESTART IDENTITY CASCADE", flush=True)
        if map_types:
            print(f"  [dry-run] DELETE migration_id_map untuk: {', '.join(map_types)}", flush=True)
        return

    truncate_msg = f"Menghapus {', '.join(tables)} ..."
    if tables:
        truncate_msg += " (tutup DBeaver/pgAdmin jika lama)"

    old_autocommit = pg.autocommit
    try:
        with Spinner(truncate_msg):
            pg.autocommit = True
            with pg.cursor() as cur:
                if tables:
                    sql = f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"
                    cur.execute(sql)
                if map_types:
                    cur.execute(
                        "DELETE FROM migration_id_map WHERE entity_type = ANY(%s)",
                        (map_types,),
                    )
    finally:
        pg.autocommit = old_autocommit

    if tables:
        print(f"  ✓ Dihapus: {', '.join(tables)}", flush=True)
    if map_types:
        print(f"  ✓ ID map dibersihkan untuk: {', '.join(map_types)}", flush=True)
