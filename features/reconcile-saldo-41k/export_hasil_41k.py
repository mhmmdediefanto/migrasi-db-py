#!/usr/bin/env python3
"""Export hasil koreksi & verifikasi stok matahari_final ke Excel."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

FEATURE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = FEATURE_ROOT.parents[1]
sys.path[:0] = [
    str(PROJECT_ROOT / "vendor"),
    str(PROJECT_ROOT),
    str(FEATURE_ROOT),
]

from lib.config import load_settings
from lib.db import connect_pg
from lib.stok_check import resolve_gudang_id

from reconcile_lib.export_stok_apply_xlsx import export_stok_apply_from_csv
from reconcile_lib.export_stok_hitung_xlsx import export_stok_hitung_workbook
from reconcile_lib.load_excel import load_saldo_stock
from reconcile_lib.stok_apply import FEATURE_OUTPUT
from reconcile_lib.stok_hitung_query import (
    DEFAULT_SINCE,
    build_stats,
    fetch_stok_hitung_41k,
    fetch_stok_hitung_luar_41k,
)

DEFAULT_EXCEL = (
    "/Users/muhbagussaputro/Library/Containers/net.whatsapp.WhatsApp/Data/tmp/documents/"
    "DF071E91-F83E-4080-BA40-726C742EADC4/SaldostockSragen per 180626.xlsx"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export hasil stok 41k ke Excel")
    parser.add_argument("--from-file", default=DEFAULT_EXCEL)
    parser.add_argument("--pg-database", default="matahari_final")
    parser.add_argument("--gudang", default="pusat")
    parser.add_argument("--since-date", default=DEFAULT_SINCE.isoformat())
    parser.add_argument(
        "--preview-csv",
        default="",
        help="Default: output/stok_apply_41k_preview.csv",
    )
    parser.add_argument("--out-koreksi", default="")
    parser.add_argument("--out-verifikasi", default="")
    args = parser.parse_args()

    since = date.fromisoformat(args.since_date)
    excel_path = Path(args.from_file).expanduser().resolve()
    out_dir = FEATURE_ROOT / "output"
    csv_path = (
        Path(args.preview_csv).expanduser().resolve()
        if args.preview_csv
        else out_dir / "stok_apply_41k_preview.csv"
    )
    out_koreksi = (
        Path(args.out_koreksi).expanduser().resolve()
        if args.out_koreksi
        else out_dir / "hasil_koreksi_stok_41k_matahari_final.xlsx"
    )
    out_verifikasi = (
        Path(args.out_verifikasi).expanduser().resolve()
        if args.out_verifikasi
        else out_dir / "hasil_verifikasi_stok_41k_matahari_final.xlsx"
    )

    print(f"Membaca Excel sumber: {excel_path.name} ...", flush=True)
    rows_excel = load_saldo_stock(excel_path)
    names = [n for n, _ in rows_excel]
    stoks = [s for _, s in rows_excel]
    print(f"  baris: {len(names)}", flush=True)

    settings = load_settings()
    pg_cfg = dict(settings["pg"])
    pg_cfg["database"] = args.pg_database
    pg = connect_pg(pg_cfg)
    try:
        gudang_id, gudang_name = resolve_gudang_id(pg, args.gudang)
        print(f"Hitung verifikasi stok (DB: {args.pg_database}) ...", flush=True)
        rows_41k = fetch_stok_hitung_41k(
            pg, names=names, stoks=stoks, gudang_id=gudang_id, since=since,
        )
        rows_luar = fetch_stok_hitung_luar_41k(pg, names=names, gudang_id=gudang_id)
    finally:
        pg.close()

    stats = build_stats(
        rows_41k, rows_luar,
        gudang=gudang_name,
        since=since,
        source_file=str(excel_path),
        pg_database=args.pg_database,
    )

    print(f"Export verifikasi: {out_verifikasi.name} ...", flush=True)
    export_stok_hitung_workbook(out_verifikasi, rows_41k, rows_luar, stats)

    if not csv_path.is_file():
        print(f"CSV koreksi tidak ditemukan: {csv_path}", flush=True)
        print("Jalankan run_apply.sh --dry-run terlebih dahulu.", flush=True)
    else:
        print(f"Export koreksi dari: {csv_path.name} ...", flush=True)
        apply_stats = {
            "pg_database": args.pg_database,
            "gudang": gudang_name,
            "source_file": str(excel_path),
        }
        export_stok_apply_from_csv(csv_path, out_koreksi, apply_stats)
        print(f"  baris koreksi: {apply_stats.get('total_apply', 0)}")

    print("\n=== SELESAI ===")
    print(f"  verifikasi: {out_verifikasi}")
    if csv_path.is_file():
        print(f"  koreksi:    {out_koreksi}")
    print(f"  selisih_gudang: {stats.get('selisih_gudang_count')}")
    print(f"  total_41k: {stats.get('total_41k')}")
    print(f"  total_luar_41k: {stats.get('total_luar_41k')}")


if __name__ == "__main__":
    main()
