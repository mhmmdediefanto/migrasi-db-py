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
from lib.inspect_report import print_mysql_barang_stats, print_pg_barang_stats
from lib.progress import Spinner, print_step
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


def cmd_inspect(mysql_master, mysql_trx, pg) -> None:
    checks = [
        ("Supplier", "entity (nENTsupp=1)", "SELECT COUNT(*) FROM entity WHERE nENTsupp = 1", "supplier"),
        ("Golongan", "stockgroup", "SELECT COUNT(*) FROM stockgroup", "brands"),
        ("Gudang", "warehouse", "SELECT COUNT(*) FROM warehouse", "master_gudang"),
        ("Store", "outlet", "SELECT COUNT(*) FROM outlet", "master_store"),
        ("Barang", "stock (aktif)", "SELECT COUNT(*) FROM stock WHERE nSTKsuspend = 0", "barang"),
    ]

    print("=== INSPECT — perbandingan MySQL vs PostgreSQL ===\n")
    print(f"{'Entitas':<12}{'MySQL':<22}{'PostgreSQL':<14}Selisih")
    print("-" * 60)

    with mysql_master.cursor() as mcur, pg.cursor() as pcur:
        for step, (label, mysql_label, mysql_sql, pg_table) in enumerate(checks, 1):
            with Spinner(f"[{step}/{len(checks)}] Membandingkan {label}..."):
                mcur.execute(mysql_sql)
                mysql_count = int(mcur.fetchone()["COUNT(*)"])
                pcur.execute(f"SELECT COUNT(*) FROM {pg_table}")
                pg_count = int(pcur.fetchone()[0])
            print(f"{label:<12}{mysql_label} ({mysql_count})".ljust(22) + f"{pg_count:<14}{mysql_count - pg_count}")

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

    print("\n=== Stok dari transaksi (MySQL full backup) ===")
    try:
        with Spinner("Menghitung stok dari invoicedetail (bisa 30–60 detik)..."):
            with mysql_trx.cursor() as mcur:
                mcur.execute("SELECT COUNT(*) AS total FROM invoicedetail")
                total = int(mcur.fetchone()["total"])
                mcur.execute(
                    """
                    SELECT COUNT(*) AS c FROM (
                        SELECT cIVDfkSTK,
                            SUM(COALESCE(nIVDqtyin,0) - COALESCE(nIVDqtyout,0)) AS net
                        FROM invoicedetail
                        WHERE cIVDfkSTK IS NOT NULL AND cIVDfkSTK <> ''
                        GROUP BY cIVDfkSTK
                        HAVING net <> 0
                    ) t
                    """
                )
                non_zero = int(mcur.fetchone()["c"])
        print(f"  invoicedetail rows: {total:,}")
        print(f"  barang dengan net stok != 0: {non_zero:,}")
    except Exception as exc:
        print(f"  [skip] Tidak bisa baca invoicedetail: {exc}")

    print_mysql_barang_stats(mysql_master, mysql_trx)
    print_pg_barang_stats(pg)
    print("\n✓ Inspect selesai.")


def cmd_run(mysql_master, mysql_trx, pg, settings, args) -> None:
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
    }
    total_steps = len(only)

    for step_no, key in enumerate(only, 1):
        if key not in STEPS:
            raise SystemExit(f"Entitas tidak dikenal: {key}")

        print_step(step_no, total_steps, step_labels.get(key, key))
        fn = STEPS[key]

        if key == "barang":
            stats = fn(
                mysql_master,
                mysql_trx,
                pg,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
                batch_size=settings["batch_size"],
                limit=args.limit,
                offset=args.offset,
            )
        else:
            stats = fn(
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
    pg = connect_pg(settings["pg"])

    try:
        ensure_id_map_table(pg)

        if args.command == "mapping":
            cmd_mapping()
        elif args.command == "inspect":
            cmd_inspect(mysql_master, mysql_trx, pg)
        elif args.command == "run":
            cmd_run(mysql_master, mysql_trx, pg, settings, args)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        mysql_master.close()
        mysql_trx.close()
        pg.close()


if __name__ == "__main__":
    main()
