from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.stok_check import is_junk_row, parse_saldo_stock_xlsx


def clean_excel_rows(raw: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return [(nama, stok) for nama, stok in raw if not is_junk_row(nama)]


def load_saldo_stock(path: Path) -> list[tuple[str, int]]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return clean_excel_rows(parse_saldo_stock_xlsx(path))
