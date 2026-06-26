"""Apply koreksi stok skenario 1/2/3 ke DB."""

from __future__ import annotations

import csv
from pathlib import Path

import psycopg2

from lib.db import connect_pg, fetch_one
from lib.progress import Spinner, finish_progress, show_progress
from lib.stok_fix import BATCH_SIZE, fetch_barang_stok_map

from reconcile_lib.stok_apply_query import KETERANGAN, fetch_all_apply_rows

FEATURE_OUTPUT = Path(__file__).resolve().parent.parent / "output"

PREVIEW_FIELDS = [
    "skenario",
    "kode_barang",
    "nama_barang",
    "stok_sebelum",
    "stok_target",
    "delta",
    "aksi",
    "catatan",
]


def _apply_one_labeled(pg, row: dict, gudang_id: int, user_id: int) -> None:
    skenario = row.get("skenario", "?")
    row = dict(row)
    row["_keterangan"] = KETERANGAN.format(skenario=skenario)
    with pg.cursor() as cur:
        bid = row["barang_id"]
        target = row["stok_target"]
        sebelum = row["stok_sebelum"]
        harga = row["harga_satuan"]
        delta = target - sebelum
        jenis = "masuk" if delta >= 0 else "keluar"
        masuk = delta if delta > 0 else 0
        keluar = abs(delta) if delta < 0 else 0
        ket = row.get("_keterangan", KETERANGAN.format(skenario=skenario))

        cur.execute(
            """
            INSERT INTO stok (
                barang_id, gudang_id, keterangan,
                stok_sebelum, stok_masuk, stok_keluar, stok_akhir,
                jenis, harga_satuan, created_by, updated_by, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (bid, gudang_id, ket, sebelum, masuk, keluar, target, jenis, harga, user_id, user_id),
        )
        if row["aksi"] == "update":
            cur.execute(
                """
                UPDATE stok_akhir
                SET stok_akhir = %s, updated_by = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (target, user_id, row["stok_akhir_id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO stok_akhir (
                    barang_id, gudang_id, stok_akhir, harga_satuan,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (bid, gudang_id, target, harga, user_id, user_id),
            )
        # Koreksi 41k hanya stok_akhir per gudang — jangan ubah barang.stok_akhir (agregat multi-gudang)


def _write_preview(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PREVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_preview(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _fetch_applied_barang_ids(pg) -> set[int]:
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT barang_id
            FROM stok
            WHERE keterangan LIKE %s
            """,
            (f"{KETERANGAN.split('{')[0]}%",),
        )
        return {int(row[0]) for row in cur.fetchall()}


def _enrich_preview_rows(pg, rows: list[dict], gudang_id: int) -> list[dict]:
    kodes = list({r["kode_barang"] for r in rows if r.get("kode_barang")})
    meta = fetch_barang_stok_map(pg, kodes, gudang_id) if kodes else {}
    out: list[dict] = []
    for row in rows:
        kode = row.get("kode_barang")
        info = meta.get(kode)
        if not info:
            continue
        enriched = dict(row)
        enriched.update(
            barang_id=int(info["barang_id"]),
            stok_akhir_id=info.get("stok_akhir_id"),
            harga_satuan=float(info.get("harga_satuan") or 0),
            stok_sebelum=int(row.get("stok_sebelum") or info.get("stok_sebelum") or 0),
            stok_target=int(row.get("stok_target") or 0),
        )
        out.append(enriched)
    return out


def _apply_batches(
    pg,
    pg_cfg: dict,
    apply_rows: list[dict],
    *,
    gudang_id: int,
    user_id: int,
    batch_size: int,
    inner_stats: dict,
) -> int:
    total = len(apply_rows)
    applied = 0
    conn = pg
    for batch_start in range(0, total, batch_size):
        batch = apply_rows[batch_start: batch_start + batch_size]
        for attempt in range(3):
            try:
                for row in batch:
                    _apply_one_labeled(conn, row, gudang_id, user_id)
                    applied += 1
                conn.commit()
                break
            except psycopg2.OperationalError:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 2:
                    raise
                conn = connect_pg(pg_cfg)
            except Exception:
                conn.rollback()
                raise
        show_progress(
            applied, total,
            f"S1={inner_stats.get('s1_apply', 0)} "
            f"S2={inner_stats.get('s2_apply', 0)} "
            f"S3={inner_stats.get('s3_apply', 0)}",
        )
    finish_progress()
    return applied


def run_stok_apply_41k(
    pg,
    *,
    names: list[str],
    stoks: list[int],
    gudang_id: int,
    gudang_name: str,
    since,
    user_id: int = 1,
    dry_run: bool = True,
    out_preview: Path | None = None,
    allow_production: bool = False,
    from_preview: Path | None = None,
    resume: bool = False,
    batch_size: int = BATCH_SIZE,
    pg_cfg: dict | None = None,
) -> dict:
    db_name = fetch_one(pg, "SELECT current_database()")
    if db_name == "matahari-production" and not allow_production:
        raise RuntimeError(
            f"DB target '{db_name}' adalah production. Tambahkan --allow-production."
        )

    if from_preview:
        with Spinner(f"Muat preview: {from_preview.name} ..."):
            preview_rows = _load_preview(from_preview)
            apply_rows = _enrich_preview_rows(pg, preview_rows, gudang_id)
        inner_stats = {
            "s1_kandidat": sum(1 for r in apply_rows if r.get("skenario") == "S1"),
            "s1_apply": sum(1 for r in apply_rows if r.get("skenario") == "S1"),
            "s2_apply": sum(1 for r in apply_rows if r.get("skenario") == "S2"),
            "s3_apply": sum(1 for r in apply_rows if r.get("skenario") == "S3"),
            "total_apply": len(apply_rows),
            "since_date": since.isoformat() if since else "",
            "from_preview": str(from_preview),
        }
    else:
        with Spinner("Kumpulkan baris apply S1/S2/S3 ..."):
            apply_rows, inner_stats = fetch_all_apply_rows(
                pg, names=names, stoks=stoks, gudang_id=gudang_id, since=since,
            )

    if resume:
        applied_ids = _fetch_applied_barang_ids(pg)
        before = len(apply_rows)
        apply_rows = [r for r in apply_rows if int(r["barang_id"]) not in applied_ids]
        inner_stats["skipped_resume"] = before - len(apply_rows)
        inner_stats["total_apply"] = len(apply_rows)

    preview_path = out_preview or FEATURE_OUTPUT / "stok_apply_41k_preview.csv"
    if not from_preview:
        _write_preview(preview_path, apply_rows)

    stats = {
        "db": db_name,
        "gudang": gudang_name,
        "dry_run": dry_run,
        "preview_csv": str(preview_path),
        **inner_stats,
        "update": sum(1 for r in apply_rows if r["aksi"] == "update"),
        "insert": sum(1 for r in apply_rows if r["aksi"] == "insert"),
    }

    if dry_run:
        return stats

    if not pg_cfg:
        raise RuntimeError("pg_cfg wajib untuk apply (reconnect saat tunnel putus).")

    stats["applied"] = _apply_batches(
        pg,
        pg_cfg,
        apply_rows,
        gudang_id=gudang_id,
        user_id=user_id,
        batch_size=batch_size,
        inner_stats=inner_stats,
    )
    return stats
