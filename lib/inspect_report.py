from __future__ import annotations

from lib.db import fetch_one
from lib.progress import Spinner

KODE_AKTIF_COUNT_SQL = """
    SELECT COUNT(DISTINCT TRIM(sd.cSTDcode)) AS total
    FROM stock s
    INNER JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
      AND sd.cSTDcode IS NOT NULL
      AND TRIM(sd.cSTDcode) <> ''
"""

TRX_NET_NONZERO_SQL = """
    SELECT COUNT(*) AS c FROM (
        SELECT cIVDfkSTK,
            SUM(COALESCE(nIVDqtyin,0) - COALESCE(nIVDqtyout,0)) AS net
        FROM invoicedetail
        WHERE cIVDfkSTK IS NOT NULL AND cIVDfkSTK <> ''
        GROUP BY cIVDfkSTK
        HAVING net <> 0
    ) t
"""

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

PG_STOK_AKHIR_PER_GUDANG_SQL = """
    SELECT
        g.id AS gudang_id,
        g.name AS gudang_name,
        COUNT(sa.id) AS baris_total,
        COUNT(DISTINCT sa.barang_id) AS barang_unik,
        SUM(CASE WHEN sa.stok_akhir > 0 THEN 1 ELSE 0 END) AS baris_positif,
        SUM(CASE WHEN sa.stok_akhir < 0 THEN 1 ELSE 0 END) AS baris_negatif,
        SUM(CASE WHEN sa.stok_akhir = 0 THEN 1 ELSE 0 END) AS baris_nol
    FROM master_gudang g
    LEFT JOIN stok_akhir sa
        ON sa.gudang_id = g.id AND sa.deleted_at IS NULL
    WHERE g.deleted_at IS NULL
    GROUP BY g.id, g.name
    ORDER BY g.id
"""

MYSQL_PPN_GOLONGAN_FILTER = """
    (UPPER(g.cGRPdesc) LIKE '%PPN%' OR UPPER(g.cGRPdesc) LIKE '%PKP%')
"""

MYSQL_PPN_BARANG_SQL = f"""
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN COALESCE(t.net, 0) > 0 THEN 1 ELSE 0 END) AS stok_positif,
        SUM(CASE WHEN COALESCE(t.net, 0) < 0 THEN 1 ELSE 0 END) AS stok_negatif,
        SUM(CASE WHEN COALESCE(t.net, 0) = 0 THEN 1 ELSE 0 END) AS stok_nol
    FROM stock s
    INNER JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
    LEFT JOIN ({STOCK_NET_SUBQUERY}) t ON t.cIVDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
      AND {MYSQL_PPN_GOLONGAN_FILTER}
"""

MYSQL_PPN_GOLONGAN_COUNT_SQL = f"""
    SELECT COUNT(*) AS total
    FROM stockgroup g
    WHERE {MYSQL_PPN_GOLONGAN_FILTER}
"""

MYSQL_PAJAK_PPN_STOK_SQL = f"""
    SELECT
        COUNT(*) AS total_kode,
        SUM(CASE WHEN net > 0 THEN 1 ELSE 0 END) AS stok_positif,
        SUM(CASE WHEN net < 0 THEN 1 ELSE 0 END) AS stok_negatif,
        SUM(CASE WHEN net = 0 THEN 1 ELSE 0 END) AS stok_nol
    FROM (
        SELECT
            sd.kode_barang,
            SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net
        FROM invoicedetail d
        INNER JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
        INNER JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
        INNER JOIN (
            SELECT cSTDfkSTK, MIN(TRIM(cSTDcode)) AS kode_barang
            FROM stockdetail
            WHERE cSTDcode IS NOT NULL AND TRIM(cSTDcode) <> ''
            GROUP BY cSTDfkSTK
        ) sd ON sd.cSTDfkSTK = s.cSTKpk
        WHERE s.nSTKsuspend = 0
          AND {MYSQL_PPN_GOLONGAN_FILTER}
        GROUP BY sd.kode_barang
    ) t
"""

PG_PPN_BARANG_SQL = """
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN COALESCE(b.stok_akhir, 0) > 0 THEN 1 ELSE 0 END) AS stok_positif,
        SUM(CASE WHEN COALESCE(b.stok_akhir, 0) < 0 THEN 1 ELSE 0 END) AS stok_negatif,
        SUM(CASE WHEN COALESCE(b.stok_akhir, 0) = 0 THEN 1 ELSE 0 END) AS stok_nol
    FROM barang b
    INNER JOIN brands br ON br.id = b.brand_id AND br.deleted_at IS NULL
    WHERE b.deleted_at IS NULL
      AND COALESCE(br.is_ppn, false) = true
"""

PG_PPN_BRAND_COUNT_SQL = """
    SELECT COUNT(*) AS total
    FROM brands
    WHERE deleted_at IS NULL
      AND COALESCE(is_ppn, false) = true
"""

PG_PPN_STOK_PAJAK_SQL = """
    SELECT
        COUNT(*) AS baris_stok_akhir,
        COUNT(DISTINCT sa.barang_id) AS barang_unik,
        SUM(CASE WHEN COALESCE(sa.stok_pajak, 0) > 0 THEN 1 ELSE 0 END) AS stok_pajak_positif,
        SUM(CASE WHEN COALESCE(sa.stok_pajak, 0) < 0 THEN 1 ELSE 0 END) AS stok_pajak_negatif,
        SUM(CASE WHEN COALESCE(sa.stok_pajak, 0) = 0 THEN 1 ELSE 0 END) AS stok_pajak_nol
    FROM stok_akhir sa
    INNER JOIN barang b ON b.id = sa.barang_id AND b.deleted_at IS NULL
    INNER JOIN brands br ON br.id = b.brand_id AND br.deleted_at IS NULL
    WHERE sa.deleted_at IS NULL
      AND COALESCE(br.is_ppn, false) = true
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


def _print_stok_tiga_kolom(label: str, total: int, pos: int, neg: int, nol: int) -> None:
    if total <= 0:
        print(f"  {label}: 0")
        return
    print(f"  {label}: {total:,}")
    print(f"    Stok > 0         : {pos:,} ({pos / total * 100:.1f}%)")
    print(f"    Stok < 0 (minus) : {neg:,} ({neg / total * 100:.1f}%)")
    print(f"    Stok = 0         : {nol:,} ({nol / total * 100:.1f}%)")


def _dict_row(cursor, row) -> dict:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _mysql_count(mysql, sql: str) -> int:
    with mysql.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        if isinstance(row, dict):
            return int(next(iter(row.values())))
        return int(row[0])


def _count_kode_aktif(mysql) -> int:
    return _mysql_count(mysql, KODE_AKTIF_COUNT_SQL)


def print_entity_comparison(
    mysql_master,
    mysql_ngawi,
    mysql_caruban,
    pg,
) -> None:
    """Bandingkan entitas master vs web, termasuk konteks 3 cabang."""
    checks = [
        ("Supplier", "SELECT COUNT(*) FROM entity WHERE nENTsupp = 1", "supplier", None),
        ("Golongan", "SELECT COUNT(*) FROM stockgroup", "brands", "brand auto cabang/pajak"),
        (
            "Gudang",
            "SELECT COUNT(*) FROM warehouse",
            "master_gudang",
            "web: Pusat + Ngawi + Caruban",
        ),
        (
            "Store",
            "SELECT COUNT(*) FROM outlet",
            "master_store",
            "web: 3 store (per cabang)",
        ),
    ]

    print("=== INSPECT — perbandingan MySQL vs PostgreSQL ===\n")
    print(f"{'Entitas':<12}{'MySQL master':<18}{'PostgreSQL':<14}Catatan")
    print("-" * 72)

    with mysql_master.cursor() as mcur, pg.cursor() as pcur:
        for label, mysql_sql, pg_table, note in checks:
            with Spinner(f"Membandingkan {label}..."):
                mcur.execute(mysql_sql)
                mysql_count = int(next(iter(mcur.fetchone().values())))
                pcur.execute(f"SELECT COUNT(*) FROM {pg_table}")
                pg_count = int(pcur.fetchone()[0])
            delta = pg_count - mysql_count
            if note:
                catatan = note
            elif delta == 0:
                catatan = "ok"
            else:
                catatan = f"{'+' if delta > 0 else ''}{delta:,}"
            print(
                f"{label:<12}{mysql_count:<18}{pg_count:<14}{catatan}"
            )

        with Spinner("Membandingkan barang (master)..."):
            mcur.execute("SELECT COUNT(*) FROM stock WHERE nSTKsuspend = 0")
            master_barang = int(next(iter(mcur.fetchone().values())))
            pcur.execute("SELECT COUNT(*) FROM barang WHERE deleted_at IS NULL")
            pg_barang = int(pcur.fetchone()[0])

    print(f"{'Barang':<12}{master_barang:<18}{pg_barang:<14}lihat breakdown ↓")

    print("\n=== Katalog barang — master + 3 cabang ===")
    branch_sources: list[tuple[str, object | None]] = [
        ("evacer (master Sragen)", mysql_master),
        ("Ngawi backup", mysql_ngawi),
        ("Caruban backup", mysql_caruban),
    ]
    kode_per_sumber: dict[str, int] = {}
    for label, conn in branch_sources:
        if conn is None:
            print(f"  {label:<22}: [belum dikonfigurasi di .env]")
            continue
        with Spinner(f"Menghitung kode aktif {label}..."):
            kode_per_sumber[label] = _count_kode_aktif(conn)
        print(f"  {label:<22}: {kode_per_sumber[label]:,} kode")

    map_master = fetch_one(pg, "SELECT COUNT(*) FROM migration_id_map WHERE entity_type = 'barang'") or 0
    map_ngawi = fetch_one(pg, "SELECT COUNT(*) FROM migration_id_map WHERE entity_type = 'barang_ngawi'") or 0
    map_caruban = fetch_one(pg, "SELECT COUNT(*) FROM migration_id_map WHERE entity_type = 'barang_caruban'") or 0
    map_pajak = fetch_one(pg, "SELECT COUNT(*) FROM migration_id_map WHERE entity_type = 'barang_pajak'") or 0

    print(f"\n  PostgreSQL katalog web : {pg_barang:,} barang")
    print(f"    └ map master (Sragen) : {int(map_master):,}")
    print(f"    └ map cabang Ngawi    : {int(map_ngawi):,}")
    print(f"    └ map cabang Caruban  : {int(map_caruban):,}")
    print(f"    └ map barang pajak    : {int(map_pajak):,}")

    tambahan = pg_barang - master_barang
    if tambahan > 0:
        print(
            f"  Selisih vs master      : +{tambahan:,} "
            f"(cabang ~{int(map_ngawi) + int(map_caruban):,}, pajak ~{int(map_pajak):,})"
        )
    elif tambahan < 0:
        print(f"  Selisih vs master      : {tambahan:,}")
    else:
        print("  Selisih vs master      : 0")


def print_trx_stock_by_branch(
    mysql_trx,
    mysql_ngawi,
    mysql_caruban,
) -> None:
    """Ringkasan ledger stok per backup cabang."""
    print("\n=== Stok dari transaksi (per cabang / backup) ===")
    branches: list[tuple[str, object | None]] = [
        ("Sragen / Pusat", mysql_trx),
        ("Ngawi", mysql_ngawi),
        ("Caruban", mysql_caruban),
    ]
    for label, conn in branches:
        if conn is None:
            print(f"\n  [{label}] [belum dikonfigurasi di .env]")
            continue
        try:
            with Spinner(f"Menghitung ledger {label}..."):
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS total FROM invoicedetail")
                    total = int(cur.fetchone()["total"])
                    cur.execute(TRX_NET_NONZERO_SQL)
                    non_zero = int(cur.fetchone()["c"])
            print(f"\n  [{label}]")
            print(f"    invoicedetail rows     : {total:,}")
            print(f"    kode net stok != 0     : {non_zero:,}")
        except Exception as exc:
            print(f"\n  [{label}] [skip] {exc}")


def print_ppn_inspect_stats(mysql_master, mysql_trx, mysql_pajak, pg) -> None:
    """Ringkasan barang PPN dan stok pajak (MySQL vs PostgreSQL)."""
    print("\n=== Barang PPN / Stok Pajak ===")

    try:
        with Spinner("Menghitung barang PPN di MySQL master..."):
            with mysql_master.cursor() as cur:
                cur.execute(MYSQL_PPN_GOLONGAN_COUNT_SQL)
                golongan_ppn = _row_int(cur.fetchone(), "total")
            with mysql_trx.cursor() as cur:
                cur.execute(MYSQL_PPN_BARANG_SQL)
                row = _dict_row(cur, cur.fetchone())

        total = _row_int(row, "total")
        print(f"\n  MySQL master (golongan PPN/PKP: {golongan_ppn:,})")
        _print_stok_tiga_kolom(
            "Barang PPN (stok fisik dari full backup)",
            total,
            _row_int(row, "stok_positif"),
            _row_int(row, "stok_negatif"),
            _row_int(row, "stok_nol"),
        )
        print("  Catatan: stok fisik = net invoicedetail (evacer_fullbackup).")
    except Exception as exc:
        print(f"\n  MySQL master PPN: [skip] {exc}")

    try:
        with Spinner("Menghitung stok pajak PPN di MySQL DB pajak..."):
            with mysql_pajak.cursor() as cur:
                cur.execute(MYSQL_PAJAK_PPN_STOK_SQL)
                row = _dict_row(cur, cur.fetchone())

        total = _row_int(row, "total_kode")
        print(f"\n  MySQL DB pajak (ledger PPN/PKP)")
        _print_stok_tiga_kolom(
            "Kode barang PPN (stok pajak dari ledger)",
            total,
            _row_int(row, "stok_positif"),
            _row_int(row, "stok_negatif"),
            _row_int(row, "stok_nol"),
        )
        non_zero = _row_int(row, "stok_positif") + _row_int(row, "stok_negatif")
        print(f"    Kode net stok pajak != 0: {non_zero:,}")
    except Exception as exc:
        print(f"\n  MySQL DB pajak PPN: [skip] {exc}")

    try:
        with Spinner("Menghitung barang PPN di PostgreSQL..."):
            with pg.cursor() as cur:
                cur.execute(PG_PPN_BRAND_COUNT_SQL)
                brand_ppn = _row_int(_dict_row(cur, cur.fetchone()), "total")
                cur.execute(PG_PPN_BARANG_SQL)
                barang_row = _dict_row(cur, cur.fetchone())
                cur.execute(PG_PPN_STOK_PAJAK_SQL)
                pajak_row = _dict_row(cur, cur.fetchone())

        total = _row_int(barang_row, "total")
        print(f"\n  PostgreSQL web (brand PPN: {brand_ppn:,})")
        _print_stok_tiga_kolom(
            "Barang PPN (kolom barang.stok_akhir / fisik)",
            total,
            _row_int(barang_row, "stok_positif"),
            _row_int(barang_row, "stok_negatif"),
            _row_int(barang_row, "stok_nol"),
        )

        ppn_total = _row_int(pajak_row, "barang_unik")
        if ppn_total > 0:
            print(f"\n  PostgreSQL stok_akhir — barang PPN: {ppn_total:,}")
            pos = _row_int(pajak_row, "stok_pajak_positif")
            neg = _row_int(pajak_row, "stok_pajak_negatif")
            nol = _row_int(pajak_row, "stok_pajak_nol")
            print(f"    stok_pajak > 0     : {pos:,}")
            print(f"    stok_pajak < 0     : {neg:,}")
            print(f"    stok_pajak = 0     : {nol:,}")
            if pos + neg == 0 and total > 0:
                print("    ⚠ stok_pajak belum terisi — jalankan: ./run.sh run --only=stok_pajak")
        else:
            print("\n  PostgreSQL: belum ada barang PPN di stok_akhir.")
    except Exception as exc:
        print(f"\n  PostgreSQL PPN: [skip] {exc}")


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

                cur.execute(PG_STOK_AKHIR_PER_GUDANG_SQL)
                gudang_cols = [d[0] for d in cur.description]
                gudang_rows = [dict(zip(gudang_cols, r)) for r in cur.fetchall()]

        _print_breakdown("Breakdown Barang — PostgreSQL (hasil migrasi)", row)

        total_barang = _row_int(row, "total")
        baris = _row_int(stok_row, "baris_stok_akhir")
        if baris:
            print(f"\n  Total barang (katalog web): {total_barang:,}")

            print(f"\n  Tabel stok_akhir (agregat semua gudang):")
            print(f"    Baris total          : {baris:,}")
            print(f"    Barang unik          : {_row_int(stok_row, 'barang_punya_stok_akhir'):,}")
            print(f"    Baris stok > 0       : {_row_int(stok_row, 'baris_positif'):,}")
            print(f"    Baris stok < 0       : {_row_int(stok_row, 'baris_negatif'):,}")
            print(f"    Baris stok = 0       : {_row_int(stok_row, 'baris_nol'):,}")

            print(f"\n  stok_akhir per gudang (katalog {total_barang:,} barang):")
            cabang_terisi = False
            for grow in gudang_rows:
                name = grow.get("gudang_name") or f"gudang_id={grow.get('gudang_id')}"
                punya_row = _row_int(grow, "baris_total")
                tanpa_row = max(0, total_barang - punya_row)
                print(f"    {name}")
                print(f"      punya row stok       : {punya_row:,} / {total_barang:,}")
                print(f"      tanpa row (stok 0)   : {tanpa_row:,}")
                print(f"      stok > 0 / < 0 / 0   : {_row_int(grow, 'baris_positif'):,} / "
                      f"{_row_int(grow, 'baris_negatif'):,} / {_row_int(grow, 'baris_nol'):,}")
                hint = (name or "").lower()
                if punya_row > 0 and ("ngawi" in hint or "caruban" in hint):
                    cabang_terisi = True

            if cabang_terisi:
                print("  Catatan: stok fisik sudah terpisah per gudang (Pusat + cabang).")
            else:
                print("  Catatan: stok baru di Gudang Pusat — jalankan stok_cabang untuk Ngawi/Caruban.")
    except Exception as exc:
        print(f"\n=== Breakdown Barang — PostgreSQL ===")
        print(f"  [skip] {exc}")
