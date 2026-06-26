from __future__ import annotations

from datetime import date

TRX_FLAGS_SQL = """
WITH target AS (
    SELECT DISTINCT ON (v.nama)
        b.id AS barang_id,
        b.kode_barang,
        COALESCE(b.nama, v.nama) AS nama_barang,
        v.stok_excel
    FROM unnest(%s::text[], %s::int[]) AS v(nama, stok_excel)
    LEFT JOIN barang b
           ON b.nama = v.nama
          AND b.deleted_at IS NULL
    ORDER BY v.nama, b.id NULLS LAST
),
beli AS (
    SELECT pd.barang_id, COUNT(DISTINCT p.id) AS cnt,
           MAX(mg.name) AS gudang_pembelian
    FROM pembelian_detail pd
    JOIN pembelian p ON p.id = pd.pembelian_id AND p.deleted_at IS NULL
    LEFT JOIN master_gudang mg ON mg.id = p.gudang_id
    WHERE pd.deleted_at IS NULL
      AND (%s::date IS NULL OR p.tanggal >= %s::date)
      AND (%s::bigint IS NULL OR p.gudang_id = %s::bigint)
    GROUP BY pd.barang_id
),
jual AS (
    SELECT pd.barang_id, COUNT(DISTINCT p.id) AS cnt
    FROM penjualan_detail pd
    JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
    WHERE pd.deleted_at IS NULL
      AND (%s::date IS NULL OR p.tanggal::date >= %s::date)
    GROUP BY pd.barang_id
),
jual_gudang AS (
    SELECT
        pd.barang_id,
        string_agg(DISTINCT mg.name, ', ' ORDER BY mg.name) AS gudang_kasir_penjualan,
        string_agg(DISTINCT u_kasir.name, ', ' ORDER BY u_kasir.name) AS kasir_penjualan
    FROM penjualan_detail pd
    JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
    LEFT JOIN users u_kasir ON u_kasir.id = COALESCE(p.sales_id, p.created_by)
    LEFT JOIN master_store ms ON ms.id = u_kasir.store_id
    LEFT JOIN master_gudang mg ON mg.id = ms.gudang_id
    WHERE pd.deleted_at IS NULL
      AND (%s::date IS NULL OR p.tanggal::date >= %s::date)
    GROUP BY pd.barang_id
),
transfer AS (
    SELECT tgd.barang_id, COUNT(DISTINCT tg.id) AS cnt
    FROM transfer_gudang_detail tgd
    JOIN transfer_gudang tg ON tg.id = tgd.transfer_gudang_id AND tg.deleted_at IS NULL
    WHERE tgd.deleted_at IS NULL
      AND (%s::date IS NULL OR tg.tanggal_transfer >= %s::date)
      AND (
            %s::bigint IS NULL
         OR tg.gudang_asal_id = %s::bigint
         OR tg.gudang_tujuan_id = %s::bigint
      )
    GROUP BY tgd.barang_id
),
retur_beli AS (
    SELECT prd.barang_id, COUNT(DISTINCT pr.id) AS cnt
    FROM pembelian_return_detail prd
    JOIN pembelian_return pr ON pr.id = prd.pembelian_return_id AND pr.deleted_at IS NULL
    WHERE prd.deleted_at IS NULL
      AND (%s::bigint IS NULL OR pr.gudang_id = %s::bigint)
    GROUP BY prd.barang_id
),
retur_jual AS (
    SELECT prd.barang_id, COUNT(DISTINCT pr.id) AS cnt
    FROM penjualan_return_detail prd
    JOIN penjualan_return pr ON pr.id = prd.penjualan_return_id AND pr.deleted_at IS NULL
    WHERE prd.status = 1
    GROUP BY prd.barang_id
),
penyesuaian AS (
    SELECT psd.barang_id, COUNT(DISTINCT ps.id) AS cnt
    FROM penyesuaian_stok_detail psd
    JOIN penyesuaian_stok ps ON ps.id = psd.penyesuaian_stok_id AND ps.deleted_at IS NULL
    WHERE (%s::date IS NULL OR ps.tanggal_transaksi >= %s::date)
      AND (%s::bigint IS NULL OR ps.gudang_id = %s::bigint)
    GROUP BY psd.barang_id
)
SELECT
    t.barang_id,
    t.kode_barang,
    t.nama_barang,
    t.stok_excel,
    b.stok_akhir AS stok_barang_system,
    sa.stok_akhir AS stok_gudang_pusat,
    COALESCE(beli.cnt, 0) AS cnt_pembelian,
    beli.gudang_pembelian,
    COALESCE(jual.cnt, 0) AS cnt_penjualan,
    jg.gudang_kasir_penjualan,
    jg.kasir_penjualan,
    COALESCE(transfer.cnt, 0) AS cnt_transfer,
    COALESCE(retur_beli.cnt, 0) AS cnt_retur_beli,
    COALESCE(retur_jual.cnt, 0) AS cnt_retur_jual,
    COALESCE(penyesuaian.cnt, 0) AS cnt_penyesuaian,
    (t.barang_id IS NULL) AS not_in_barang
FROM target t
LEFT JOIN barang b ON b.id = t.barang_id
LEFT JOIN stok_akhir sa ON sa.barang_id = t.barang_id AND sa.gudang_id = %s
LEFT JOIN beli ON beli.barang_id = t.barang_id
LEFT JOIN jual ON jual.barang_id = t.barang_id
LEFT JOIN jual_gudang jg ON jg.barang_id = t.barang_id
LEFT JOIN transfer ON transfer.barang_id = t.barang_id
LEFT JOIN retur_beli ON retur_beli.barang_id = t.barang_id
LEFT JOIN retur_jual ON retur_jual.barang_id = t.barang_id
LEFT JOIN penyesuaian ON penyesuaian.barang_id = t.barang_id
ORDER BY t.nama_barang NULLS LAST
"""

OUTPUT_FIELDS = [
    ("no", "No"),
    ("kode_barang", "Kode Barang"),
    ("nama_barang", "Nama Barang"),
    ("stok_excel", "Stok Excel"),
    ("stok_barang_system", "Stok System (Barang)"),
    ("stok_gudang_pusat", "Stok Gudang Pusat"),
    ("ada_pembelian", "Ada Pembelian"),
    ("cnt_pembelian", "Jml Faktur Beli"),
    ("gudang_pembelian", "Gudang Pembelian"),
    ("ada_penjualan", "Ada Penjualan"),
    ("cnt_penjualan", "Jml Faktur Jual"),
    ("gudang_kasir_penjualan", "Gudang Kasir (Penjualan)"),
    ("kasir_penjualan", "Kasir (Penjualan)"),
    ("ada_transfer", "Ada Transfer"),
    ("cnt_transfer", "Jml Transfer"),
    ("ada_retur_beli", "Ada Retur Beli"),
    ("cnt_retur_beli", "Jml Retur Beli"),
    ("ada_retur_jual", "Ada Retur Jual"),
    ("cnt_retur_jual", "Jml Retur Jual"),
    ("ada_penyesuaian", "Ada Penyesuaian"),
    ("cnt_penyesuaian", "Jml Penyesuaian"),
    ("ada_trx_apapun", "Ada Trx"),
    ("kategori_trx", "Kategori Trx"),
    ("not_in_barang", "Tidak Ada di Barang"),
]

SUMMARY_FIELDS = [
    ("total_barang_excel", "Total Barang Excel"),
    ("ada_trx", "Ada Transaksi (Y)"),
    ("tanpa_trx", "Tanpa Transaksi"),
    ("not_in_barang", "Tidak Ada di Master Barang"),
    ("ada_pembelian", "Ada Pembelian"),
    ("ada_penjualan", "Ada Penjualan"),
    ("ada_transfer", "Ada Transfer Gudang"),
    ("ada_retur_beli", "Ada Retur Pembelian"),
    ("ada_retur_jual", "Ada Retur Penjualan"),
    ("ada_penyesuaian", "Ada Penyesuaian Stok"),
    ("total_stok_excel", "Total Stok Excel"),
    ("total_stok_system", "Total Stok System"),
    ("total_stok_gudang", "Total Stok Gudang Pusat"),
]


def _kategori(row: dict) -> str:
    flags = []
    if row.get("ada_pembelian"):
        flags.append("BELI")
    if row.get("ada_penjualan"):
        flags.append("JUAL")
    if row.get("ada_transfer"):
        flags.append("TRANSFER")
    if row.get("ada_retur_beli"):
        flags.append("RETUR_BELI")
    if row.get("ada_retur_jual"):
        flags.append("RETUR_JUAL")
    if row.get("ada_penyesuaian"):
        flags.append("PENYESUAIAN")
    return "+".join(flags) if flags else "TIDAK ADA TRX"


def _bool_yes(value) -> str:
    return "Y" if value else "N"


def fetch_trx_flags(
    pg,
    *,
    names: list[str],
    stoks: list[int],
    gudang_id: int,
    since: date | None = None,
) -> list[dict]:
    params = (
        names,
        stoks,
        since,
        since,
        gudang_id,
        gudang_id,
        since,
        since,
        since,
        since,
        since,
        since,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        gudang_id,
        since,
        since,
        gudang_id,
        gudang_id,
        gudang_id,
    )
    with pg.cursor() as cur:
        cur.execute(TRX_FLAGS_SQL, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    for i, row in enumerate(rows, start=1):
        row["no"] = i
        row["ada_pembelian"] = row["cnt_pembelian"] > 0
        row["ada_penjualan"] = row["cnt_penjualan"] > 0
        row["ada_transfer"] = row["cnt_transfer"] > 0
        row["ada_retur_beli"] = row["cnt_retur_beli"] > 0
        row["ada_retur_jual"] = row["cnt_retur_jual"] > 0
        row["ada_penyesuaian"] = row["cnt_penyesuaian"] > 0
        row["ada_trx_apapun"] = any(
            (
                row["ada_pembelian"],
                row["ada_penjualan"],
                row["ada_transfer"],
                row["ada_retur_beli"],
                row["ada_retur_jual"],
                row["ada_penyesuaian"],
            )
        )
        if row.get("not_in_barang"):
            row["kategori_trx"] = "NOT_IN_BARANG"
        else:
            row["kategori_trx"] = _kategori(row)
    return rows


def build_stats(rows: list[dict], *, gudang: str, since: date | None, source_file: str) -> dict:
    def _sum_int(key: str) -> int:
        total = 0
        for row in rows:
            val = row.get(key)
            if val is not None and val != "":
                total += int(round(float(val)))
        return total

    return {
        "source_file": source_file,
        "gudang": gudang,
        "since_date": since.isoformat() if since else "SEMUA",
        "total_barang_excel": len(rows),
        "ada_trx": sum(1 for r in rows if r.get("ada_trx_apapun")),
        "tanpa_trx": sum(1 for r in rows if not r.get("ada_trx_apapun") and not r.get("not_in_barang")),
        "not_in_barang": sum(1 for r in rows if r.get("not_in_barang")),
        "ada_pembelian": sum(1 for r in rows if r.get("ada_pembelian")),
        "ada_penjualan": sum(1 for r in rows if r.get("ada_penjualan")),
        "ada_transfer": sum(1 for r in rows if r.get("ada_transfer")),
        "ada_retur_beli": sum(1 for r in rows if r.get("ada_retur_beli")),
        "ada_retur_jual": sum(1 for r in rows if r.get("ada_retur_jual")),
        "ada_penyesuaian": sum(1 for r in rows if r.get("ada_penyesuaian")),
        "total_stok_excel": _sum_int("stok_excel"),
        "total_stok_system": _sum_int("stok_barang_system"),
        "total_stok_gudang": _sum_int("stok_gudang_pusat"),
    }
