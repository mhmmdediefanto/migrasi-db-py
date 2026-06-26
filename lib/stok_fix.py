"""
Koreksi stok Gudang Pusat (Sragen) — Strategi B (rekonsiliasi).

stok_target = stok_excel
            + net_pembelian_pusat (tanggal >= since, gudang_id=1)
            - net_penjualan       (tanggal >= since, semua gudang)
            + retur_pembelian_pusat - retur_penjualan

Tabel yang diubah (urutan):
  1. stok        — INSERT ledger koreksi
  2. stok_akhir  — UPDATE existing / INSERT jika belum ada
  3. barang      — UPDATE stok_akhir
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from lib.db import fetch_one
from lib.progress import Spinner, show_progress, finish_progress

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

KETERANGAN_KOREKSI = "Koreksi Stok - Saldo Excel 18/06/2026 Sragen"
DEFAULT_SINCE = date(2026, 6, 18)
BATCH_SIZE = 200

PREVIEW_FIELDS = [
    "kode_barang",
    "nama_barang",
    "stok_excel",
    "net_beli",
    "net_jual",
    "net_retur_beli",
    "net_retur_jual",
    "stok_target",
    "stok_sebelum",
    "delta",
    "aksi",
    "catatan",
]


# ---------------------------------------------------------------------------
# 1. Load selisih rows dari Excel output check-stok
# ---------------------------------------------------------------------------

def load_koreksi_rows(path: Path) -> list[dict]:
    """Baca file selisih stok (output check-stok) — ambil kode, nama, stok_excel."""
    from lib.stok_trx_check import load_selisih_rows
    return load_selisih_rows(path)


# ---------------------------------------------------------------------------
# 2. Query net trx sejak tanggal
# ---------------------------------------------------------------------------

def fetch_net_beli_pusat(
    pg, kodes: list[str], since: date, gudang_id: int
) -> dict[str, int]:
    """Net pembelian di Gudang Pusat per kode_barang sejak tanggal since."""
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.kode_barang,
                   COALESCE(SUM(pd.jumlah::numeric), 0)::int AS net
            FROM barang b
            INNER JOIN pembelian_detail pd
                    ON pd.barang_id = b.id AND pd.deleted_at IS NULL
            INNER JOIN pembelian p
                    ON p.id = pd.pembelian_id
                   AND p.deleted_at IS NULL
                   AND p.gudang_id = %s
                   AND p.tanggal >= %s
            WHERE b.deleted_at IS NULL
              AND b.kode_barang = ANY(%s)
            GROUP BY b.kode_barang
            """,
            (gudang_id, since, kodes),
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def fetch_net_jual(pg, kodes: list[str], since: date) -> dict[str, int]:
    """Net penjualan (semua gudang) per kode_barang sejak tanggal since."""
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.kode_barang,
                   COALESCE(SUM(pd.jumlah::numeric), 0)::int AS net
            FROM barang b
            INNER JOIN penjualan_detail pd
                    ON pd.barang_id = b.id AND pd.deleted_at IS NULL
            INNER JOIN penjualan p
                    ON p.id = pd.penjualan_id
                   AND p.deleted_at IS NULL
                   AND p.tanggal::date >= %s
            WHERE b.deleted_at IS NULL
              AND b.kode_barang = ANY(%s)
            GROUP BY b.kode_barang
            """,
            (since, kodes),
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def fetch_net_retur_beli(
    pg, kodes: list[str], gudang_id: int
) -> dict[str, int]:
    """Net retur pembelian Gudang Pusat per kode_barang (semua tanggal)."""
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.kode_barang,
                   COALESCE(SUM(prd.qty_return::numeric), 0)::int AS net
            FROM barang b
            INNER JOIN pembelian_return_detail prd
                    ON prd.barang_id = b.id AND prd.deleted_at IS NULL
            INNER JOIN pembelian_return pr
                    ON pr.id = prd.pembelian_return_id
                   AND pr.deleted_at IS NULL
                   AND pr.gudang_id = %s
            WHERE b.deleted_at IS NULL
              AND b.kode_barang = ANY(%s)
            GROUP BY b.kode_barang
            """,
            (gudang_id, kodes),
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def fetch_net_retur_jual(pg, kodes: list[str]) -> dict[str, int]:
    """Net retur penjualan (semua gudang) per kode_barang (semua tanggal).
    penjualan_return_detail tidak punya deleted_at — filter via status=1.
    """
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.kode_barang,
                   COALESCE(SUM(prd.qty_return::numeric), 0)::int AS net
            FROM barang b
            INNER JOIN penjualan_return_detail prd
                    ON prd.barang_id = b.id
                   AND prd.status = 1
            INNER JOIN penjualan_return pr
                    ON pr.id = prd.penjualan_return_id
                   AND pr.deleted_at IS NULL
            WHERE b.deleted_at IS NULL
              AND b.kode_barang = ANY(%s)
            GROUP BY b.kode_barang
            """,
            (kodes,),
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# 3. Load stok saat ini dari DB
# ---------------------------------------------------------------------------

def fetch_barang_stok_map(pg, kodes: list[str], gudang_id: int) -> dict[str, dict]:
    """
    Return map kode_barang → {barang_id, harga_satuan, stok_sebelum, stok_akhir_id}.
    stok_sebelum = stok_akhir.stok_akhir (fallback barang.stok_akhir, fallback 0).
    stok_akhir_id = None jika belum ada row stok_akhir di gudang ini.
    """
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                b.kode_barang,
                b.id            AS barang_id,
                COALESCE(b.harga_jual, b.harga_beli, 0) AS harga_satuan,
                b.stok_akhir    AS stok_barang,
                sa.id           AS stok_akhir_id,
                sa.stok_akhir   AS stok_gudang
            FROM barang b
            LEFT JOIN stok_akhir sa
                   ON sa.barang_id = b.id
                  AND sa.gudang_id = %s
                  AND sa.deleted_at IS NULL
            WHERE b.deleted_at IS NULL
              AND b.kode_barang = ANY(%s)
            """,
            (gudang_id, kodes),
        )
        result: dict[str, dict] = {}
        for kode, barang_id, harga, stok_barang, sa_id, stok_gudang in cur.fetchall():
            stok_sebelum = (
                int(round(float(stok_gudang))) if stok_gudang is not None
                else int(round(float(stok_barang or 0)))
            )
            result[kode] = {
                "barang_id": barang_id,
                "harga_satuan": float(harga or 0),
                "stok_akhir_id": sa_id,
                "stok_sebelum": stok_sebelum,
                "stok_barang_saat_ini": int(round(float(stok_barang or 0))),
            }
    return result


# ---------------------------------------------------------------------------
# 4. Compute stok_target
# ---------------------------------------------------------------------------

def compute_targets(
    selisih_rows: list[dict],
    barang_map: dict[str, dict],
    net_beli: dict[str, int],
    net_jual: dict[str, int],
    net_retur_beli: dict[str, int],
    net_retur_jual: dict[str, int],
) -> list[dict]:
    """Gabungkan semua info menjadi preview rows dengan stok_target."""
    rows: list[dict] = []
    for r in selisih_rows:
        kode = r["kode_barang"]
        nama = r.get("nama_barang", "")
        stok_excel = int(round(float(r.get("stok_excel") or 0)))

        if kode not in barang_map:
            rows.append({
                "kode_barang": kode,
                "nama_barang": nama,
                "stok_excel": stok_excel,
                "net_beli": 0,
                "net_jual": 0,
                "net_retur_beli": 0,
                "net_retur_jual": 0,
                "stok_target": stok_excel,
                "stok_sebelum": 0,
                "delta": 0,
                "aksi": "skip",
                "catatan": "barang tidak ditemukan di DB",
                "barang_id": None,
                "stok_akhir_id": None,
                "harga_satuan": 0,
            })
            continue

        bmap = barang_map[kode]
        nb = net_beli.get(kode, 0)
        nj = net_jual.get(kode, 0)
        nrb = net_retur_beli.get(kode, 0)
        nrj = net_retur_jual.get(kode, 0)

        target = stok_excel + nb - nj + nrb - nrj
        sebelum = bmap["stok_sebelum"]
        delta = target - sebelum

        barang_saat_ini = bmap.get("stok_barang_saat_ini", sebelum)
        # Perlu insert kalau belum ada baris stok_akhir
        # Perlu update kalau delta != 0 (stok_gudang beda dari target)
        # Perlu update barang kalau barang.stok_akhir tidak sync dengan target
        barang_not_sync = barang_saat_ini != target

        if delta == 0 and not barang_not_sync:
            aksi = "skip"
            catatan = "sudah sesuai"
        elif bmap["stok_akhir_id"] is None:
            aksi = "insert"
            catatan = "belum ada baris stok_akhir"
        else:
            aksi = "update"
            catatan = "barang.stok_akhir tidak sync" if delta == 0 else ""

        rows.append({
            "kode_barang": kode,
            "nama_barang": nama,
            "stok_excel": stok_excel,
            "net_beli": nb,
            "net_jual": nj,
            "net_retur_beli": nrb,
            "net_retur_jual": nrj,
            "stok_target": target,
            "stok_sebelum": sebelum,
            "delta": delta,
            "aksi": aksi,
            "catatan": catatan,
            "barang_id": bmap["barang_id"],
            "stok_akhir_id": bmap["stok_akhir_id"],
            "harga_satuan": bmap["harga_satuan"],
        })
    return rows


# ---------------------------------------------------------------------------
# 5. Apply correction (satu barang)
# ---------------------------------------------------------------------------

def _apply_one(pg, row: dict, gudang_id: int, user_id: int) -> None:
    """Write ledger + stok_akhir + barang untuk satu baris."""
    bid = row["barang_id"]
    target = row["stok_target"]
    sebelum = row["stok_sebelum"]
    harga = row["harga_satuan"]
    delta = target - sebelum
    jenis = "masuk" if delta >= 0 else "keluar"
    masuk = delta if delta > 0 else 0
    keluar = abs(delta) if delta < 0 else 0

    with pg.cursor() as cur:
        # Ledger koreksi
        cur.execute(
            """
            INSERT INTO stok (
                barang_id, gudang_id, keterangan,
                stok_sebelum, stok_masuk, stok_keluar, stok_akhir,
                jenis, harga_satuan, created_by, updated_by, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (bid, gudang_id, KETERANGAN_KOREKSI,
             sebelum, masuk, keluar, target,
             jenis, harga, user_id, user_id),
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
        else:  # insert
            cur.execute(
                """
                INSERT INTO stok_akhir (
                    barang_id, gudang_id, stok_akhir, harga_satuan,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (bid, gudang_id, target, harga, user_id, user_id),
            )

        cur.execute(
            """
            UPDATE barang
            SET stok_akhir = %s, updated_by = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (target, user_id, bid),
        )


# ---------------------------------------------------------------------------
# 6. Orchestrator
# ---------------------------------------------------------------------------

def run_stok_fix(
    pg,
    *,
    selisih_file: Path,
    gudang: str = "pusat",
    since_date: date | None = None,
    user_id: int = 1,
    dry_run: bool = True,
    out_preview: Path | None = None,
    limit: int = 0,
    offset: int = 0,
    allow_production: bool = False,
) -> dict:
    from lib.stok_trx_check import resolve_gudang_id

    # Safety gate production
    db_name = fetch_one(pg, "SELECT current_database()")
    if db_name == "matahari-production" and not allow_production:
        raise RuntimeError(
            f"DB target adalah '{db_name}' (production). "
            "Tambahkan --allow-production untuk melanjutkan."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    since = since_date or DEFAULT_SINCE

    path_in = selisih_file.expanduser().resolve()
    if not path_in.is_file():
        raise FileNotFoundError(path_in)

    gudang_id, gudang_name = resolve_gudang_id(pg, gudang)

    with Spinner(f"Membaca {path_in.name} ..."):
        selisih_rows = load_koreksi_rows(path_in)

    if offset:
        selisih_rows = selisih_rows[offset:]
    if limit:
        selisih_rows = selisih_rows[:limit]

    kodes = [r["kode_barang"] for r in selisih_rows if r.get("kode_barang")]

    with Spinner(f"Query net beli Pusat ≥ {since} ({len(kodes)} kode) ..."):
        net_beli = fetch_net_beli_pusat(pg, kodes, since, gudang_id)

    with Spinner(f"Query net jual ≥ {since} ({len(kodes)} kode) ..."):
        net_jual = fetch_net_jual(pg, kodes, since)

    with Spinner(f"Query retur beli/jual ({len(kodes)} kode) ..."):
        net_retur_b = fetch_net_retur_beli(pg, kodes, gudang_id)
        net_retur_j = fetch_net_retur_jual(pg, kodes)

    with Spinner(f"Query stok saat ini ({len(kodes)} kode) ..."):
        barang_map = fetch_barang_stok_map(pg, kodes, gudang_id)

    with Spinner("Hitung stok_target ..."):
        preview_rows = compute_targets(
            selisih_rows, barang_map, net_beli, net_jual, net_retur_b, net_retur_j
        )

    # Tulis preview CSV
    preview_path = out_preview or OUTPUT_DIR / "stok_koreksi_preview.csv"
    with preview_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=PREVIEW_FIELDS, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(preview_rows)

    stats = {
        "db": db_name,
        "gudang": gudang_name,
        "since_date": since.isoformat(),
        "total_baris": len(preview_rows),
        "update": sum(1 for r in preview_rows if r["aksi"] == "update"),
        "insert": sum(1 for r in preview_rows if r["aksi"] == "insert"),
        "skip": sum(1 for r in preview_rows if r["aksi"] == "skip"),
        "not_found": sum(1 for r in preview_rows if r["catatan"] == "barang tidak ditemukan di DB"),
        "dry_run": dry_run,
        "preview_csv": str(preview_path),
    }

    if dry_run:
        return stats

    # Apply
    apply_rows = [r for r in preview_rows if r["aksi"] in ("update", "insert")]
    total = len(apply_rows)
    applied = 0
    errors: list[str] = []

    for batch_start in range(0, total, BATCH_SIZE):
        batch = apply_rows[batch_start: batch_start + BATCH_SIZE]
        try:
            for row in batch:
                _apply_one(pg, row, gudang_id, user_id)
                applied += 1
            pg.commit()
        except Exception as exc:
            pg.rollback()
            errors.append(f"batch {batch_start}: {exc}")
            raise

        show_progress(applied, total, f"update={stats['update']} insert={stats['insert']}")

    finish_progress()
    stats["applied"] = applied
    stats["errors"] = errors
    return stats


# ---------------------------------------------------------------------------
# 7. Verifikasi
# ---------------------------------------------------------------------------

def verify_stok_fix(
    pg,
    *,
    selisih_file: Path,
    gudang: str = "pusat",
    since_date: date | None = None,
    out_sisa: Path | None = None,
) -> dict:
    """
    Hitung ulang stok_target dan bandingkan dengan stok aktual setelah apply.
    Return stats; mismatch_stok_akhir dan mismatch_barang harus 0 jika berhasil.
    """
    from lib.stok_trx_check import resolve_gudang_id

    since = since_date or DEFAULT_SINCE
    path_in = selisih_file.expanduser().resolve()
    gudang_id, gudang_name = resolve_gudang_id(pg, gudang)

    with Spinner(f"Membaca {path_in.name} untuk verifikasi ..."):
        selisih_rows = load_koreksi_rows(path_in)

    kodes = [r["kode_barang"] for r in selisih_rows if r.get("kode_barang")]

    with Spinner("Query net trx + stok aktual ..."):
        net_beli = fetch_net_beli_pusat(pg, kodes, since, gudang_id)
        net_jual = fetch_net_jual(pg, kodes, since)
        net_retur_b = fetch_net_retur_beli(pg, kodes, gudang_id)
        net_retur_j = fetch_net_retur_jual(pg, kodes)
        barang_map = fetch_barang_stok_map(pg, kodes, gudang_id)

    preview_rows = compute_targets(
        selisih_rows, barang_map, net_beli, net_jual, net_retur_b, net_retur_j
    )

    # Bandingkan stok aktual saat ini vs stok_target
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.kode_barang, sa.stok_akhir AS sa_stok, b.stok_akhir AS b_stok
            FROM barang b
            LEFT JOIN stok_akhir sa
                   ON sa.barang_id = b.id AND sa.gudang_id = %s AND sa.deleted_at IS NULL
            WHERE b.deleted_at IS NULL AND b.kode_barang = ANY(%s)
            """,
            (gudang_id, kodes),
        )
        aktual: dict[str, dict] = {}
        for kode, sa_stok, b_stok in cur.fetchall():
            aktual[kode] = {
                "sa": int(round(float(sa_stok))) if sa_stok is not None else None,
                "b": int(round(float(b_stok or 0))),
            }

    mismatch_sa = 0
    mismatch_b = 0
    sisa_rows: list[dict] = []

    for row in preview_rows:
        kode = row["kode_barang"]
        target = row["stok_target"]
        ak = aktual.get(kode, {})
        sa = ak.get("sa")
        b = ak.get("b", 0)

        # sa = None → belum ada row stok_akhir; OK jika barang.stok_akhir == target
        sa_ok = (sa is not None and sa == target) or (sa is None and b == target)
        b_ok = b == target

        if not sa_ok:
            mismatch_sa += 1
        if not b_ok:
            mismatch_b += 1
        if not sa_ok or not b_ok:
            sisa_rows.append({
                **{k: row[k] for k in PREVIEW_FIELDS if k in row},
                "aktual_stok_akhir": sa,
                "aktual_barang": b,
            })

    # Tulis sisa selisih jika ada
    if sisa_rows and out_sisa:
        out_sisa.parent.mkdir(parents=True, exist_ok=True)
        extra_fields = ["aktual_stok_akhir", "aktual_barang"]
        with out_sisa.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=PREVIEW_FIELDS + extra_fields, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(sisa_rows)

    return {
        "gudang": gudang_name,
        "total": len(preview_rows),
        "mismatch_stok_akhir": mismatch_sa,
        "mismatch_barang": mismatch_b,
        "ok": mismatch_sa == 0 and mismatch_b == 0,
        "sisa_file": str(out_sisa) if sisa_rows and out_sisa else None,
    }
