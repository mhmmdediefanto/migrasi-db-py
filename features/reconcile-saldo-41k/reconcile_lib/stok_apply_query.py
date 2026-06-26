"""Query barang untuk apply koreksi stok skenario 1/2/3."""

from __future__ import annotations

from datetime import date

from reconcile_lib.stok_hitung_query import (
    DEFAULT_SINCE,
    fetch_stok_hitung_41k,
    fetch_stok_hitung_luar_41k,
)

KETERANGAN = "Koreksi Stok Reconcile 41k — Skenario {skenario}"

# S1: luar 41k, tanpa trx di gudang target → stok gudang = 0
SCENARIO_1_SQL = """
WITH excel AS (
    SELECT unnest(%s::text[]) AS nama
)
SELECT
    b.id AS barang_id,
    b.kode_barang,
    b.nama AS nama_barang,
    COALESCE(sa.stok_akhir, 0)::int AS stok_gudang,
    sa.id AS stok_akhir_id,
    COALESCE(b.harga_jual, b.harga_beli, 0)::numeric AS harga_satuan
FROM barang b
LEFT JOIN stok_akhir sa
       ON sa.barang_id = b.id AND sa.gudang_id = %s AND sa.deleted_at IS NULL
WHERE b.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM excel e WHERE e.nama = b.nama)
  AND NOT EXISTS (
        SELECT 1 FROM pembelian_detail pd
        JOIN pembelian p ON p.id = pd.pembelian_id AND p.deleted_at IS NULL
        WHERE pd.barang_id = b.id AND pd.deleted_at IS NULL AND p.gudang_id = %s
  )
  AND NOT EXISTS (
        SELECT 1 FROM penjualan_detail pd
        JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
        JOIN users u ON u.id = p.sales_id
        JOIN master_store ms ON ms.id = u.store_id
        WHERE pd.barang_id = b.id AND pd.deleted_at IS NULL
          AND p.sales_id IS NOT NULL AND ms.gudang_id = %s
  )
  AND NOT EXISTS (
        SELECT 1 FROM transfer_gudang_detail tgd
        JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL
        WHERE tgd.barang_id = b.id AND tgd.deleted_at IS NULL
          AND (tg.gudang_asal_id = %s OR tg.gudang_tujuan_id = %s)
  )
  AND NOT EXISTS (
        SELECT 1 FROM pembelian_return_detail prd
        JOIN pembelian_return pr ON pr.id = prd.pembelian_return_id AND pr.deleted_at IS NULL
        WHERE prd.barang_id = b.id AND prd.deleted_at IS NULL AND pr.gudang_id = %s
  )
  AND NOT EXISTS (
        SELECT 1 FROM penjualan_return_detail prd
        JOIN penjualan_return pr ON pr.id = prd.penjualan_return_id AND pr.deleted_at IS NULL
        WHERE prd.barang_id = b.id AND prd.status = 1 AND pr.gudang_id = %s
  )
  AND NOT EXISTS (
        SELECT 1 FROM penyesuaian_stok_detail psd
        JOIN penyesuaian_stok ps ON ps.id = psd.penyesuaian_stok_id AND ps.deleted_at IS NULL
        WHERE psd.barang_id = b.id AND ps.gudang_id = %s
  )
  AND COALESCE(sa.stok_akhir, 0) <> 0
ORDER BY b.kode_barang
"""


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_scenario_1(pg, *, excel_names: list[str], gudang_id: int) -> list[dict]:
    with pg.cursor() as cur:
        cur.execute(
            SCENARIO_1_SQL,
            (
                excel_names,
                gudang_id,
                gudang_id,
                gudang_id,
                gudang_id,
                gudang_id,
                gudang_id,
                gudang_id,
                gudang_id,
            ),
        )
        return _rows_to_dicts(cur)


def fetch_barang_stok_map_gudang(pg, kodes: list[str], gudang_id: int) -> dict[str, dict]:
    """Stok sebelum apply = stok_akhir gudang saja (0 jika belum ada baris)."""
    if not kodes:
        return {}
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                b.kode_barang,
                b.id AS barang_id,
                COALESCE(b.harga_jual, b.harga_beli, 0) AS harga_satuan,
                sa.id AS stok_akhir_id,
                sa.stok_akhir AS stok_gudang
            FROM barang b
            LEFT JOIN stok_akhir sa
                   ON sa.barang_id = b.id
                  AND sa.gudang_id = %s
                  AND sa.deleted_at IS NULL
            WHERE b.deleted_at IS NULL AND b.kode_barang = ANY(%s)
            """,
            (gudang_id, kodes),
        )
        result: dict[str, dict] = {}
        for kode, barang_id, harga, sa_id, stok_gudang in cur.fetchall():
            result[kode] = {
                "barang_id": barang_id,
                "harga_satuan": float(harga or 0),
                "stok_akhir_id": sa_id,
                "stok_sebelum": int(round(float(stok_gudang))) if stok_gudang is not None else 0,
            }
        return result


def _sebelum_stok(row: dict) -> int:
    return int(row.get("stok_gudang") or 0)


def _build_apply_row(
    *,
    skenario: str,
    barang_id: int,
    kode_barang: str,
    nama_barang: str,
    stok_target: int,
    stok_sebelum: int,
    stok_akhir_id,
    harga_satuan: float,
    catatan: str = "",
) -> dict | None:
    delta = stok_target - stok_sebelum
    if delta == 0:
        return None
    aksi = "insert" if stok_akhir_id is None else "update"
    return {
        "skenario": skenario,
        "kode_barang": kode_barang,
        "nama_barang": nama_barang,
        "stok_target": stok_target,
        "stok_sebelum": stok_sebelum,
        "delta": delta,
        "aksi": aksi,
        "catatan": catatan,
        "barang_id": barang_id,
        "stok_akhir_id": stok_akhir_id,
        "harga_satuan": float(harga_satuan or 0),
    }


def build_scenario_1_apply(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        sebelum = _sebelum_stok(row)
        apply = _build_apply_row(
            skenario="S1",
            barang_id=int(row["barang_id"]),
            kode_barang=row["kode_barang"],
            nama_barang=row["nama_barang"],
            stok_target=0,
            stok_sebelum=sebelum,
            stok_akhir_id=row.get("stok_akhir_id"),
            harga_satuan=float(row.get("harga_satuan") or 0),
            catatan="luar 41k, tanpa trx gudang Sragen → 0",
        )
        if apply:
            out.append(apply)
    return out


def build_scenario_2_apply(
    hitung_rows: list[dict],
    barang_meta: dict[str, dict],
) -> list[dict]:
    out: list[dict] = []
    for row in hitung_rows:
        if row.get("ada_trx") != "Y":
            continue
        kode = row.get("kode_barang")
        meta = barang_meta.get(kode)
        if not meta:
            continue
        target = int(row.get("stok_hitung") or 0)
        sebelum = meta["stok_sebelum"]
        apply = _build_apply_row(
            skenario="S2",
            barang_id=meta["barang_id"],
            kode_barang=kode,
            nama_barang=row.get("nama_barang", ""),
            stok_target=target,
            stok_sebelum=sebelum,
            stok_akhir_id=meta.get("stok_akhir_id"),
            harga_satuan=meta.get("harga_satuan", 0),
            catatan="luar 41k, ada trx gudang Sragen → 0 + net trx Sragen",
        )
        if apply:
            out.append(apply)
    return out


def build_scenario_3_apply(
    hitung_rows: list[dict],
    barang_meta: dict[str, dict],
) -> list[dict]:
    out: list[dict] = []
    for row in hitung_rows:
        if row.get("not_in_barang") == "Y":
            continue
        kode = row.get("kode_barang")
        if not kode:
            continue
        meta = barang_meta.get(kode)
        if not meta:
            continue
        target = int(row.get("stok_hitung") or 0)
        sebelum = meta["stok_sebelum"]
        apply = _build_apply_row(
            skenario="S3",
            barang_id=meta["barang_id"],
            kode_barang=kode,
            nama_barang=row.get("nama_barang", ""),
            stok_target=target,
            stok_sebelum=sebelum,
            stok_akhir_id=meta.get("stok_akhir_id"),
            harga_satuan=meta.get("harga_satuan", 0),
            catatan="dalam 41k → excel + trx gudang Sragen >= 18/6",
        )
        if apply:
            out.append(apply)
    return out


def fetch_all_apply_rows(
    pg,
    *,
    names: list[str],
    stoks: list[int],
    gudang_id: int,
    since: date | None = None,
) -> tuple[list[dict], dict]:
    since = since or DEFAULT_SINCE

    s1_raw = fetch_scenario_1(pg, excel_names=names, gudang_id=gudang_id)
    rows_41k = fetch_stok_hitung_41k(pg, names=names, stoks=stoks, gudang_id=gudang_id, since=since)
    rows_luar = fetch_stok_hitung_luar_41k(pg, names=names, gudang_id=gudang_id)

    kodes = list({
        *(r["kode_barang"] for r in rows_41k if r.get("kode_barang")),
        *(r["kode_barang"] for r in rows_luar if r.get("kode_barang")),
    })
    barang_meta = fetch_barang_stok_map_gudang(pg, kodes, gudang_id) if kodes else {}

    s1 = build_scenario_1_apply(s1_raw)
    s2 = build_scenario_2_apply(rows_luar, barang_meta)
    s3 = build_scenario_3_apply(rows_41k, barang_meta)

    s1_ids = {r["barang_id"] for r in s1}
    s2 = [r for r in s2 if r["barang_id"] not in s1_ids]

    all_rows = s1 + s2 + s3
    stats = {
        "s1_kandidat": len(s1_raw),
        "s1_apply": len(s1),
        "s2_apply": len(s2),
        "s3_apply": len(s3),
        "total_apply": len(all_rows),
        "since_date": since.isoformat(),
    }
    return all_rows, stats
