"""Revert koreksi stok S2/S3 — kembalikan stok_akhir gudang ke nilai sebelum koreksi."""

from __future__ import annotations

import csv
from pathlib import Path

from lib.db import fetch_one
from lib.progress import finish_progress, show_progress
from lib.stok_fix import BATCH_SIZE

from reconcile_lib.stok_apply_query import KETERANGAN

FEATURE_OUTPUT = Path(__file__).resolve().parent.parent / "output"

FETCH_LEDGER_SQL = """
SELECT
    s.id AS ledger_id,
    s.barang_id,
    s.gudang_id,
    s.stok_sebelum,
    s.stok_akhir AS stok_target,
    b.kode_barang,
    sa.id AS stok_akhir_id
FROM stok s
JOIN barang b ON b.id = s.barang_id
LEFT JOIN stok_akhir sa
       ON sa.barang_id = s.barang_id
      AND sa.gudang_id = s.gudang_id
      AND sa.deleted_at IS NULL
WHERE s.keterangan = %s
ORDER BY s.id
"""


def _load_preview_aksi(path: Path, skenario: str) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("skenario") == skenario and row.get("kode_barang"):
                out[row["kode_barang"]] = row.get("aksi", "update")
    return out


def fetch_ledger_rows(pg, skenario: str) -> list[dict]:
    ket = KETERANGAN.format(skenario=skenario)
    with pg.cursor() as cur:
        cur.execute(FETCH_LEDGER_SQL, (ket,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def run_revert_skenario(
    pg,
    skenario: str,
    *,
    user_id: int = 1,
    dry_run: bool = True,
    preview_csv: Path | None = None,
    batch_size: int = BATCH_SIZE,
    revert_barang: bool = True,
) -> dict:
    db_name = fetch_one(pg, "SELECT current_database()")
    rows = fetch_ledger_rows(pg, skenario)
    aksi_map = _load_preview_aksi(
        preview_csv or FEATURE_OUTPUT / "stok_apply_41k_preview.csv",
        skenario,
    )

    stats = {
        "db": db_name,
        "skenario": skenario,
        "dry_run": dry_run,
        "ledger": len(rows),
        "restore_gudang": 0,
        "delete_stok_akhir": 0,
        "restore_barang": 0,
        "delete_ledger": 0,
    }

    if not rows:
        return stats

    total = len(rows)
    processed = 0
    label = f"revert {skenario}"

    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start: batch_start + batch_size]
        if dry_run:
            for row in batch:
                aksi = aksi_map.get(row["kode_barang"], "update")
                stats["restore_gudang"] += 1
                if aksi == "insert" and int(row["stok_sebelum"] or 0) == 0:
                    stats["delete_stok_akhir"] += 1
                if revert_barang:
                    stats["restore_barang"] += 1
                stats["delete_ledger"] += 1
                processed += 1
        else:
            try:
                with pg.cursor() as cur:
                    for row in batch:
                        bid = row["barang_id"]
                        gid = row["gudang_id"]
                        sebelum = int(row["stok_sebelum"] or 0)
                        target = int(row["stok_target"] or 0)
                        delta = target - sebelum
                        aksi = aksi_map.get(row["kode_barang"], "update")

                        if aksi == "insert" and sebelum == 0 and row.get("stok_akhir_id"):
                            cur.execute(
                                """
                                DELETE FROM stok_akhir
                                WHERE id = %s AND barang_id = %s AND gudang_id = %s
                                """,
                                (row["stok_akhir_id"], bid, gid),
                            )
                            stats["delete_stok_akhir"] += 1
                        elif row.get("stok_akhir_id"):
                            cur.execute(
                                """
                                UPDATE stok_akhir
                                SET stok_akhir = %s, updated_by = %s, updated_at = NOW()
                                WHERE id = %s
                                """,
                                (sebelum, user_id, row["stok_akhir_id"]),
                            )
                            stats["restore_gudang"] += 1

                        if revert_barang and delta != 0:
                            cur.execute(
                                """
                                UPDATE barang
                                SET stok_akhir = COALESCE(stok_akhir, 0) - %s,
                                    updated_by = %s,
                                    updated_at = NOW()
                                WHERE id = %s
                                """,
                                (delta, user_id, bid),
                            )
                            stats["restore_barang"] += 1

                        cur.execute("DELETE FROM stok WHERE id = %s", (row["ledger_id"],))
                        stats["delete_ledger"] += 1
                        processed += 1
                pg.commit()
            except Exception:
                pg.rollback()
                raise

        show_progress(processed, total, label)

    finish_progress()
    return stats


def run_revert_s2_s3(
    pg,
    *,
    user_id: int = 1,
    dry_run: bool = True,
    preview_csv: Path | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict:
    s2 = run_revert_skenario(
        pg, "S2", user_id=user_id, dry_run=dry_run,
        preview_csv=preview_csv, batch_size=batch_size,
    )
    s3 = run_revert_skenario(
        pg, "S3", user_id=user_id, dry_run=dry_run,
        preview_csv=preview_csv, batch_size=batch_size,
    )
    return {"s2": s2, "s3": s3}
