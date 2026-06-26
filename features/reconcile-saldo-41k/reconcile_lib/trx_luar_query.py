from __future__ import annotations

from datetime import date

from reconcile_lib.pg_gudang import (
    PEMBELIAN_GUDANG_JOIN,
    PEMBELIAN_GUDANG_SELECT,
    PENJUALAN_KASIR_JOIN,
    PENJUALAN_KASIR_SELECT,
)

PENJUALAN_FAKTUR_SQL = f"""
WITH excel_nama AS (
    SELECT unnest(%s::text[]) AS nama
)
SELECT
    p.id AS transaksi_id,
    p.no_faktur,
    p.tanggal::date AS tanggal,
    {PENJUALAN_KASIR_SELECT.strip()},
    COUNT(pd.id) AS total_line,
    COUNT(pd.id) FILTER (WHERE en.nama IS NOT NULL) AS line_dalam_41k,
    COUNT(pd.id) FILTER (WHERE en.nama IS NULL) AS line_luar_41k,
    COALESCE(SUM(pd.jumlah::numeric) FILTER (WHERE en.nama IS NOT NULL), 0)::int AS qty_dalam_41k,
    COALESCE(SUM(pd.jumlah::numeric) FILTER (WHERE en.nama IS NULL), 0)::int AS qty_luar_41k,
    CASE
        WHEN COUNT(pd.id) FILTER (WHERE en.nama IS NOT NULL) = 0 THEN 'MURNI_LUAR_41K'
        WHEN COUNT(pd.id) FILTER (WHERE en.nama IS NULL) = 0 THEN 'MURNI_DALAM_41K'
        ELSE 'CAMPURAN'
    END AS kategori_faktur
FROM penjualan p
JOIN penjualan_detail pd ON pd.penjualan_id = p.id AND pd.deleted_at IS NULL
JOIN barang b ON b.id = pd.barang_id AND b.deleted_at IS NULL
LEFT JOIN excel_nama en ON en.nama = b.nama
{PENJUALAN_KASIR_JOIN}
WHERE p.deleted_at IS NULL
  AND (%s::date IS NULL OR p.tanggal::date >= %s::date)
GROUP BY p.id, p.no_faktur, p.tanggal, u_kasir.name, ms.name, mg_kasir.name, mg_kasir.id
HAVING COUNT(pd.id) FILTER (WHERE en.nama IS NULL) > 0
ORDER BY p.tanggal DESC, p.no_faktur
"""

PENJUALAN_DETAIL_LUAR_SQL = f"""
WITH excel_nama AS (
    SELECT unnest(%s::text[]) AS nama
)
SELECT
    p.no_faktur,
    p.tanggal::date AS tanggal,
    {PENJUALAN_KASIR_SELECT.strip()},
    b.kode_barang,
    b.nama AS nama_barang,
    pd.jumlah::int AS qty,
    pd.harga_satuan,
    pd.total,
    b.stok_akhir AS stok_barang_system
FROM penjualan p
JOIN penjualan_detail pd ON pd.penjualan_id = p.id AND pd.deleted_at IS NULL
JOIN barang b ON b.id = pd.barang_id AND b.deleted_at IS NULL
LEFT JOIN excel_nama en ON en.nama = b.nama
{PENJUALAN_KASIR_JOIN}
WHERE p.deleted_at IS NULL
  AND en.nama IS NULL
  AND (%s::date IS NULL OR p.tanggal::date >= %s::date)
ORDER BY p.tanggal DESC, p.no_faktur, b.kode_barang
"""

PEMBELIAN_FAKTUR_SQL = f"""
WITH excel_nama AS (
    SELECT unnest(%s::text[]) AS nama
)
SELECT
    p.id AS transaksi_id,
    p.no_faktur,
    p.tanggal AS tanggal,
    {PEMBELIAN_GUDANG_SELECT.strip()},
    COUNT(pd.id) AS total_line,
    COUNT(pd.id) FILTER (WHERE en.nama IS NOT NULL) AS line_dalam_41k,
    COUNT(pd.id) FILTER (WHERE en.nama IS NULL) AS line_luar_41k,
    COALESCE(SUM(pd.jumlah::numeric) FILTER (WHERE en.nama IS NOT NULL), 0)::int AS qty_dalam_41k,
    COALESCE(SUM(pd.jumlah::numeric) FILTER (WHERE en.nama IS NULL), 0)::int AS qty_luar_41k,
    CASE
        WHEN COUNT(pd.id) FILTER (WHERE en.nama IS NOT NULL) = 0 THEN 'MURNI_LUAR_41K'
        WHEN COUNT(pd.id) FILTER (WHERE en.nama IS NULL) = 0 THEN 'MURNI_DALAM_41K'
        ELSE 'CAMPURAN'
    END AS kategori_faktur
FROM pembelian p
JOIN pembelian_detail pd ON pd.pembelian_id = p.id AND pd.deleted_at IS NULL
JOIN barang b ON b.id = pd.barang_id AND b.deleted_at IS NULL
LEFT JOIN excel_nama en ON en.nama = b.nama
{PEMBELIAN_GUDANG_JOIN}
WHERE p.deleted_at IS NULL
  AND p.gudang_id = %s
  AND (%s::date IS NULL OR p.tanggal >= %s::date)
GROUP BY p.id, p.no_faktur, p.tanggal, mg.name, p.gudang_id
HAVING COUNT(pd.id) FILTER (WHERE en.nama IS NULL) > 0
ORDER BY p.tanggal DESC, p.no_faktur
"""

PEMBELIAN_DETAIL_LUAR_SQL = f"""
WITH excel_nama AS (
    SELECT unnest(%s::text[]) AS nama
)
SELECT
    p.no_faktur,
    p.tanggal AS tanggal,
    {PEMBELIAN_GUDANG_SELECT.strip()},
    b.kode_barang,
    b.nama AS nama_barang,
    pd.jumlah::int AS qty,
    pd.harga,
    pd.total,
    b.stok_akhir AS stok_barang_system
FROM pembelian p
JOIN pembelian_detail pd ON pd.pembelian_id = p.id AND pd.deleted_at IS NULL
JOIN barang b ON b.id = pd.barang_id AND b.deleted_at IS NULL
LEFT JOIN excel_nama en ON en.nama = b.nama
{PEMBELIAN_GUDANG_JOIN}
WHERE p.deleted_at IS NULL
  AND p.gudang_id = %s
  AND en.nama IS NULL
  AND (%s::date IS NULL OR p.tanggal >= %s::date)
ORDER BY p.tanggal DESC, p.no_faktur, b.kode_barang
"""

BARANG_LUAR_RINGKASAN_SQL = """
WITH excel_nama AS (
    SELECT unnest(%s::text[]) AS nama
),
jual AS (
    SELECT
        b.kode_barang,
        b.nama AS nama_barang,
        COUNT(DISTINCT p.id) AS faktur_jual,
        COALESCE(SUM(pd.jumlah::numeric), 0)::int AS qty_jual,
        MAX(p.tanggal::date) AS tgl_jual_terakhir
    FROM penjualan p
    JOIN penjualan_detail pd ON pd.penjualan_id = p.id AND pd.deleted_at IS NULL
    JOIN barang b ON b.id = pd.barang_id AND b.deleted_at IS NULL
    LEFT JOIN excel_nama en ON en.nama = b.nama
    WHERE p.deleted_at IS NULL AND en.nama IS NULL
      AND (%s::date IS NULL OR p.tanggal::date >= %s::date)
    GROUP BY b.kode_barang, b.nama
),
beli AS (
    SELECT
        b.kode_barang,
        b.nama AS nama_barang,
        COUNT(DISTINCT p.id) AS faktur_beli,
        COALESCE(SUM(pd.jumlah::numeric), 0)::int AS qty_beli,
        MAX(p.tanggal) AS tgl_beli_terakhir
    FROM pembelian p
    JOIN pembelian_detail pd ON pd.pembelian_id = p.id AND pd.deleted_at IS NULL
    JOIN barang b ON b.id = pd.barang_id AND b.deleted_at IS NULL
    LEFT JOIN excel_nama en ON en.nama = b.nama
    WHERE p.deleted_at IS NULL AND p.gudang_id = %s AND en.nama IS NULL
      AND (%s::date IS NULL OR p.tanggal >= %s::date)
    GROUP BY b.kode_barang, b.nama
)
SELECT
    COALESCE(j.kode_barang, bel.kode_barang) AS kode_barang,
    COALESCE(j.nama_barang, bel.nama_barang) AS nama_barang,
    b.stok_akhir AS stok_barang_system,
    sa.stok_akhir AS stok_gudang_pusat,
    COALESCE(j.faktur_jual, 0) AS faktur_jual,
    COALESCE(j.qty_jual, 0) AS qty_jual,
    j.tgl_jual_terakhir,
    COALESCE(bel.faktur_beli, 0) AS faktur_beli,
    COALESCE(bel.qty_beli, 0) AS qty_beli,
    bel.tgl_beli_terakhir,
    CASE
        WHEN COALESCE(j.qty_jual, 0) > 0 AND COALESCE(bel.qty_beli, 0) > 0 THEN 'JUAL+BELI'
        WHEN COALESCE(j.qty_jual, 0) > 0 THEN 'JUAL saja'
        WHEN COALESCE(bel.qty_beli, 0) > 0 THEN 'BELI saja'
        ELSE 'LAIN'
    END AS kategori_trx
FROM jual j
FULL OUTER JOIN beli bel ON bel.kode_barang = j.kode_barang
LEFT JOIN barang b ON b.kode_barang = COALESCE(j.kode_barang, bel.kode_barang) AND b.deleted_at IS NULL
LEFT JOIN stok_akhir sa ON sa.barang_id = b.id AND sa.gudang_id = %s
ORDER BY COALESCE(j.qty_jual, 0) + COALESCE(bel.qty_beli, 0) DESC, kode_barang
"""

TRANSFER_LUAR_SQL = """
WITH excel_nama AS (
    SELECT unnest(%s::text[]) AS nama
)
SELECT
    tg.transfer_id,
    tg.tanggal_transfer,
    tg.status,
    ga.name AS gudang_asal,
    gt.name AS gudang_tujuan,
    b.kode_barang,
    b.nama AS nama_barang,
    tgd.jumlah_transfer,
    tgd.jumlah_terima
FROM transfer_gudang tg
JOIN transfer_gudang_detail tgd ON tgd.transfer_gudang_id = tg.id AND tgd.deleted_at IS NULL
JOIN barang b ON b.id = tgd.barang_id AND b.deleted_at IS NULL
JOIN master_gudang ga ON ga.id = tg.gudang_asal_id
JOIN master_gudang gt ON gt.id = tg.gudang_tujuan_id
LEFT JOIN excel_nama en ON en.nama = b.nama
WHERE tg.deleted_at IS NULL
  AND en.nama IS NULL
  AND (%s::date IS NULL OR tg.tanggal_transfer >= %s::date)
  AND (%s::bigint IS NULL OR tg.gudang_asal_id = %s OR tg.gudang_tujuan_id = %s)
ORDER BY tg.tanggal_transfer DESC, tg.transfer_id, b.kode_barang
"""


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_trx_luar(
    pg,
    *,
    excel_names: list[str],
    gudang_id: int,
    since: date | None = None,
) -> dict[str, list[dict]]:
    since_params = (since, since)
    with pg.cursor() as cur:
        cur.execute(PENJUALAN_FAKTUR_SQL, (excel_names, since, since))
        penjualan_faktur = _rows_to_dicts(cur)

        cur.execute(PENJUALAN_DETAIL_LUAR_SQL, (excel_names, since, since))
        penjualan_detail = _rows_to_dicts(cur)

        cur.execute(PEMBELIAN_FAKTUR_SQL, (excel_names, gudang_id, since, since))
        pembelian_faktur = _rows_to_dicts(cur)

        cur.execute(PEMBELIAN_DETAIL_LUAR_SQL, (excel_names, gudang_id, since, since))
        pembelian_detail = _rows_to_dicts(cur)

        cur.execute(
            BARANG_LUAR_RINGKASAN_SQL,
            (excel_names, since, since, gudang_id, since, since, gudang_id),
        )
        barang_ringkasan = _rows_to_dicts(cur)

        cur.execute(
            TRANSFER_LUAR_SQL,
            (excel_names, since, since, gudang_id, gudang_id, gudang_id),
        )
        transfer = _rows_to_dicts(cur)

    return {
        "penjualan_faktur": penjualan_faktur,
        "penjualan_detail": penjualan_detail,
        "pembelian_faktur": pembelian_faktur,
        "pembelian_detail": pembelian_detail,
        "barang_ringkasan": barang_ringkasan,
        "transfer": transfer,
    }


def build_luar_stats(data: dict, *, excel_count: int, gudang: str, since: date | None, source_file: str, pg_database: str) -> dict:
    pj_f = data["penjualan_faktur"]
    pb_f = data["pembelian_faktur"]
    br = data["barang_ringkasan"]

    def _count_cat(rows: list[dict], key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            cat = row.get(key, "")
            out[cat] = out.get(cat, 0) + 1
        return out

    pj_cat = _count_cat(pj_f, "kategori_faktur")
    pb_cat = _count_cat(pb_f, "kategori_faktur")

    return {
        "source_file": source_file,
        "pg_database": pg_database,
        "gudang": gudang,
        "since_date": since.isoformat() if since else "SEMUA",
        "excel_barang_rows": excel_count,
        "barang_luar_dengan_trx": len(br),
        "penjualan_faktur_luar": len(pj_f),
        "penjualan_faktur_murni_luar": pj_cat.get("MURNI_LUAR_41K", 0),
        "penjualan_faktur_campuran": pj_cat.get("CAMPURAN", 0),
        "penjualan_line_luar": len(data["penjualan_detail"]),
        "penjualan_qty_luar": sum(int(r.get("qty") or 0) for r in data["penjualan_detail"]),
        "pembelian_faktur_luar": len(pb_f),
        "pembelian_faktur_murni_luar": pb_cat.get("MURNI_LUAR_41K", 0),
        "pembelian_faktur_campuran": pb_cat.get("CAMPURAN", 0),
        "pembelian_line_luar": len(data["pembelian_detail"]),
        "pembelian_qty_luar": sum(int(r.get("qty") or 0) for r in data["pembelian_detail"]),
        "transfer_line_luar": len(data["transfer"]),
    }
