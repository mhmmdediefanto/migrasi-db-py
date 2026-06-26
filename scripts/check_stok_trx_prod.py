#!/usr/bin/env python3
"""Standalone: cek pembelian/penjualan untuk barang selisih stok (prod via tunnel di .env)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from lib.config import load_settings
from lib.pg_conn import connect_pg_from_env
from lib.stok_trx_check import run_stok_trx_check

SELISIH = ROOT / "output" / "stok_selisih_sragen_180626.xlsx"
OUT = ROOT / "output" / "stok_selisih_trx_prod_sragen_180626.xlsx"
SINCE = date(2026, 6, 18)


def main() -> None:
    settings = load_settings()
    pg_cfg = settings["pg"]
    print(
        f"Connecting {pg_cfg['host']}:{pg_cfg['port']} / {pg_cfg['database']}...",
        flush=True,
    )
    pg = connect_pg_from_env()
    try:
        stats = run_stok_trx_check(
            pg,
            selisih_file=SELISIH,
            gudang="Sragen",
            out_path=OUT,
            since_date=SINCE,
        )
        print("=== DONE ===", flush=True)
        for k, v in stats.items():
            print(f"  {k}: {v}", flush=True)
        print(f"\nFile: {OUT}", flush=True)
    finally:
        pg.close()


if __name__ == "__main__":
    main()
