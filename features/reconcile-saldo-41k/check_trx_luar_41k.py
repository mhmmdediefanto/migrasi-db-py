#!/usr/bin/env python3
"""Export transaksi web untuk barang LUAR Saldo Stock 41k."""
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

from reconcile_lib.export_luar_xlsx import export_luar_workbook
from reconcile_lib.load_excel import load_saldo_stock
from reconcile_lib.trx_luar_query import build_luar_stats, fetch_trx_luar

DEFAULT_EXCEL = (
    "/Users/muhbagussaputro/Library/Containers/net.whatsapp.WhatsApp/Data/tmp/documents/"
    "DF071E91-F83E-4080-BA40-726C742EADC4/SaldostockSragen per 180626.xlsx"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export transaksi barang LUAR Saldo Stock 41k",
    )
    parser.add_argument("--from-file", default=DEFAULT_EXCEL)
    parser.add_argument("--pg-database", default="matahari_final")
    parser.add_argument("--gudang", default="pusat")
    parser.add_argument("--since-date", default="")
    parser.add_argument(
        "--out",
        default="",
        help="Default: features/reconcile-saldo-41k/output/trx_luar_41k_matahari_final.xlsx",
    )
    args = parser.parse_args()

    since = date.fromisoformat(args.since_date) if args.since_date else None
    excel_path = Path(args.from_file).expanduser().resolve()
    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else FEATURE_ROOT / "output" / "trx_luar_41k_matahari_final.xlsx"
    )

    print(f"Membaca Excel 41k: {excel_path.name} ...", flush=True)
    rows_excel = load_saldo_stock(excel_path)
    names = [n for n, _ in rows_excel]
    print(f"  baris: {len(names)}", flush=True)

    settings = load_settings()
    pg_cfg = dict(settings["pg"])
    pg_cfg["database"] = args.pg_database
    pg = connect_pg(pg_cfg)
    try:
        gudang_id, gudang_name = resolve_gudang_id(pg, args.gudang)
        print(f"Query transaksi LUAR 41k (gudang={gudang_name}) ...", flush=True)
        data = fetch_trx_luar(pg, excel_names=names, gudang_id=gudang_id, since=since)
    finally:
        pg.close()

    stats = build_luar_stats(
        data,
        excel_count=len(names),
        gudang=gudang_name,
        since=since,
        source_file=str(excel_path),
        pg_database=args.pg_database,
    )

    print(f"Export: {out_path} ...", flush=True)
    export_luar_workbook(out_path, data, stats)

    print("\n=== SELESAI ===")
    for key, _ in [
        ("barang_luar_dengan_trx", ""),
        ("penjualan_faktur_luar", ""),
        ("penjualan_faktur_murni_luar", ""),
        ("penjualan_faktur_campuran", ""),
        ("penjualan_line_luar", ""),
        ("pembelian_faktur_luar", ""),
        ("pembelian_line_luar", ""),
        ("transfer_line_luar", ""),
    ]:
        print(f"  {key}: {stats.get(key)}")
    print(f"\nFile: {out_path}")


if __name__ == "__main__":
    main()
