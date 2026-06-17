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


def resolve_harga_beli_lama(row) -> float | None:
    """
    Harga beli lama desktop:
    1. stockdetail.nSTDoprice
    2. stock.nstkhbeli (jika beda dari nSTKbuy)
    """
    current = resolve_harga_beli(row)
    oprice = positive_decimal(row.get("harga_beli_lama_src"))
    if oprice is not None:
        if current is not None and abs(oprice - current) < 0.01:
            return None
        return oprice

    hb_lama = positive_decimal(row.get("nstkhbeli"))
    hb_now = positive_decimal(row.get("nSTKbuy"))
    if hb_lama is not None and hb_now is not None and abs(hb_lama - hb_now) >= 0.01:
        return hb_lama
    return None


def resolve_harga_jual_lama(row) -> float | None:
    """Harga jual lama desktop: stockdetail.nSTDoretail."""
    current = resolve_harga_jual(row)
    oretail = positive_decimal(row.get("harga_jual_lama_src"))
    if oretail is None:
        return None
    if current is not None and abs(oretail - current) < 0.01:
        return None
    return oretail


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


_BANK_NAME_RE = re.compile(
    r"\b(BCA|BNI|MANDIRI|BRI|BTN|CIMB|DANAMON|PERMATA|OCBC|PANIN|"
    r"MAYBANK|BUKOPIN|BANK\s+[A-Z][A-Z\s]{2,})\b",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?:08\d{8,11}|0\d{2,3}[\s./-]?\d{6,8}(?:[\s/]+\d{8,12})?)",
    re.IGNORECASE,
)
_ACCOUNT_RE = re.compile(r"(?:a/?c\.?|rekening|rek\.?)\s*[:.]?\s*([\d\s-]+)", re.IGNORECASE)
_OWNER_RE = re.compile(r"a\.?\s*n\.?\s*[:.]?\s*([^,\n\r]+)", re.IGNORECASE)


def _looks_like_phone_or_bank(text: str) -> bool:
    upper = text.upper()
    if _PHONE_RE.search(text):
        return True
    if _ACCOUNT_RE.search(text):
        return True
    if _BANK_NAME_RE.search(upper):
        return True
    if upper.startswith("AN.") or upper.startswith("A/N"):
        return True
    return False


def extract_supplier_phone(*texts) -> str | None:
    """Ambil nomor telepon dari kolom alamat desktop (sering tercampur di add1/add5)."""
    for text in texts:
        raw = str(text or "").strip()
        if not raw:
            continue
        match = _PHONE_RE.search(raw)
        if match:
            phone = re.sub(r"\s+", " ", match.group(0)).strip()
            return phone[:255]
    return None


def parse_supplier_bank_memo(memo: str | None) -> dict[str, str | None]:
    """
    Parse cENTmemo desktop — format umum:
      a.n. NAMA PEMILIK
      A/C. 1234567890
      BCA SURABAYA
  Kalau ada beberapa rekening, ambil blok pertama.
    """
    text = str(memo or "").replace("\r", "\n")
    if not text.strip():
        return {
            "nama_pemilik_rekening": None,
            "nomor_rekening": None,
            "nama_bank": None,
        }

    owner_match = _OWNER_RE.search(text)
    account_match = _ACCOUNT_RE.search(text)
    bank_match = _BANK_NAME_RE.search(text)

    owner = normalize_text(owner_match.group(1), 150) if owner_match else None
    account = None
    if account_match:
        account = re.sub(r"\D", "", account_match.group(1))
        account = account[:50] if account else None

    bank = None
    if bank_match:
        bank = normalize_text(bank_match.group(0), 100)

    return {
        "nama_pemilik_rekening": owner or None,
        "nomor_rekening": account or None,
        "nama_bank": bank or None,
    }


def build_supplier_address(row) -> str | None:
    """Gabung alamat; lewati baris yang isinya telepon/rekening."""
    parts: list[str] = []
    for key in ("cENTadd1", "cENTadd2", "cENTadd3", "cENTadd4", "cENTadd5"):
        text = normalize_text(row.get(key), 60)
        if not text or _looks_like_phone_or_bank(text):
            continue
        parts.append(text)
    return ", ".join(parts) if parts else None


def resolve_supplier_fields(row) -> dict:
    """Kumpulkan kolom supplier web dari baris entity + city."""
    memo_bank = parse_supplier_bank_memo(row.get("cENTmemo"))

    phone = extract_supplier_phone(
        row.get("centadd5s"),
        row.get("cENTadd5"),
        row.get("centadd1s"),
        row.get("cENTadd1"),
        row.get("cENTimage"),
        row.get("centadd2s"),
        row.get("cENTadd2"),
        row.get("centadd3s"),
        row.get("cENTadd3"),
        row.get("centadd4s"),
        row.get("cENTadd4"),
    )

    rekening = normalize_text(row.get("cENTrek"), 50) or memo_bank["nomor_rekening"]
    bank = normalize_text(row.get("cENTacc"), 100) or memo_bank["nama_bank"]
    owner = memo_bank["nama_pemilik_rekening"]

    return {
        "address": build_supplier_address(row),
        "wilayah": normalize_text(row.get("wilayah"), 255) or None,
        "phone": phone,
        "nama_bank": bank or None,
        "nomor_rekening": rekening or None,
        "nama_pemilik_rekening": owner,
        "status": int(row.get("nENTsuspend") or 0) == 0,
    }
