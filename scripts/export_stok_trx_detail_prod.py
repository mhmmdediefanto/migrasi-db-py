#!/usr/bin/env python3
"""Export Excel rapi: ringkasan + detail pembelian/penjualan (prod via tunnel di .env)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from lib.config import load_settings
from lib.pg_conn import connect_pg_from_env
from lib.stok_trx_detail_export import export_stok_trx_detail_xlsx

TRX_FILE = ROOT / "output" / "stok_selisih_trx_prod_sragen_180626.xlsx"
SELISIH_FILE = ROOT / "output" / "stok_selisih_sragen_180626.xlsx"
OUT = ROOT / "output" / "stok_selisih_trx_detail_prod.xlsx"


def main() -> None:
    settings = load_settings()
    pg_cfg = settings["pg"]
    print(
        f"Connecting {pg_cfg['host']}:{pg_cfg['port']} / {pg_cfg['database']}...",
        flush=True,
    )
    pg = connect_pg_from_env()
    try:
        stats = export_stok_trx_detail_xlsx(
            pg,
            trx_file=TRX_FILE,
            selisih_file=SELISIH_FILE,
            out_path=OUT,
            db_name=pg_cfg["database"],
        )
        print("=== DONE ===", flush=True)
        for k, v in stats.items():
            print(f"  {k}: {v}", flush=True)
    finally:
        pg.close()


if __name__ == "__main__":
    main()
