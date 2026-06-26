"""
Sinkronkan stok_opname_detail.stok_sistem_snapshot setelah koreksi stok.

Match barang via kode_barang_snapshot (dari file selisih koreksi).
Nilai baru = COALESCE(stok_akhir.stok_akhir, barang.stok_akhir) di gudang opname.
"""
from __future__ import annotations

import csv
from pathlib import Path

from lib.db import fetch_one
from lib.progress import Spinner, show_progress, finish_progress

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
BATCH_SIZE = 200

PREVIEW_FIELDS = [
    "stok_opname_detail_id",
    "no_opname",
    "kode_barang_snapshot",
    "nama_barang_snapshot",
    "snapshot_lama",
    "snapshot_baru",
    "stok_fisik",
    "delta",
]


def load_koreksi_kodes(selisih_file: Path) -> list[str]:
    from lib.stok_fix import load_koreksi_rows
    rows = load_koreksi_rows(selisih_file)
    return [r["kode_barang"] for r in rows if r.get("kode_barang")]


def fetch_mismatch_rows(pg, kodes: list[str], gudang_id: int) -> list[dict]:
    """Baris detail opname scanning yang snapshot != stok gudang sekarang."""
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                sod.id              AS detail_id,
                so.no_opname,
                sod.kode_barang_snapshot,
                sod.nama_barang_snapshot,
                sod.stok_sistem_snapshot AS snapshot_lama,
                COALESCE(sa.stok_akhir, b.stok_akhir::int) AS snapshot_baru,
                sod.stok_fisik
            FROM stok_opname_detail sod
            JOIN stok_opname so ON so.id = sod.stok_opname_id
                               AND so.deleted_at IS NULL
                               AND so.status = 'scanning'
                               AND so.gudang_id = %s
            JOIN barang b ON b.id = sod.barang_id AND b.deleted_at IS NULL
            LEFT JOIN stok_akhir sa ON sa.barang_id = b.id
                                   AND sa.gudang_id = so.gudang_id
                                   AND sa.deleted_at IS NULL
            WHERE sod.kode_barang_snapshot = ANY(%s)
              AND sod.stok_sistem_snapshot IS DISTINCT FROM
                  COALESCE(sa.stok_akhir, b.stok_akhir::int)
            ORDER BY so.id, sod.kode_barang_snapshot
            """,
            (gudang_id, kodes),
        )
        rows = []
        for detail_id, no_opname, kode, nama, lama, baru, fisik in cur.fetchall():
            rows.append({
                "stok_opname_detail_id": detail_id,
                "no_opname": no_opname,
                "kode_barang_snapshot": kode,
                "nama_barang_snapshot": nama or "",
                "snapshot_lama": int(lama),
                "snapshot_baru": int(baru),
                "stok_fisik": int(fisik or 0),
                "delta": int(baru) - int(lama),
            })
    return rows


def run_opname_snapshot_fix(
    pg,
    *,
    selisih_file: Path,
    gudang: str = "pusat",
    dry_run: bool = True,
    out_preview: Path | None = None,
    allow_production: bool = False,
) -> dict:
    from lib.stok_trx_check import resolve_gudang_id

    db_name = fetch_one(pg, "SELECT current_database()")
    if db_name == "matahari-production" and not allow_production:
        raise RuntimeError(
            f"DB target adalah '{db_name}' (production). "
            "Tambahkan --allow-production untuk melanjutkan."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path_in = selisih_file.expanduser().resolve()
    if not path_in.is_file():
        raise FileNotFoundError(path_in)

    gudang_id, gudang_name = resolve_gudang_id(pg, gudang)

    with Spinner(f"Membaca kode koreksi dari {path_in.name} ..."):
        kodes = load_koreksi_kodes(path_in)

    with Spinner(f"Query mismatch snapshot ({len(kodes)} kode) ..."):
        mismatch_rows = fetch_mismatch_rows(pg, kodes, gudang_id)

    preview_path = out_preview or OUTPUT_DIR / "stok_opname_snapshot_preview.csv"
    with preview_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PREVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(mismatch_rows)

    stats = {
        "db": db_name,
        "gudang": gudang_name,
        "kode_koreksi": len(kodes),
        "perlu_update": len(mismatch_rows),
        "dry_run": dry_run,
        "preview_csv": str(preview_path),
    }

    if dry_run:
        return stats

    total = len(mismatch_rows)
    applied = 0
    errors: list[str] = []

    for batch_start in range(0, total, BATCH_SIZE):
        batch = mismatch_rows[batch_start: batch_start + BATCH_SIZE]
        try:
            with pg.cursor() as cur:
                for row in batch:
                    cur.execute(
                        """
                        UPDATE stok_opname_detail
                        SET stok_sistem_snapshot = %s, updated_at = NOW()
                        WHERE id = %s
                        """,
                        (row["snapshot_baru"], row["stok_opname_detail_id"]),
                    )
                    applied += 1
            pg.commit()
        except Exception as exc:
            pg.rollback()
            errors.append(f"batch {batch_start}: {exc}")
            raise

        show_progress(applied, total, f"updated={applied}")

    finish_progress()
    stats["applied"] = applied
    stats["errors"] = errors
    return stats


def verify_opname_snapshot_fix(
    pg,
    *,
    selisih_file: Path,
    gudang: str = "pusat",
    out_sisa: Path | None = None,
) -> dict:
    from lib.stok_trx_check import resolve_gudang_id

    path_in = selisih_file.expanduser().resolve()
    gudang_id, gudang_name = resolve_gudang_id(pg, gudang)

    with Spinner("Verifikasi snapshot opname ..."):
        kodes = load_koreksi_kodes(path_in)
        sisa = fetch_mismatch_rows(pg, kodes, gudang_id)

    if sisa and out_sisa:
        out_sisa.parent.mkdir(parents=True, exist_ok=True)
        with out_sisa.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PREVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(sisa)

    return {
        "gudang": gudang_name,
        "kode_koreksi": len(kodes),
        "mismatch": len(sisa),
        "ok": len(sisa) == 0,
        "sisa_file": str(out_sisa) if sisa and out_sisa else None,
    }
