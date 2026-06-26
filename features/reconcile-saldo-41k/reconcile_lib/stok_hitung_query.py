from __future__ import annotations

from datetime import date

from reconcile_lib.pg_gudang import PENJUALAN_RETURN_SALES_JOIN, PENJUALAN_SALES_JOIN

DEFAULT_SINCE = date(2026, 6, 18)

# S3 / hitung 41k: baseline Excel Sragen + trx gudang Sragen saja (>= since)
# Penjualan/retur jual: penjualan.sales_id → users → master_store → gudang

STOK_HITUNG_41K_SQL = f"""
WITH excel AS (
    SELECT v.nama, v.stok_excel
    FROM unnest(%s::text[], %s::int[]) AS v(nama, stok_excel)
),
target AS (
    SELECT DISTINCT ON (e.nama)
        b.id AS barang_id,
        b.kode_barang,
        COALESCE(b.nama, e.nama) AS nama_barang,
        e.stok_excel AS baseline,
        TRUE AS in_41k
    FROM excel e
    LEFT JOIN barang b ON b.nama = e.nama AND b.deleted_at IS NULL
    ORDER BY e.nama, b.id NULLS LAST
),
ids AS (
    SELECT barang_id FROM target WHERE barang_id IS NOT NULL
),
net_beli AS (
    SELECT pd.barang_id, COALESCE(SUM(pd.jumlah::numeric), 0)::int AS qty
    FROM pembelian_detail pd
    JOIN pembelian p ON p.id = pd.pembelian_id AND p.deleted_at IS NULL
    JOIN ids ON ids.barang_id = pd.barang_id
    WHERE pd.deleted_at IS NULL AND p.gudang_id = %s AND p.tanggal >= %s
    GROUP BY pd.barang_id
),
net_jual AS (
    SELECT pd.barang_id, COALESCE(SUM(pd.jumlah::numeric), 0)::int AS qty
    FROM penjualan_detail pd
    JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
    JOIN ids ON ids.barang_id = pd.barang_id
    {PENJUALAN_SALES_JOIN}
    WHERE pd.deleted_at IS NULL
      AND p.tanggal::date >= %s
      AND p.sales_id IS NOT NULL
      AND ms.gudang_id = %s
    GROUP BY pd.barang_id
),
net_retur_beli AS (
    SELECT prd.barang_id, COALESCE(SUM(prd.qty_return::numeric), 0)::int AS qty
    FROM pembelian_return_detail prd
    JOIN pembelian_return pr ON pr.id = prd.pembelian_return_id AND pr.deleted_at IS NULL
    JOIN ids ON ids.barang_id = prd.barang_id
    WHERE prd.deleted_at IS NULL AND pr.gudang_id = %s AND pr.tanggal_return >= %s
    GROUP BY prd.barang_id
),
net_retur_jual AS (
    SELECT prd.barang_id, COALESCE(SUM(prd.qty_return::numeric), 0)::int AS qty
    FROM penjualan_return_detail prd
    JOIN penjualan_return pr ON pr.id = prd.penjualan_return_id AND pr.deleted_at IS NULL
    JOIN ids ON ids.barang_id = prd.barang_id
    {PENJUALAN_RETURN_SALES_JOIN}
    WHERE prd.status = 1
      AND pr.tanggal_return >= %s
      AND p.sales_id IS NOT NULL
      AND ms.gudang_id = %s
    GROUP BY prd.barang_id
),
net_tf_masuk AS (
    SELECT tgd.barang_id, COALESCE(SUM(COALESCE(tgd.jumlah_terima, tgd.jumlah_transfer)::numeric), 0)::int AS qty
    FROM transfer_gudang_detail tgd
    JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL
    JOIN ids ON ids.barang_id = tgd.barang_id
    WHERE tgd.deleted_at IS NULL AND tg.gudang_tujuan_id = %s
      AND tg.tanggal_transfer >= %s AND tg.status IN ('diterima', 'confirmed')
    GROUP BY tgd.barang_id
),
net_tf_keluar AS (
    SELECT tgd.barang_id, COALESCE(SUM(tgd.jumlah_transfer::numeric), 0)::int AS qty
    FROM transfer_gudang_detail tgd
    JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL
    JOIN ids ON ids.barang_id = tgd.barang_id
    WHERE tgd.deleted_at IS NULL AND tg.gudang_asal_id = %s
      AND tg.tanggal_transfer >= %s AND tg.status IN ('diterima', 'confirmed', 'dikirim')
    GROUP BY tgd.barang_id
),
net_penyesuaian AS (
    SELECT psd.barang_id,
           COALESCE(SUM(psd.stok_fisik - COALESCE(psd.stok_sebelum, 0)::numeric), 0)::int AS qty
    FROM penyesuaian_stok_detail psd
    JOIN penyesuaian_stok ps ON ps.id = psd.penyesuaian_stok_id AND ps.deleted_at IS NULL
    JOIN ids ON ids.barang_id = psd.barang_id
    WHERE ps.gudang_id = %s AND ps.tanggal_transaksi >= %s
    GROUP BY psd.barang_id
)
SELECT
    t.kode_barang,
    t.nama_barang,
    t.in_41k,
    t.baseline,
    COALESCE(nb.qty, 0) AS net_pembelian,
    COALESCE(nj.qty, 0) AS net_penjualan,
    COALESCE(nrb.qty, 0) AS net_retur_beli,
    COALESCE(nrj.qty, 0) AS net_retur_jual,
    COALESCE(ntm.qty, 0) AS net_transfer_masuk,
    COALESCE(ntk.qty, 0) AS net_transfer_keluar,
    COALESCE(np.qty, 0) AS net_penyesuaian,
    t.baseline
        + COALESCE(nb.qty, 0)
        - COALESCE(nj.qty, 0)
        + COALESCE(nrb.qty, 0)
        - COALESCE(nrj.qty, 0)
        + COALESCE(ntm.qty, 0)
        - COALESCE(ntk.qty, 0)
        + COALESCE(np.qty, 0) AS stok_hitung,
    sa.stok_akhir AS stok_gudang_sistem,
    b.stok_akhir AS stok_barang_sistem,
    (t.barang_id IS NULL) AS not_in_barang
FROM target t
LEFT JOIN barang b ON b.id = t.barang_id
LEFT JOIN stok_akhir sa ON sa.barang_id = t.barang_id AND sa.gudang_id = %s
LEFT JOIN net_beli nb ON nb.barang_id = t.barang_id
LEFT JOIN net_jual nj ON nj.barang_id = t.barang_id
LEFT JOIN net_retur_beli nrb ON nrb.barang_id = t.barang_id
LEFT JOIN net_retur_jual nrj ON nrj.barang_id = t.barang_id
LEFT JOIN net_tf_masuk ntm ON ntm.barang_id = t.barang_id
LEFT JOIN net_tf_keluar ntk ON ntk.barang_id = t.barang_id
LEFT JOIN net_penyesuaian np ON np.barang_id = t.barang_id
ORDER BY t.nama_barang
"""

STOK_HITUNG_LUAR_41K_SQL = f"""
WITH excel AS (
    SELECT unnest(%s::text[]) AS nama
),
scope AS (
    SELECT b.id AS barang_id, b.kode_barang, b.nama AS nama_barang,
           0 AS baseline, FALSE AS in_41k
    FROM barang b
    WHERE b.deleted_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM excel e WHERE e.nama = b.nama)
      AND (
            EXISTS (
                SELECT 1 FROM stok_akhir sa
                WHERE sa.barang_id = b.id AND sa.gudang_id = %s
                  AND COALESCE(sa.stok_akhir, 0) <> 0
            )
         OR EXISTS (SELECT 1 FROM pembelian_detail pd JOIN pembelian p ON p.id = pd.pembelian_id AND p.deleted_at IS NULL WHERE pd.barang_id = b.id AND pd.deleted_at IS NULL AND p.gudang_id = %s)
         OR EXISTS (
                SELECT 1 FROM penjualan_detail pd
                JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
                JOIN users u ON u.id = p.sales_id
                JOIN master_store ms ON ms.id = u.store_id
                WHERE pd.barang_id = b.id AND pd.deleted_at IS NULL
                  AND p.sales_id IS NOT NULL AND ms.gudang_id = %s
            )
         OR EXISTS (SELECT 1 FROM transfer_gudang_detail tgd JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL WHERE tgd.barang_id = b.id AND tgd.deleted_at IS NULL AND (tg.gudang_asal_id = %s OR tg.gudang_tujuan_id = %s))
      )
),
net_beli AS (
    SELECT pd.barang_id, COALESCE(SUM(pd.jumlah::numeric), 0)::int AS qty
    FROM pembelian_detail pd
    JOIN pembelian p ON p.id = pd.pembelian_id AND p.deleted_at IS NULL
    JOIN scope s ON s.barang_id = pd.barang_id
    WHERE pd.deleted_at IS NULL AND p.gudang_id = %s
    GROUP BY pd.barang_id
),
net_jual AS (
    SELECT pd.barang_id, COALESCE(SUM(pd.jumlah::numeric), 0)::int AS qty
    FROM penjualan_detail pd
    JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
    JOIN scope s ON s.barang_id = pd.barang_id
    {PENJUALAN_SALES_JOIN}
    WHERE pd.deleted_at IS NULL
      AND p.sales_id IS NOT NULL
      AND ms.gudang_id = %s
    GROUP BY pd.barang_id
),
net_retur_beli AS (
    SELECT prd.barang_id, COALESCE(SUM(prd.qty_return::numeric), 0)::int AS qty
    FROM pembelian_return_detail prd
    JOIN pembelian_return pr ON pr.id = prd.pembelian_return_id AND pr.deleted_at IS NULL
    JOIN scope s ON s.barang_id = prd.barang_id
    WHERE prd.deleted_at IS NULL AND pr.gudang_id = %s
    GROUP BY prd.barang_id
),
net_retur_jual AS (
    SELECT prd.barang_id, COALESCE(SUM(prd.qty_return::numeric), 0)::int AS qty
    FROM penjualan_return_detail prd
    JOIN penjualan_return pr ON pr.id = prd.penjualan_return_id AND pr.deleted_at IS NULL
    JOIN scope s ON s.barang_id = prd.barang_id
    {PENJUALAN_RETURN_SALES_JOIN}
    WHERE prd.status = 1
      AND p.sales_id IS NOT NULL
      AND ms.gudang_id = %s
    GROUP BY prd.barang_id
),
net_tf_masuk AS (
    SELECT tgd.barang_id, COALESCE(SUM(COALESCE(tgd.jumlah_terima, tgd.jumlah_transfer)::numeric), 0)::int AS qty
    FROM transfer_gudang_detail tgd
    JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL
    JOIN scope s ON s.barang_id = tgd.barang_id
    WHERE tgd.deleted_at IS NULL AND tg.gudang_tujuan_id = %s
      AND tg.status IN ('diterima', 'confirmed')
    GROUP BY tgd.barang_id
),
net_tf_keluar AS (
    SELECT tgd.barang_id, COALESCE(SUM(tgd.jumlah_transfer::numeric), 0)::int AS qty
    FROM transfer_gudang_detail tgd
    JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL
    JOIN scope s ON s.barang_id = tgd.barang_id
    WHERE tgd.deleted_at IS NULL AND tg.gudang_asal_id = %s
      AND tg.status IN ('diterima', 'confirmed', 'dikirim')
    GROUP BY tgd.barang_id
),
net_penyesuaian AS (
    SELECT psd.barang_id,
           COALESCE(SUM(psd.stok_fisik - COALESCE(psd.stok_sebelum, 0)::numeric), 0)::int AS qty
    FROM penyesuaian_stok_detail psd
    JOIN penyesuaian_stok ps ON ps.id = psd.penyesuaian_stok_id AND ps.deleted_at IS NULL
    JOIN scope s ON s.barang_id = psd.barang_id
    WHERE ps.gudang_id = %s
    GROUP BY psd.barang_id
)
SELECT
    s.kode_barang,
    s.nama_barang,
    s.in_41k,
    s.baseline,
    COALESCE(nb.qty, 0) AS net_pembelian,
    COALESCE(nj.qty, 0) AS net_penjualan,
    COALESCE(nrb.qty, 0) AS net_retur_beli,
    COALESCE(nrj.qty, 0) AS net_retur_jual,
    COALESCE(ntm.qty, 0) AS net_transfer_masuk,
    COALESCE(ntk.qty, 0) AS net_transfer_keluar,
    COALESCE(np.qty, 0) AS net_penyesuaian,
    s.baseline
        + COALESCE(nb.qty, 0)
        - COALESCE(nj.qty, 0)
        + COALESCE(nrb.qty, 0)
        - COALESCE(nrj.qty, 0)
        + COALESCE(ntm.qty, 0)
        - COALESCE(ntk.qty, 0)
        + COALESCE(np.qty, 0) AS stok_hitung,
    sa.stok_akhir AS stok_gudang_sistem,
    b.stok_akhir AS stok_barang_sistem,
    FALSE AS not_in_barang
FROM scope s
JOIN barang b ON b.id = s.barang_id
LEFT JOIN stok_akhir sa ON sa.barang_id = s.barang_id AND sa.gudang_id = %s
LEFT JOIN net_beli nb ON nb.barang_id = s.barang_id
LEFT JOIN net_jual nj ON nj.barang_id = s.barang_id
LEFT JOIN net_retur_beli nrb ON nrb.barang_id = s.barang_id
LEFT JOIN net_retur_jual nrj ON nrj.barang_id = s.barang_id
LEFT JOIN net_tf_masuk ntm ON ntm.barang_id = s.barang_id
LEFT JOIN net_tf_keluar ntk ON ntk.barang_id = s.barang_id
LEFT JOIN net_penyesuaian np ON np.barang_id = s.barang_id
ORDER BY s.kode_barang
"""

OUTPUT_FIELDS = [
    ("no", "No"),
    ("kode_barang", "Kode Barang"),
    ("nama_barang", "Nama Barang"),
    ("in_41k", "Dalam 41k"),
    ("baseline", "Baseline (Excel/0)"),
    ("net_pembelian", "+ Pembelian"),
    ("net_penjualan", "- Penjualan"),
    ("net_retur_beli", "+ Retur Beli"),
    ("net_retur_jual", "- Retur Jual"),
    ("net_transfer_masuk", "+ Transfer Masuk"),
    ("net_transfer_keluar", "- Transfer Keluar"),
    ("net_penyesuaian", "+/- Penyesuaian"),
    ("stok_hitung", "Stok Hitung"),
    ("stok_gudang_sistem", "Stok Gudang (Sistem)"),
    ("stok_barang_sistem", "Stok Barang (Sistem)"),
    ("selisih_gudang", "Selisih vs Gudang"),
    ("selisih_barang", "Selisih vs Barang"),
    ("ada_trx", "Ada Trx"),
    ("not_in_barang", "Not in Barang"),
]


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _enrich_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, row in enumerate(rows, start=1):
        hitung = int(row.get("stok_hitung") or 0)
        gudang = int(row.get("stok_gudang_sistem") or 0) if row.get("stok_gudang_sistem") is not None else 0
        barang = int(row.get("stok_barang_sistem") or 0) if row.get("stok_barang_sistem") is not None else 0
        trx_keys = (
            "net_pembelian", "net_penjualan", "net_retur_beli", "net_retur_jual",
            "net_transfer_masuk", "net_transfer_keluar", "net_penyesuaian",
        )
        ada_trx = any(int(row.get(k) or 0) != 0 for k in trx_keys)
        out.append({
            **row,
            "no": i,
            "in_41k": "Y" if row.get("in_41k") else "N",
            "baseline": int(row.get("baseline") or 0),
            "stok_hitung": hitung,
            "stok_gudang_sistem": gudang if row.get("stok_gudang_sistem") is not None else "",
            "stok_barang_sistem": int(barang) if row.get("stok_barang_sistem") is not None else "",
            "selisih_gudang": hitung - gudang if row.get("stok_gudang_sistem") is not None else hitung,
            "selisih_barang": hitung - int(barang) if row.get("stok_barang_sistem") is not None else hitung,
            "ada_trx": "Y" if ada_trx else "N",
            "not_in_barang": "Y" if row.get("not_in_barang") else "N",
        })
    return out


def fetch_stok_hitung_41k(
    pg,
    *,
    names: list[str],
    stoks: list[int],
    gudang_id: int,
    since: date | None = None,
) -> list[dict]:
    since = since or DEFAULT_SINCE
    params = (
        names, stoks,
        gudang_id, since,
        since, gudang_id,
        gudang_id, since,
        since, gudang_id,
        gudang_id, since,
        gudang_id, since,
        gudang_id, since,
        gudang_id,
    )
    with pg.cursor() as cur:
        cur.execute(STOK_HITUNG_41K_SQL, params)
        return _enrich_rows(_rows_to_dicts(cur))


def fetch_stok_hitung_luar_41k(
    pg,
    *,
    names: list[str],
    gudang_id: int,
) -> list[dict]:
    params = (
        names,
        gudang_id, gudang_id, gudang_id, gudang_id, gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
    )
    with pg.cursor() as cur:
        cur.execute(STOK_HITUNG_LUAR_41K_SQL, params)
        return _enrich_rows(_rows_to_dicts(cur))


def build_stats(rows_41k: list[dict], rows_luar: list[dict], *, gudang: str, since: date, source_file: str, pg_database: str) -> dict:
    all_rows = rows_41k + rows_luar
    selisih_g = [r for r in all_rows if r.get("stok_gudang_sistem") != "" and int(r.get("selisih_gudang") or 0) != 0]
    selisih_b = [r for r in all_rows if r.get("stok_barang_sistem") != "" and int(r.get("selisih_barang") or 0) != 0]
    return {
        "source_file": source_file,
        "pg_database": pg_database,
        "gudang": gudang,
        "since_date": since.isoformat(),
        "total_41k": len(rows_41k),
        "total_luar_41k": len(rows_luar),
        "selisih_gudang_count": len(selisih_g),
        "selisih_barang_count": len(selisih_b),
        "selisih_gudang_41k": sum(1 for r in rows_41k if r.get("stok_gudang_sistem") != "" and int(r.get("selisih_gudang") or 0) != 0),
        "selisih_gudang_luar": sum(1 for r in rows_luar if r.get("stok_gudang_sistem") != "" and int(r.get("selisih_gudang") or 0) != 0),
        "ada_trx_41k": sum(1 for r in rows_41k if r.get("ada_trx") == "Y"),
        "ada_trx_luar": sum(1 for r in rows_luar if r.get("ada_trx") == "Y"),
        "total_baseline_41k": sum(int(r.get("baseline") or 0) for r in rows_41k),
        "total_stok_hitung_41k": sum(int(r.get("stok_hitung") or 0) for r in rows_41k),
        "total_stok_hitung_luar": sum(int(r.get("stok_hitung") or 0) for r in rows_luar),
    }
