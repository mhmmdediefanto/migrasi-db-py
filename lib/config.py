from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_settings() -> dict:
    load_dotenv(ROOT / ".env")

    mysql_host = os.getenv("MYSQL_HOST", "127.0.0.1")
    if mysql_host == "localhost":
        mysql_host = "127.0.0.1"

    return {
        # MySQL dibagi 2:
        # - master: hanya master data (entity/stock/stockgroup/stockdetail, dll.)
        # - trx: full backup (invoice/invoicedetail) untuk hitung stok aktual
        "mysql_master": {
            "host": mysql_host,
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "database": os.getenv("MYSQL_MASTER_DATABASE", os.getenv("MYSQL_DATABASE", "")),
            "user": os.getenv("MYSQL_USERNAME", ""),
            "password": os.getenv("MYSQL_PASSWORD", ""),
        },
        "mysql_trx": {
            "host": mysql_host,
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "database": os.getenv(
                "MYSQL_TRX_DATABASE",
                os.getenv("MYSQL_MASTER_DATABASE", os.getenv("MYSQL_DATABASE", "")),
            ),
            "user": os.getenv("MYSQL_USERNAME", ""),
            "password": os.getenv("MYSQL_PASSWORD", ""),
        },
        "pg": {
            "host": os.getenv("PG_HOST", "localhost"),
            "port": int(os.getenv("PG_PORT", "5432")),
            "database": os.getenv("PG_DATABASE", ""),
            "user": os.getenv("PG_USERNAME", ""),
            "password": os.getenv("PG_PASSWORD", ""),
        },
        "user_id": int(os.getenv("MIGRATION_DEFAULT_USER_ID", "1")),
        "batch_size": max(50, int(os.getenv("MIGRATION_BATCH_SIZE", "500"))),
    }


def load_mapping() -> dict:
    mapping_path = ROOT / "config" / "mapping.py"
    spec = importlib.util.spec_from_file_location("column_mapping", mapping_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.COLUMN_MAPPING


def normalize_text(value, max_length: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    return text[:max_length] if len(text) > max_length else text


def slug_code(value: str, max_length: int = 50) -> str:
    text = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if not text:
        text = "LEGACY"
    return text[:max_length]


def parse_ukuran(numeric, text) -> int | None:
    if numeric is not None and float(numeric) > 0:
        return int(round(float(numeric)))

    match = re.search(r"(\d{1,2})", str(text or "").strip())
    return int(match.group(1)) if match else None


def positive_decimal(value):
    """Ambil nilai numerik > 0, atau None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve_harga_jual(row) -> float | None:
    """Prioritas: stock.nHrgQty01 → stockdetail.nSTDretail → stockdetail.nSTDprice."""
    for key in ("nHrgQty01", "harga_retail", "harga_price"):
        value = positive_decimal(row.get(key))
        if value is not None:
            return value
    return None


def resolve_harga_beli(row) -> float | None:
    """Prioritas: stock.nSTKbuy → stockdetail.nSTDprice (fallback)."""
    for key in ("nSTKbuy", "harga_price"):
        value = positive_decimal(row.get(key))
        if value is not None:
            return value
    return None


def resolve_hpp(row) -> float | None:
    """Prioritas: stock.nSTKcogs → stock.nSTKbuy."""
    for key in ("nSTKcogs", "nSTKbuy"):
        value = positive_decimal(row.get(key))
        if value is not None:
            return value
    return None


def resolve_stok_qty(row) -> int:
    """
    Stok desktop (ISX/evacer):
    1. Jumlah stockdetail.outlet01–outlet20 (stok per outlet/toko)
    2. Fallback stock.nSTKopen (stok pembukaan)
    """
    outlet_total = row.get("stok_outlet_total")
    if outlet_total is not None:
        try:
            qty = int(round(float(outlet_total)))
            if qty > 0:
                return qty
        except (TypeError, ValueError):
            pass

    opening = row.get("nSTKopen")
    if opening is not None:
        try:
            qty = int(round(float(opening)))
            if qty > 0:
                return qty
        except (TypeError, ValueError):
            pass

    return 0
