#!/usr/bin/env python3
"""Hitung stok: baseline Excel/0 ± transaksi, bandingkan vs sistem."""
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

from reconcile_lib.export_stok_hitung_xlsx import export_stok_hitung_workbook
from reconcile_lib.load_excel import load_saldo_stock
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
    parser = argparse.ArgumentParser(description="Stok hitung baseline Excel/0 ± transaksi")
    parser.add_argument("--from-file", default=DEFAULT_EXCEL)
    parser.add_argument("--pg-database", default="matahari_final")
    parser.add_argument("--gudang", default="pusat")
    parser.add_argument("--since-date", default=DEFAULT_SINCE.isoformat())
    parser.add_argument(
        "--out",
        default="",
        help="Default: output/stok_hitung_41k_matahari_final.xlsx",
    )
    args = parser.parse_args()

    since = date.fromisoformat(args.since_date)
    excel_path = Path(args.from_file).expanduser().resolve()
    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else FEATURE_ROOT / "output" / "stok_hitung_41k_matahari_final.xlsx"
    )

    print(f"Membaca Excel: {excel_path.name} ...", flush=True)
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
        print(f"Hitung stok 41k (baseline Excel, trx >= {since}) ...", flush=True)
        rows_41k = fetch_stok_hitung_41k(
            pg, names=names, stoks=stoks, gudang_id=gudang_id, since=since,
        )
        print(f"Hitung stok luar 41k (baseline=0, semua trx) ...", flush=True)
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

    print(f"Export: {out_path} ...", flush=True)
    export_stok_hitung_workbook(out_path, rows_41k, rows_luar, stats)

    print("\n=== SELESAI ===")
    for k in (
        "total_41k", "total_luar_41k", "selisih_gudang_count",
        "selisih_gudang_41k", "selisih_gudang_luar", "ada_trx_41k",
    ):
        print(f"  {k}: {stats.get(k)}")
    print(f"\nFile: {out_path}")


if __name__ == "__main__":
    main()
