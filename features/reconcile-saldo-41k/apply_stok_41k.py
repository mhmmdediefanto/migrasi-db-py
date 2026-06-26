#!/usr/bin/env python3
"""Apply koreksi stok S1/S2/S3 ke DB."""
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

from reconcile_lib.load_excel import load_saldo_stock
from reconcile_lib.stok_apply import run_stok_apply_41k
from reconcile_lib.stok_hitung_query import DEFAULT_SINCE

DEFAULT_EXCEL = (
    "/Users/muhbagussaputro/Library/Containers/net.whatsapp.WhatsApp/Data/tmp/documents/"
    "DF071E91-F83E-4080-BA40-726C742EADC4/SaldostockSragen per 180626.xlsx"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply koreksi stok S1/S2/S3")
    parser.add_argument("--from-file", default=DEFAULT_EXCEL)
    parser.add_argument("--pg-database", default="matahari_final")
    parser.add_argument("--gudang", default="pusat")
    parser.add_argument("--since-date", default=DEFAULT_SINCE.isoformat())
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--out-preview", default="")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument(
        "--from-preview",
        default="",
        help="Lanjutkan apply dari CSV preview (skip query berat)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip barang yang sudah punya ledger koreksi 41k",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        args.dry_run = True

    since = date.fromisoformat(args.since_date)
    excel_path = Path(args.from_file).expanduser().resolve()
    rows_excel = load_saldo_stock(excel_path)
    names = [n for n, _ in rows_excel]
    stoks = [s for _, s in rows_excel]

    settings = load_settings()
    pg_cfg = dict(settings["pg"])
    pg_cfg["database"] = args.pg_database
    pg = connect_pg(pg_cfg)
    from_preview = Path(args.from_preview).expanduser().resolve() if args.from_preview else None
    try:
        gudang_id, gudang_name = resolve_gudang_id(pg, args.gudang)
        preview = Path(args.out_preview) if args.out_preview else None
        stats = run_stok_apply_41k(
            pg,
            names=names,
            stoks=stoks,
            gudang_id=gudang_id,
            gudang_name=gudang_name,
            since=since,
            user_id=args.user_id,
            dry_run=not args.apply,
            out_preview=preview,
            allow_production=args.allow_production,
            from_preview=from_preview,
            resume=args.resume,
            batch_size=args.batch_size,
            pg_cfg=pg_cfg if args.apply else None,
        )
    finally:
        pg.close()

    mode = "DRY-RUN" if stats.get("dry_run") else "APPLY"
    print(f"\n=== {mode} SELESAI ===")
    for k in (
        "db", "gudang", "s1_kandidat", "s1_apply", "s2_apply", "s3_apply",
        "total_apply", "skipped_resume", "update", "insert", "preview_csv",
        "applied", "from_preview",
    ):
        if k in stats:
            print(f"  {k}: {stats[k]}")


if __name__ == "__main__":
    main()
