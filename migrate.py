#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Paket lokal (vendor) tanpa perlu install global
ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
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
from lib.harga_fix import DEFAULT_CSV, apply_harga_jual_fix, export_harga_jual_fix
from lib.stok_check import run_stok_check
from lib.stok_trx_check import connect_pg_from_params, run_stok_trx_check
from lib.stok_fix import run_stok_fix, verify_stok_fix
from lib.stok_opname_fix import run_opname_snapshot_fix, verify_opname_snapshot_fix
from lib.stok_minus_bukti import DEFAULT_GOLONGAN, export_stok_minus_bukti_xlsx
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
        "harga_export": "Export CSV harga_jual yang perlu diperbaiki (~21k)",
        "harga_fix": "Apply harga_jual dari CSV (tanpa scan 725k)",
    }
    total_steps = len(only)

    for step_no, key in enumerate(only, 1):
        if key not in STEPS and key not in ("pajak_report", "harga_export", "harga_fix"):
            raise SystemExit(f"Entitas tidak dikenal: {key}")

        print_step(step_no, total_steps, step_labels.get(key, key))

        if key == "harga_export":
            csv_path = Path(args.from_csv) if args.from_csv else DEFAULT_CSV
            stats = export_harga_jual_fix(mysql_master, pg, csv_path=csv_path)
        elif key == "harga_fix":
            csv_path = Path(args.from_csv) if args.from_csv else DEFAULT_CSV
            stats = apply_harga_jual_fix(
                pg,
                csv_path=csv_path,
                dry_run=args.dry_run,
                user_id=settings["user_id"],
            )
        elif key == "pajak_report":
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
                update=args.update,
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

    check_stok = sub.add_parser(
        "check-stok",
        help="Bandingkan stok Excel Saldo Stock desktop vs PostgreSQL",
    )
    check_stok.add_argument(
        "--from-file",
        required=True,
        help="Path file Excel (.xlsx) laporan Saldo Stock",
    )
    check_stok.add_argument(
        "--gudang",
        default="pusat",
        help="Gudang target: pusat/sragen, ngawi, caruban (default: pusat)",
    )
    check_stok.add_argument(
        "--out",
        default="",
        help="Path output Excel selisih (default: output/stok_selisih.xlsx)",
    )
    check_stok.add_argument(
        "--pg-database",
        default="",
        help="Override PG_DATABASE untuk cek ini saja (mis. matahari_final_clone)",
    )

    export_minus = sub.add_parser(
        "export-stok-minus-bukti",
        help="Export ringkasan + transaksi + ledger bukti stok minus dari MySQL desktop",
    )
    export_minus.add_argument(
        "--golongan",
        default=DEFAULT_GOLONGAN,
        help=f"Nama golongan exact (default: {DEFAULT_GOLONGAN})",
    )
    export_minus.add_argument(
        "--mysql-db",
        default="trx",
        choices=("trx", "master", "pajak", "ngawi", "caruban"),
        help="Sumber MySQL dari .env (default: trx = MYSQL_TRX_DATABASE)",
    )
    export_minus.add_argument(
        "--out",
        default="",
        help="Path output Excel (default: output/stok_minus_<golongan>_<db>.xlsx)",
    )

    check_trx = sub.add_parser(
        "check-stok-trx",
        help="Cek pembelian/penjualan untuk barang di file selisih stok (read-only)",
    )
    check_trx.add_argument(
        "--from-selisih",
        default="output/stok_selisih_sragen_180626.xlsx",
        help="File Excel selisih stok (default: output/stok_selisih_sragen_180626.xlsx)",
    )
    check_trx.add_argument("--gudang", default="pusat", help="Gudang pembelian: pusat/ngawi/caruban")
    check_trx.add_argument("--out", default="", help="Path output Excel")
    check_trx.add_argument(
        "--since-date",
        default="2026-06-18",
        help="Tanggal laporan saldo stock (cek trx >= tanggal ini, default: 2026-06-18)",
    )
    check_trx.add_argument(
        "--all",
        action="store_true",
        help="Export semua baris selisih (termasuk tanpa trx). Default: hanya yang ada pembelian/penjualan",
    )
    check_trx.add_argument("--pg-host", default="", help="Override PG host (prod)")
    check_trx.add_argument("--pg-port", type=int, default=0, help="Override PG port")
    check_trx.add_argument("--pg-database", default="", help="Override PG database")
    check_trx.add_argument("--pg-user", default="", help="Override PG user")
    check_trx.add_argument("--pg-password", default="", help="Override PG password")

    fix_stok = sub.add_parser(
        "fix-stok-pusat",
        help="Koreksi stok Gudang Pusat (Sragen) — rekonsiliasi Excel 18/6 + trx web",
    )
    fix_stok.add_argument(
        "--from-selisih",
        default="output/stok_selisih_sragen_180626.xlsx",
        help="File Excel selisih stok (default: output/stok_selisih_sragen_180626.xlsx)",
    )
    fix_stok.add_argument(
        "--gudang",
        default="pusat",
        help="Gudang target (default: pusat/sragen)",
    )
    fix_stok.add_argument(
        "--since-date",
        default="2026-06-18",
        help="Tanggal baseline rekonsiliasi (default: 2026-06-18)",
    )
    fix_stok.add_argument(
        "--out-preview",
        default="",
        help="Path output CSV preview (default: output/stok_koreksi_preview.csv)",
    )
    fix_stok.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="User ID untuk created_by/updated_by (default: 1)",
    )
    fix_stok.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Batas baris (0 = semua)",
    )
    fix_stok.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset baris",
    )

    mode_group = fix_stok.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Hanya preview, tidak write ke DB (default)",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Write koreksi ke DB (wajib eksplisit)",
    )
    mode_group.add_argument(
        "--verify-only",
        action="store_true",
        help="Verifikasi hasil apply (tidak write)",
    )
    fix_stok.add_argument(
        "--allow-production",
        action="store_true",
        help="Izinkan apply ke DB production (matahari-production). Tanpa flag ini → abort.",
    )

    fix_opname = sub.add_parser(
        "fix-opname-snapshot",
        help="Sinkron stok_opname_detail.stok_sistem_snapshot setelah koreksi stok",
    )
    fix_opname.add_argument(
        "--from-selisih",
        default="output/stok_selisih_sragen_180626.xlsx",
        help="File Excel selisih koreksi (default: output/stok_selisih_sragen_180626.xlsx)",
    )
    fix_opname.add_argument(
        "--gudang",
        default="pusat",
        help="Gudang opname (default: pusat/sragen)",
    )
    fix_opname.add_argument(
        "--out-preview",
        default="",
        help="Path output CSV preview (default: output/stok_opname_snapshot_preview.csv)",
    )
    opname_mode = fix_opname.add_mutually_exclusive_group()
    opname_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Hanya preview, tidak write ke DB (default)",
    )
    opname_mode.add_argument(
        "--apply",
        action="store_true",
        help="Write snapshot ke DB (wajib eksplisit)",
    )
    opname_mode.add_argument(
        "--verify-only",
        action="store_true",
        help="Verifikasi snapshot vs stok gudang (tidak write)",
    )
    fix_opname.add_argument(
        "--allow-production",
        action="store_true",
        help="Izinkan apply ke DB production (matahari-production). Tanpa flag ini → abort.",
    )

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
    run.add_argument(
        "--update",
        action="store_true",
        help="Update harga_jual (+ harga_jual_lama) barang existing dari nSTDretail; harga beli tidak diubah",
    )
    run.add_argument(
        "--from-csv",
        default="",
        help="Path CSV untuk harga_export / harga_fix (default: output/harga_jual_perlu_update.csv)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    mysql_only = args.command in ("export-stok-minus-bukti",)
    pg_only = args.command in (
        "mapping", "check-stok", "check-stok-trx",
        "fix-stok-pusat", "fix-opname-snapshot",
    )

    pg_cfg = dict(settings["pg"])
    pg_database_override = ""
    if args.command in ("check-stok", "check-stok-trx", "fix-stok-pusat") and getattr(args, "pg_database", ""):
        pg_database_override = args.pg_database.strip()
        if pg_database_override:
            pg_cfg["database"] = pg_database_override

    pg = None
    if not mysql_only:
        if args.command == "check-stok-trx" and getattr(args, "pg_host", ""):
            pg_host = args.pg_host.strip()
            pg_port = args.pg_port if args.pg_port else settings["pg"]["port"]
            pg_database = args.pg_database.strip() or settings["pg"]["database"]
            pg_user = args.pg_user.strip() or settings["pg"]["user"]
            pg_password = args.pg_password if args.pg_password is not None else settings["pg"]["password"]
            pg = connect_pg_from_params(pg_host, pg_port, pg_database, pg_user, pg_password)
            pg_database_override = pg_database
        else:
            pg = connect_pg(pg_cfg)

    mysql_master = None
    mysql_trx = None
    mysql_trx_pajak = None
    mysql_trx_ngawi = None
    mysql_trx_caruban = None

    if not pg_only:
        mysql_master = connect_mysql(settings["mysql_master"])
        mysql_trx = connect_mysql(settings["mysql_trx"])
        mysql_trx_pajak = connect_mysql(settings["mysql_trx_pajak"])
        if settings["mysql_trx_ngawi"]["database"]:
            mysql_trx_ngawi = connect_mysql(settings["mysql_trx_ngawi"])
        if settings["mysql_trx_caruban"]["database"]:
            mysql_trx_caruban = connect_mysql(settings["mysql_trx_caruban"])

    try:
        if pg is not None and args.command != "mapping":
            ensure_id_map_table(pg)

        if args.command == "mapping":
            cmd_mapping()
        elif args.command == "check-stok":
            out_path = Path(args.out) if args.out else None
            stats = run_stok_check(
                pg,
                from_file=Path(args.from_file),
                gudang=args.gudang,
                out_path=out_path,
                pg_database=pg_database_override or settings["pg"]["database"],
            )
            print("=== CHECK STOK — Excel vs PostgreSQL ===")
            for key, value in stats.items():
                print(f"  {key}: {value}")
            print("\n✓ Export selisih selesai.")
        elif args.command == "export-stok-minus-bukti":
            mysql_key = {
                "trx": "mysql_trx",
                "master": "mysql_master",
                "pajak": "mysql_trx_pajak",
                "ngawi": "mysql_trx_ngawi",
                "caruban": "mysql_trx_caruban",
            }[args.mysql_db]
            mysql_cfg = settings[mysql_key]
            if not mysql_cfg.get("database"):
                raise SystemExit(f"MySQL database untuk {args.mysql_db} belum di-set di .env")
            mysql_conn = connect_mysql(mysql_cfg)
            try:
                out_path = Path(args.out) if args.out else None
                stats = export_stok_minus_bukti_xlsx(
                    mysql_conn,
                    golongan=args.golongan,
                    out_path=out_path,
                    db_name=mysql_cfg["database"],
                )
            finally:
                mysql_conn.close()
            print("=== EXPORT STOK MINUS BUKTI (MySQL desktop) ===")
            for key, value in stats.items():
                print(f"  {key}: {value}")
            if stats.get("mismatch_akhir"):
                print(
                    f"\n⚠ {stats['mismatch_akhir']} barang: saldo akhir ledger ≠ net ringkasan "
                    "(cek duplikasi stockdetail / urutan transaksi)."
                )
            else:
                print("\n✓ Semua barang minus: saldo akhir ledger cocok dengan ringkasan.")
        elif args.command == "check-stok-trx":
            from datetime import date

            since_parts = args.since_date.strip().split("-")
            since_date = date(int(since_parts[0]), int(since_parts[1]), int(since_parts[2]))
            out_path = Path(args.out) if args.out else None
            stats = run_stok_trx_check(
                pg,
                selisih_file=Path(args.from_selisih),
                gudang=args.gudang,
                out_path=out_path,
                since_date=since_date,
                only_with_trx=not args.all,
            )
            print("=== CHECK STOK TRX — pembelian & penjualan (read-only) ===")
            for key, value in stats.items():
                print(f"  {key}: {value}")
            print("\n✓ Export trx selesai.")
        elif args.command == "fix-stok-pusat":
            from datetime import date
            since_parts = args.since_date.strip().split("-")
            since = date(int(since_parts[0]), int(since_parts[1]), int(since_parts[2]))
            out_preview = Path(args.out_preview) if args.out_preview else None
            is_apply = args.apply
            is_verify = args.verify_only
            is_dry = not is_apply and not is_verify

            if is_verify:
                out_sisa = OUTPUT_DIR / "stok_koreksi_sisa.csv"
                stats = verify_stok_fix(
                    pg,
                    selisih_file=Path(args.from_selisih),
                    gudang=args.gudang,
                    since_date=since,
                    out_sisa=out_sisa,
                )
                print("=== VERIFY STOK PUSAT ===")
                for k, v in stats.items():
                    print(f"  {k}: {v}")
                if stats["ok"]:
                    print("\n✓ LULUS — mismatch = 0, aman dilanjutkan ke production.")
                else:
                    print(f"\n✗ GAGAL — mismatch_stok_akhir={stats['mismatch_stok_akhir']} mismatch_barang={stats['mismatch_barang']}")
                    print(f"  Detail: {stats.get('sisa_file')}")
            else:
                stats = run_stok_fix(
                    pg,
                    selisih_file=Path(args.from_selisih),
                    gudang=args.gudang,
                    since_date=since,
                    user_id=args.user_id,
                    dry_run=is_dry,
                    out_preview=out_preview,
                    limit=args.limit,
                    offset=args.offset,
                    allow_production=args.allow_production,
                )
                mode_label = "DRY-RUN" if is_dry else "APPLY"
                print(f"=== FIX STOK PUSAT [{mode_label}] ===")
                for k, v in stats.items():
                    print(f"  {k}: {v}")
                if is_dry:
                    print(f"\n✓ Dry-run selesai. Review: {stats['preview_csv']}")
                    print("  Jalankan --apply untuk write ke DB.")
                else:
                    print(f"\n✓ Apply selesai ({stats.get('applied', 0)} baris).")
                    print("  Jalankan --verify-only untuk verifikasi.")
        elif args.command == "fix-opname-snapshot":
            out_preview = Path(args.out_preview) if args.out_preview else None
            is_apply = args.apply
            is_verify = args.verify_only
            is_dry = not is_apply and not is_verify

            if is_verify:
                out_sisa = OUTPUT_DIR / "stok_opname_snapshot_sisa.csv"
                stats = verify_opname_snapshot_fix(
                    pg,
                    selisih_file=Path(args.from_selisih),
                    gudang=args.gudang,
                    out_sisa=out_sisa,
                )
                print("=== VERIFY OPNAME SNAPSHOT ===")
                for k, v in stats.items():
                    print(f"  {k}: {v}")
                if stats["ok"]:
                    print("\n✓ LULUS — mismatch = 0.")
                else:
                    print(f"\n✗ GAGAL — mismatch={stats['mismatch']}")
                    print(f"  Detail: {stats.get('sisa_file')}")
            else:
                stats = run_opname_snapshot_fix(
                    pg,
                    selisih_file=Path(args.from_selisih),
                    gudang=args.gudang,
                    dry_run=is_dry,
                    out_preview=out_preview,
                    allow_production=args.allow_production,
                )
                mode_label = "DRY-RUN" if is_dry else "APPLY"
                print(f"=== FIX OPNAME SNAPSHOT [{mode_label}] ===")
                for k, v in stats.items():
                    print(f"  {k}: {v}")
                if is_dry:
                    print(f"\n✓ Dry-run selesai. Review: {stats['preview_csv']}")
                    print("  Jalankan --apply untuk write ke DB.")
                else:
                    print(f"\n✓ Apply selesai ({stats.get('applied', 0)} baris).")
                    print("  Jalankan --verify-only untuk verifikasi.")
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
        if mysql_master:
            mysql_master.close()
        if mysql_trx:
            mysql_trx.close()
        if mysql_trx_pajak:
            mysql_trx_pajak.close()
        if mysql_trx_ngawi:
            mysql_trx_ngawi.close()
        if mysql_trx_caruban:
            mysql_trx_caruban.close()
        if pg is not None:
            pg.close()


if __name__ == "__main__":
    main()
