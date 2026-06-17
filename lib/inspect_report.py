from __future__ import annotations

from lib.progress import Spinner

STOCK_NET_SUBQUERY = """
    SELECT cIVDfkSTK,
        SUM(COALESCE(nIVDqtyin, 0) - COALESCE(nIVDqtyout, 0)) AS net
    FROM invoicedetail
    WHERE cIVDfkSTK IS NOT NULL AND cIVDfkSTK <> ''
    GROUP BY cIVDfkSTK
"""

MYSQL_BARANG_BREAKDOWN_SQL = f"""
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN COALESCE(s.nstkkonsi, 0) = 1 THEN 1 ELSE 0 END) AS konsinyasi,
        SUM(CASE WHEN COALESCE(s.nstkkonsi, 0) <> 1 THEN 1 ELSE 0 END) AS regular,
        SUM(CASE WHEN COALESCE(t.net, 0) > 0 THEN 1 ELSE 0 END) AS stok_positif,
        SUM(CASE WHEN COALESCE(t.net, 0) < 0 THEN 1 ELSE 0 END) AS stok_negatif,
        SUM(CASE WHEN COALESCE(t.net, 0) = 0 THEN 1 ELSE 0 END) AS stok_nol,
        SUM(CASE WHEN COALESCE(s.nstkkonsi, 0) = 1 AND COALESCE(t.net, 0) > 0 THEN 1 ELSE 0 END)
            AS konsi_stok_positif,
        SUM(CASE WHEN COALESCE(s.nstkkonsi, 0) = 1 AND COALESCE(t.net, 0) < 0 THEN 1 ELSE 0 END)
            AS konsi_stok_negatif,
        SUM(CASE WHEN COALESCE(s.nSTKopen, 0) > 0 AND COALESCE(t.net, 0) = 0 THEN 1 ELSE 0 END)
            AS stok_pembukaan_saja
    FROM stock s
    LEFT JOIN ({STOCK_NET_SUBQUERY}) t ON t.cIVDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
"""

PG_BARANG_BREAKDOWN_SQL = """
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN is_consignment = true THEN 1 ELSE 0 END) AS konsinyasi,
        SUM(CASE WHEN COALESCE(is_consignment, false) = false THEN 1 ELSE 0 END) AS regular,
        SUM(CASE WHEN COALESCE(stok_akhir, 0) > 0 THEN 1 ELSE 0 END) AS stok_positif,
        SUM(CASE WHEN COALESCE(stok_akhir, 0) < 0 THEN 1 ELSE 0 END) AS stok_negatif,
        SUM(CASE WHEN COALESCE(stok_akhir, 0) = 0 THEN 1 ELSE 0 END) AS stok_nol,
        SUM(CASE WHEN is_consignment = true AND COALESCE(stok_akhir, 0) > 0 THEN 1 ELSE 0 END)
            AS konsi_stok_positif,
        SUM(CASE WHEN is_consignment = true AND COALESCE(stok_akhir, 0) < 0 THEN 1 ELSE 0 END)
            AS konsi_stok_negatif,
        SUM(CASE WHEN type = 'consignment' THEN 1 ELSE 0 END) AS type_konsinyasi
    FROM barang
    WHERE deleted_at IS NULL
"""

PG_STOK_AKHIR_SQL = """
    SELECT
        COUNT(*) AS baris_stok_akhir,
        COUNT(DISTINCT barang_id) AS barang_punya_stok_akhir,
        SUM(CASE WHEN stok_akhir > 0 THEN 1 ELSE 0 END) AS baris_positif,
        SUM(CASE WHEN stok_akhir < 0 THEN 1 ELSE 0 END) AS baris_negatif,
        SUM(CASE WHEN stok_akhir = 0 THEN 1 ELSE 0 END) AS baris_nol
    FROM stok_akhir
    WHERE deleted_at IS NULL
"""


def _row_int(row: dict, key: str) -> int:
    value = row.get(key)
    if value is None:
        return 0
    return int(value)


def _print_breakdown(title: str, row: dict) -> None:
    total = _row_int(row, "total")
    if total <= 0:
        print(f"\n=== {title} ===")
        print("  (tidak ada data)")
        return

    konsi = _row_int(row, "konsinyasi")
    regular = _row_int(row, "regular")
    pos = _row_int(row, "stok_positif")
    neg = _row_int(row, "stok_negatif")
    nol = _row_int(row, "stok_nol")
    konsi_pos = _row_int(row, "konsi_stok_positif")
    konsi_neg = _row_int(row, "konsi_stok_negatif")

    print(f"\n=== {title} ===")
    print(f"  Total barang       : {total:,}")
    print(f"  Regular            : {regular:,} ({regular / total * 100:.1f}%)")
    print(f"  Konsinyasi         : {konsi:,} ({konsi / total * 100:.1f}%)")
    print(f"  Stok > 0           : {pos:,} ({pos / total * 100:.1f}%)")
    print(f"  Stok < 0 (minus)   : {neg:,} ({neg / total * 100:.1f}%)")
    print(f"  Stok = 0           : {nol:,} ({nol / total * 100:.1f}%)")
    if konsi > 0:
        print(f"    └ konsi stok > 0 : {konsi_pos:,}")
        print(f"    └ konsi stok < 0 : {konsi_neg:,}")

    pembukaan = _row_int(row, "stok_pembukaan_saja")
    if pembukaan:
        print(f"  Stok pembukaan saja (net transaksi = 0): {pembukaan:,}")


def print_mysql_barang_stats(mysql_master, mysql_trx) -> None:
    try:
        with Spinner("Menghitung breakdown barang MySQL (bisa 1–2 menit)..."):
            with mysql_trx.cursor() as cur:
                cur.execute(MYSQL_BARANG_BREAKDOWN_SQL)
                row = cur.fetchone()
        _print_breakdown("Breakdown Barang — MySQL (sumber migrasi)", row)
        print("  Catatan: stok dihitung dari net invoicedetail (DB full backup).")
    except Exception as exc:
        print(f"\n=== Breakdown Barang — MySQL ===")
        print(f"  [skip] {exc}")


def print_pg_barang_stats(pg) -> None:
    try:
        with Spinner("Menghitung breakdown barang PostgreSQL..."):
            with pg.cursor() as cur:
                cur.execute(PG_BARANG_BREAKDOWN_SQL)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description]
                row = dict(zip(cols, row))

                cur.execute(PG_STOK_AKHIR_SQL)
                stok_row = cur.fetchone()
                stok_cols = [d[0] for d in cur.description]
                stok_row = dict(zip(stok_cols, stok_row))

        _print_breakdown("Breakdown Barang — PostgreSQL (hasil migrasi)", row)

        baris = _row_int(stok_row, "baris_stok_akhir")
        if baris:
            print(f"\n  Tabel stok_akhir (per gudang):")
            print(f"    Baris total          : {baris:,}")
            print(f"    Barang unik          : {_row_int(stok_row, 'barang_punya_stok_akhir'):,}")
            print(f"    Baris stok > 0       : {_row_int(stok_row, 'baris_positif'):,}")
            print(f"    Baris stok < 0       : {_row_int(stok_row, 'baris_negatif'):,}")
            print(f"    Baris stok = 0       : {_row_int(stok_row, 'baris_nol'):,}")
            print("  Catatan: stok saat ini masuk gudang default (Sragen), belum per cabang.")
    except Exception as exc:
        print(f"\n=== Breakdown Barang — PostgreSQL ===")
        print(f"  [skip] {exc}")
