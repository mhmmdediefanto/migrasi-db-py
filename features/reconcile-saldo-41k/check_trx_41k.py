#!/usr/bin/env python3
"""Cek transaksi web untuk barang Saldo Stock 41k."""
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

from reconcile_lib.export_xlsx import export_workbook
from reconcile_lib.load_excel import load_saldo_stock
from reconcile_lib.trx_query import build_stats, fetch_trx_flags

DEFAULT_EXCEL = (
    "/Users/muhbagussaputro/Library/Containers/net.whatsapp.WhatsApp/Data/tmp/documents/"
    "DF071E91-F83E-4080-BA40-726C742EADC4/SaldostockSragen per 180626.xlsx"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cek transaksi untuk barang Saldo Stock 41k",
    )
    parser.add_argument(
        "--from-file",
        default=DEFAULT_EXCEL,
        help="Path file Saldo Stock .xlsx",
    )
    parser.add_argument("--pg-database", default="matahari_final")
    parser.add_argument("--gudang", default="pusat")
    parser.add_argument(
        "--since-date",
        default="",
        help="Filter trx >= tanggal (YYYY-MM-DD). Kosong = semua transaksi.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Path output .xlsx (default: features/reconcile-saldo-41k/output/trx_flags_41k.xlsx)",
    )
    args = parser.parse_args()

    since = date.fromisoformat(args.since_date) if args.since_date else None
    excel_path = Path(args.from_file).expanduser().resolve()
    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else FEATURE_ROOT / "output" / "trx_flags_41k_matahari_final.xlsx"
    )

    print(f"Membaca Excel: {excel_path.name} ...", flush=True)
    rows_excel = load_saldo_stock(excel_path)
    names = [n for n, _ in rows_excel]
    stoks = [s for _, s in rows_excel]
    print(f"  baris bersih: {len(rows_excel)}", flush=True)

    settings = load_settings()
    pg_cfg = dict(settings["pg"])
    pg_cfg["database"] = args.pg_database
    print(
        f"Koneksi PG: {pg_cfg['host']}:{pg_cfg['port']} / {args.pg_database} ...",
        flush=True,
    )
    pg = connect_pg(pg_cfg)
    try:
        gudang_id, gudang_name = resolve_gudang_id(pg, args.gudang)
        print(f"Query transaksi (gudang={gudang_name}, since={since or 'ALL'}) ...", flush=True)
        result = fetch_trx_flags(
            pg,
            names=names,
            stoks=stoks,
            gudang_id=gudang_id,
            since=since,
        )
    finally:
        pg.close()

    stats = build_stats(
        result,
        gudang=gudang_name,
        since=since,
        source_file=str(excel_path),
    )
    stats["pg_database"] = args.pg_database

    print(f"Export Excel: {out_path} ...", flush=True)
    export_workbook(out_path, result, stats)

    print("\n=== SELESAI ===")
    for key in (
        "total_barang_excel",
        "ada_trx",
        "tanpa_trx",
        "not_in_barang",
        "ada_pembelian",
        "ada_penjualan",
        "ada_transfer",
        "total_stok_excel",
    ):
        print(f"  {key}: {stats.get(key)}")
    print(f"\nFile: {out_path}")


if __name__ == "__main__":
    main()
