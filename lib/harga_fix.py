from __future__ import annotations

import csv
from pathlib import Path

from lib.config import normalize_text, resolve_harga_jual, resolve_harga_jual_lama
from lib.db import fetch_all_dict
from lib.progress import Spinner, finish_progress, show_progress

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_CSV = OUTPUT_DIR / "harga_jual_perlu_update.csv"

CSV_FIELDS = [
    "kode_barang",
    "barang_id",
    "harga_jual_pg",
    "harga_jual_benar",
    "harga_jual_lama",
]

# Kandidat: retail aktif beda dari nHrgQty01 (harga jual lama legacy).
HARGA_KANDIDAT_SQL = """
    SELECT
        TRIM(sd.kode_barang) AS kode_barang,
        s.nHrgQty01,
        sd.harga_retail,
        sd.harga_price,
        sd.harga_jual_lama_src
    FROM stock s
    INNER JOIN (
        SELECT
            cSTDfkSTK,
            MIN(TRIM(cSTDcode)) AS kode_barang,
            MAX(CASE WHEN nSTDretail > 0 THEN nSTDretail END) AS harga_retail,
            MAX(CASE WHEN nSTDprice > 0 THEN nSTDprice END) AS harga_price,
            MAX(CASE WHEN nSTDoretail > 0 THEN nSTDoretail END) AS harga_jual_lama_src
        FROM stockdetail
        WHERE cSTDcode IS NOT NULL AND TRIM(cSTDcode) <> ''
        GROUP BY cSTDfkSTK
    ) sd ON sd.cSTDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
      AND sd.harga_retail > 0
      AND s.nHrgQty01 > 0
      AND ABS(s.nHrgQty01 - sd.harga_retail) >= 1
    ORDER BY sd.kode_barang
"""


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _load_pg_barang_index(pg) -> dict[str, tuple[int, float | None]]:
    """Index kode_barang → (barang_id, harga_jual_pg)."""
    index: dict[str, tuple[int, float | None]] = {}
    with Spinner("Memuat index barang PostgreSQL..."):
        with pg.cursor() as cur:
            cur.execute(
                """
                SELECT id, kode_barang, harga_jual
                FROM barang
                WHERE deleted_at IS NULL
                """
            )
            for barang_id, kode, harga_jual in cur.fetchall():
                key = normalize_text(kode, 255)
                if key:
                    index[key] = (int(barang_id), float(harga_jual) if harga_jual is not None else None)
    return index


def export_harga_jual_fix(mysql, pg, *, csv_path: Path | None = None) -> dict:
    """
    Export barang yang harga_jual di PostgreSQL masih salah (≠ nSTDretail).
    Hanya baca ~21k kandidat dari MySQL, bukan full 725k.
    """
    stats = {
        "kandidat_mysql": 0,
        "exported": 0,
        "sudah_benar": 0,
        "tidak_ada_di_pg": 0,
        "csv_path": "",
    }

    path = csv_path or DEFAULT_CSV
    _ensure_output_dir()

    with Spinner("Membaca kandidat harga dari MySQL (~21k)..."):
        mysql_rows = fetch_all_dict(mysql, HARGA_KANDIDAT_SQL)
    stats["kandidat_mysql"] = len(mysql_rows)

    pg_index = _load_pg_barang_index(pg)
    export_rows: list[dict] = []
    total = len(mysql_rows)

    for i, row in enumerate(mysql_rows, 1):
        if i % 500 == 0 or i == total:
            show_progress(i, total, f"exported {len(export_rows):,}")

        kode = normalize_text(row.get("kode_barang"), 255)
        if not kode:
            continue

        harga_benar = resolve_harga_jual(row)
        if harga_benar is None:
            continue

        pg_entry = pg_index.get(kode)
        if not pg_entry:
            stats["tidak_ada_di_pg"] += 1
            continue

        barang_id, harga_pg = pg_entry
        if harga_pg is not None and abs(harga_pg - harga_benar) < 0.01:
            stats["sudah_benar"] += 1
            continue

        export_rows.append(
            {
                "kode_barang": kode,
                "barang_id": barang_id,
                "harga_jual_pg": harga_pg if harga_pg is not None else "",
                "harga_jual_benar": harga_benar,
                "harga_jual_lama": resolve_harga_jual_lama(row) or row.get("nHrgQty01") or "",
            }
        )

    finish_progress()
    _write_csv(path, export_rows)
    stats["exported"] = len(export_rows)
    stats["csv_path"] = str(path)
    return stats


def apply_harga_jual_fix(pg, *, csv_path: Path, dry_run: bool, user_id: int) -> dict:
    """Update harga_jual di PostgreSQL hanya untuk baris di CSV."""
    stats = {"read": 0, "updated": 0, "skipped": 0, "errors": 0}

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV tidak ditemukan: {csv_path}")

    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    total = len(rows)
    try:
        for i, row in enumerate(rows, 1):
            stats["read"] += 1
            if i % 100 == 0 or i == total:
                show_progress(i, total, f"updated {stats['updated']:,}")

            try:
                barang_id = int(row["barang_id"])
                harga_benar = float(row["harga_jual_benar"])
            except (KeyError, TypeError, ValueError):
                stats["skipped"] += 1
                continue

            harga_lama_raw = row.get("harga_jual_lama", "")
            harga_jual_lama = float(harga_lama_raw) if str(harga_lama_raw).strip() else None

            if dry_run:
                stats["updated"] += 1
                continue

            with pg.cursor() as cur:
                cur.execute(
                    """
                    UPDATE barang
                    SET harga_jual = %s,
                        harga_jual_lama = %s,
                        updated_by = %s,
                        updated_at = NOW()
                    WHERE id = %s AND deleted_at IS NULL
                    """,
                    (harga_benar, harga_jual_lama, user_id, barang_id),
                )
                if cur.rowcount:
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1

        if not dry_run:
            pg.commit()
    except Exception:
        if not dry_run:
            pg.rollback()
        raise

    finish_progress()
    return stats
