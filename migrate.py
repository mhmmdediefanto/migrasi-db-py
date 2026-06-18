#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Paket lokal (vendor) tanpa perlu install global
ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT))

from lib.config import load_mapping, load_settings
from lib.db import connect_mysql, connect_pg, ensure_id_map_table, fetch_one, truncate_master_data
from lib.inspect_report import (
    print_entity_comparison,
    print_mysql_barang_stats,
    print_pg_barang_stats,
    print_ppn_inspect_stats,
    print_trx_stock_by_branch,
)
from lib.progress import Spinner, print_step
from lib.cabang_migrators import migrate_barang_union, migrate_stok_cabang
from lib.pajak_migrators import export_pajak_kode_reports, migrate_barang_pajak, migrate_stok_pajak
from lib.migrators import (
    migrate_barang,
    migrate_brands,
    migrate_gudang,
    migrate_store,
    migrate_suppliers,
)

STEPS = {
    "supplier": migrate_suppliers,
    "brand": migrate_brands,
    "gudang": migrate_gudang,
    "store": migrate_store,
    "barang": migrate_barang,
    "stok_pajak": migrate_stok_pajak,
    "barang_pajak": migrate_barang_pajak,
    "barang_union": migrate_barang_union,
    "stok_cabang": migrate_stok_cabang,
}


def cmd_mapping() -> None:
    mapping = load_mapping()
    print("=== Mapping MySQL → PostgreSQL ===")
    for key, info in mapping.items():
        label = info.get("ui_label", key)
        if key == "stok":
            print(f"\n[{label}] Sumber stok desktop")
            for src_key, src_info in info.items():
                cols = src_info.get("columns") or src_info.get("column", "")
                print(f"  {src_key}: {src_info['table']}.{cols} — {src_info['note']}")
            continue
        source = info["source_table"]
        if info.get("source_filter"):
            source = f"{source} ({info['source_filter']})"
        print(f"\n[{label}] {source} → {info['target_table']}")
        for src, tgt in info.get("columns", {}).items():
            print(f"  {src} → {tgt}")
    print("\nGroup Barang (footwear) belum ada padanan di MySQL.")


def cmd_inspect(
    mysql_master,
    mysql_trx,
    mysql_trx_pajak,
    mysql_trx_ngawi,
    mysql_trx_caruban,
    pg,
) -> None:
    print_entity_comparison(
        mysql_master, mysql_trx_ngawi, mysql_trx_caruban, pg
    )

    with Spinner("Menghitung migration_id_map..."):
        mapped = fetch_one(pg, "SELECT COUNT(*) FROM migration_id_map") or 0
    print(f"\nID map tersimpan: {mapped:,} baris (tabel migration_id_map)")

    print("\n=== Stok di MySQL (desktop) ===")
    stock_checks = [
        (
            "Outlet (stockdetail)",
            "Menghitung stok outlet stockdetail...",
            """
            SELECT COUNT(*) FROM (
                SELECT cSTDfkSTK
                FROM stockdetail
                GROUP BY cSTDfkSTK
                HAVING SUM(
                    COALESCE(outlet01,0)+COALESCE(outlet02,0)+COALESCE(outlet03,0)
                    +COALESCE(outlet04,0)+COALESCE(outlet05,0)+COALESCE(outlet06,0)
                    +COALESCE(outlet07,0)+COALESCE(outlet08,0)+COALESCE(outlet09,0)
                    +COALESCE(outlet10,0)+COALESCE(outlet11,0)+COALESCE(outlet12,0)
                    +COALESCE(outlet13,0)+COALESCE(outlet14,0)+COALESCE(outlet15,0)
                    +COALESCE(outlet16,0)+COALESCE(outlet17,0)+COALESCE(outlet18,0)
                    +COALESCE(outlet19,0)+COALESCE(outlet20,0)
                ) > 0
            ) t
            """,
        ),
        (
            "Pembukaan (stock.nSTKopen > 0)",
            "Menghitung stok pembukaan (nSTKopen)...",
            "SELECT COUNT(*) FROM stock WHERE nSTKopen > 0",
        ),
    ]
    with mysql_master.cursor() as mcur:
        for label, spinner_msg, sql in stock_checks:
            with Spinner(spinner_msg):
                mcur.execute(sql)
                count = int(mcur.fetchone()["COUNT(*)"])
            print(f"  {label}: {count:,} barang")

    print_trx_stock_by_branch(mysql_trx, mysql_trx_ngawi, mysql_trx_caruban)

    print("\n=== Stok dari transaksi pajak (MySQL DB pajak) ===")
    try:
        with Spinner("Menghitung stok pajak dari invoicedetail..."):
            with mysql_trx_pajak.cursor() as mcur:
                mcur.execute("SELECT COUNT(*) AS total FROM invoicedetail")
                total = int(mcur.fetchone()["total"])
                mcur.execute(
                    """
                    SELECT COUNT(*) AS c FROM (
                        SELECT sd.kode_barang,
                            SUM(COALESCE(d.nIVDqtyin,0) - COALESCE(d.nIVDqtyout,0)) AS net
                        FROM invoicedetail d
                        INNER JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
                        INNER JOIN (
                            SELECT cSTDfkSTK, MIN(TRIM(cSTDcode)) AS kode_barang
                            FROM stockdetail
                            WHERE cSTDcode IS NOT NULL AND TRIM(cSTDcode) <> ''
                            GROUP BY cSTDfkSTK
                        ) sd ON sd.cSTDfkSTK = s.cSTKpk
                        WHERE s.nSTKsuspend = 0
                        GROUP BY sd.kode_barang
                        HAVING net <> 0
                    ) t
                    """
                )
                non_zero = int(mcur.fetchone()["c"])
        print(f"  invoicedetail rows: {total:,}")
        print(f"  kode barang dengan net stok pajak != 0: {non_zero:,}")
    except Exception as exc:
        print(f"  [skip] Tidak bisa baca DB pajak: {exc}")

    print_ppn_inspect_stats(mysql_master, mysql_trx, mysql_trx_pajak, pg)
    print_mysql_barang_stats(mysql_master, mysql_trx)
    print_pg_barang_stats(pg)
    print("\n✓ Inspect selesai.")


def cmd_run(
    mysql_master,
    mysql_trx,
    mysql_trx_pajak,
    mysql_trx_ngawi,
    mysql_trx_caruban,
    pg,
    settings,
    args,
) -> None:
    only = [item.strip() for item in args.only.split(",") if item.strip()]
    print("=== DRY RUN ===" if args.dry_run else "=== MIGRASI MASTER ===")

    if args.fresh:
        print("\n> BERSIHKAN DATA LAMA (PostgreSQL)")
        truncate_master_data(pg, only, dry_run=args.dry_run)

    step_labels = {
        "supplier": "Supplier",
        "brand": "Golongan (Brand)",
        "gudang": "Gudang",
        "store": "Store",
        "barang": "Barang",
        "stok_pajak": "Stok Pajak (update stok_akhir.stok_pajak)",
        "barang_pajak": "Barang Pajak (insert dari DB pajak)",
        "barang_union": "Barang Union (Ngawi + Caruban → katalog web)",
        "stok_cabang": "Stok Cabang (Ngawi + Caruban per gudang)",
        "pajak_report": "Laporan CSV kode pajak (209 vs mapping vs insert)",
    }
    total_steps = len(only)

    for step_no, key in enumerate(only, 1):
        if key not in STEPS and key != "pajak_report":
            raise SystemExit(f"Entitas tidak dikenal: {key}")

        print_step(step_no, total_steps, step_labels.get(key, key))

        if key == "pajak_report":
            stats = export_pajak_kode_reports(mysql_master, mysql_trx_pajak, pg)
        elif key == "barang_union":
            stats = migrate_barang_union(
                mysql_trx_ngawi,
                mysql_trx_caruban,
                pg,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
                batch_size=settings["batch_size"],
            )
        elif key == "stok_cabang":
            stats = migrate_stok_cabang(
                {"ngawi": mysql_trx_ngawi, "caruban": mysql_trx_caruban},
                pg,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
            )
        elif key == "barang":
            stats = STEPS[key](
                mysql_master,
                mysql_trx,
                pg,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
                batch_size=settings["batch_size"],
                limit=args.limit,
                offset=args.offset,
            )
        elif key in ("stok_pajak", "barang_pajak"):
            stats = STEPS[key](
                mysql_trx_pajak,
                pg,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
            )
            if key == "stok_pajak":
                export_pajak_kode_reports(mysql_master, mysql_trx_pajak, pg)
        else:
            stats = STEPS[key](
                mysql_master,
                pg,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
            )

        for metric, value in stats.items():
            print(f"  {metric}: {value}")

    print("\nSelesai.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrasi master data MySQL desktop → PostgreSQL web",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("mapping", help="Lihat mapping tabel")
    sub.add_parser("inspect", help="Bandingkan jumlah data source vs target")

    run = sub.add_parser("run", help="Jalankan migrasi")
    run.add_argument("--dry-run", action="store_true", help="Simulasi tanpa insert")
    run.add_argument(
        "--fresh",
        action="store_true",
        help="Hapus data master PostgreSQL dulu sebelum migrasi (supplier, brand, barang)",
    )
    run.add_argument(
        "--only",
        default="supplier,brand,barang",
        help="Entitas dipisah koma (default: supplier,brand,barang)",
    )
    run.add_argument("--limit", type=int, default=0, help="Batas barang (0 = semua)")
    run.add_argument("--offset", type=int, default=0, help="Offset barang")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    mysql_master = connect_mysql(settings["mysql_master"])
    mysql_trx = connect_mysql(settings["mysql_trx"])
    mysql_trx_pajak = connect_mysql(settings["mysql_trx_pajak"])
    mysql_trx_ngawi = (
        connect_mysql(settings["mysql_trx_ngawi"])
        if settings["mysql_trx_ngawi"]["database"]
        else None
    )
    mysql_trx_caruban = (
        connect_mysql(settings["mysql_trx_caruban"])
        if settings["mysql_trx_caruban"]["database"]
        else None
    )
    pg = connect_pg(settings["pg"])

    try:
        ensure_id_map_table(pg)

        if args.command == "mapping":
            cmd_mapping()
        elif args.command == "inspect":
            cmd_inspect(
                mysql_master,
                mysql_trx,
                mysql_trx_pajak,
                mysql_trx_ngawi,
                mysql_trx_caruban,
                pg,
            )
        elif args.command == "run":
            cmd_run(
                mysql_master,
                mysql_trx,
                mysql_trx_pajak,
                mysql_trx_ngawi,
                mysql_trx_caruban,
                pg,
                settings,
                args,
            )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        mysql_master.close()
        mysql_trx.close()
        mysql_trx_pajak.close()
        if mysql_trx_ngawi:
            mysql_trx_ngawi.close()
        if mysql_trx_caruban:
            mysql_trx_caruban.close()
        pg.close()


if __name__ == "__main__":
    main()
