#!/usr/bin/env python3
"""Revert koreksi stok S3 — kembalikan ke kondisi sebelum apply S3."""
from __future__ import annotations

import argparse
import sys
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

from reconcile_lib.stok_revert_s3 import run_revert_s3


def main() -> None:
    parser = argparse.ArgumentParser(description="Revert koreksi stok S3")
    parser.add_argument("--pg-database", default="matahari_final")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--preview-csv", default="")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        args.dry_run = True

    settings = load_settings()
    pg_cfg = dict(settings["pg"])
    pg_cfg["database"] = args.pg_database
    pg = connect_pg(pg_cfg)

    if pg_cfg["database"] == "matahari-production" and not args.allow_production:
        pg.close()
        raise SystemExit("Production: tambahkan --allow-production")

    preview = Path(args.preview_csv).expanduser().resolve() if args.preview_csv else None
    try:
        stats = run_revert_s3(
            pg,
            user_id=args.user_id,
            dry_run=not args.apply,
            preview_csv=preview,
            batch_size=args.batch_size,
        )
    finally:
        pg.close()

    mode = "DRY-RUN" if stats.get("dry_run") else "REVERT"
    print(f"\n=== {mode} S3 SELESAI ===")
    for k in (
        "db", "s3_ledger", "restore_gudang", "delete_stok_akhir",
        "restore_barang", "delete_ledger",
    ):
        if k in stats:
            print(f"  {k}: {stats[k]}")


if __name__ == "__main__":
    main()
