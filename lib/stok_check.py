from __future__ import annotations

import csv
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from lib.db import fetch_one
from lib.progress import Spinner

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_OUT = OUTPUT_DIR / "stok_selisih.xlsx"

XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

OUTPUT_FIELDS = [
    "status",
    "kode_barang",
    "nama_barang",
    "stok_excel",
    "stok_pg_gudang",
    "selisih_gudang",
    "stok_barang",
    "selisih_barang",
]

GUDANG_ALIASES = {
    "pusat": "Sragen",
    "sragen": "Sragen",
    "gudang pusat": "Sragen",
    "ngawi": "Ngawi",
    "caruban": "Caruban",
}


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def is_junk_row(nama: str) -> bool:
    if "Hal." in nama and "of" in nama:
        return True
    if nama in ("Nama Barang", "Satuan", "SRAGEN"):
        return True
    if "Saldo Stock" in nama or "CV MATAHARI" in nama:
        return True
    if nama.startswith("21-06-2026"):
        return True
    return False


def parse_saldo_stock_xlsx(path: Path) -> list[tuple[str, int]]:
    """Baca laporan Saldo Stock desktop (.xlsx). Kolom A = nama, C = qty."""
    rows: list[tuple[str, int]] = []
    with zipfile.ZipFile(path) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall(f"{{{XLSX_NS}}}si"):
                text_node = si.find(f"{{{XLSX_NS}}}t")
                if text_node is not None and text_node.text:
                    shared_strings.append(text_node.text)
                else:
                    parts = [
                        node.text or ""
                        for node in si.findall(f".//{{{XLSX_NS}}}t")
                    ]
                    shared_strings.append("".join(parts))

        sheet_xml = "xl/worksheets/sheet1.xml"
        if sheet_xml not in zf.namelist():
            raise ValueError(f"Tidak ada sheet1 di {path}")

        sheet_root = ET.fromstring(zf.read(sheet_xml))
        for row in sheet_root.findall(f".//{{{XLSX_NS}}}row"):
            row_num = int(row.attrib.get("r", 0))
            if row_num < 3:
                continue

            cells: dict[str, str] = {}
            for cell in row.findall(f"{{{XLSX_NS}}}c"):
                ref = cell.attrib["r"]
                col = re.match(r"([A-Z]+)", ref).group(1)
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{{{XLSX_NS}}}v")
                value = value_node.text if value_node is not None else ""
                if cell_type == "s" and value:
                    value = shared_strings[int(value)]
                cells[col] = value

            nama = (cells.get("A") or "").strip()
            if not nama:
                continue
            try:
                stok = int(round(float(cells.get("C") or "0")))
            except (TypeError, ValueError):
                stok = 0
            rows.append((nama, stok))

    return rows


def resolve_gudang_id(pg, gudang_hint: str) -> tuple[int, str]:
    key = gudang_hint.strip().lower()
    name = GUDANG_ALIASES.get(key, gudang_hint.strip())
    gudang_id = fetch_one(
        pg,
        "SELECT id FROM master_gudang WHERE name ILIKE %s ORDER BY id LIMIT 1",
        (name,),
    )
    if gudang_id is None:
        raise RuntimeError(f"Gudang tidak ditemukan: {gudang_hint!r} (coba: pusat, ngawi, caruban)")
    return int(gudang_id), name


def _load_pg_index(pg, gudang_id: int) -> dict[str, dict]:
    """Index nama → kode, stok_akhir (gudang), barang.stok_akhir, has_stok_akhir_row."""
    index: dict[str, dict] = {}
    with Spinner(f"Memuat stok gudang + barang.stok_akhir (gudang_id={gudang_id})..."):
        with pg.cursor() as cur:
            cur.execute(
                """
                SELECT
                    b.nama,
                    b.kode_barang,
                    b.stok_akhir AS stok_barang,
                    sa.stok_akhir AS stok_gudang,
                    sa.id AS stok_akhir_id
                FROM barang b
                LEFT JOIN stok_akhir sa
                    ON sa.barang_id = b.id AND sa.gudang_id = %s
                WHERE b.deleted_at IS NULL
                """,
                (gudang_id,),
            )
            for nama, kode, stok_barang, stok_gudang, stok_akhir_id in cur.fetchall():
                barang_qty = int(round(float(stok_barang or 0)))
                gudang_qty = (
                    int(round(float(stok_gudang or 0)))
                    if stok_akhir_id is not None
                    else None
                )
                index[str(nama)] = {
                    "kode_barang": str(kode or ""),
                    "stok_barang": barang_qty,
                    "stok_gudang": gudang_qty,
                    "has_stok_akhir": stok_akhir_id is not None,
                }
    return index


def _build_status(
    excel_stok: int,
    has_barang: bool,
    has_stok_akhir: bool,
    gudang_qty: int | None,
    barang_qty: int,
) -> str | None:
    if not has_barang:
        return "NOT_IN_BARANG"

    gudang_match = has_stok_akhir and gudang_qty is not None and excel_stok == gudang_qty
    barang_match = excel_stok == barang_qty

    if gudang_match and barang_match:
        return None

    if not has_stok_akhir:
        if barang_match and excel_stok == 0:
            return None
        parts = ["NO_STOK_AKHIR"]
        if not barang_match:
            parts.append("MISMATCH_BARANG")
        return "+".join(parts)

    gudang_mismatch = gudang_qty is not None and excel_stok != gudang_qty
    barang_mismatch = excel_stok != barang_qty

    if gudang_mismatch and barang_mismatch:
        return "MISMATCH_BOTH"
    if gudang_mismatch:
        return "MISMATCH_GUDANG"
    if barang_mismatch:
        return "MISMATCH_BARANG"
    return None


def compare_stok_excel_vs_pg(
    excel_rows: list[tuple[str, int]],
    pg_index: dict[str, dict],
) -> list[dict]:
    diff_rows: list[dict] = []
    for nama, excel_stok in excel_rows:
        if is_junk_row(nama):
            continue

        row = pg_index.get(nama)
        if row is None:
            status = _build_status(excel_stok, False, False, None, 0)
            if status is None:
                continue
            diff_rows.append(
                {
                    "status": status,
                    "kode_barang": "",
                    "nama_barang": nama,
                    "stok_excel": excel_stok,
                    "stok_pg_gudang": "",
                    "selisih_gudang": "",
                    "stok_barang": "",
                    "selisih_barang": "",
                }
            )
            continue

        status = _build_status(
            excel_stok,
            True,
            row["has_stok_akhir"],
            row["stok_gudang"],
            row["stok_barang"],
        )
        if status is None:
            continue

        gudang_qty = row["stok_gudang"]
        barang_qty = row["stok_barang"]
        diff_rows.append(
            {
                "status": status,
                "kode_barang": row["kode_barang"],
                "nama_barang": nama,
                "stok_excel": excel_stok,
                "stok_pg_gudang": gudang_qty if row["has_stok_akhir"] else "",
                "selisih_gudang": (
                    excel_stok - gudang_qty
                    if row["has_stok_akhir"] and gudang_qty is not None
                    else (excel_stok if not row["has_stok_akhir"] else "")
                ),
                "stok_barang": barang_qty,
                "selisih_barang": excel_stok - barang_qty,
            }
        )

    return diff_rows


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(path: Path, rows: list[dict]) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Selisih Stok"
    sheet.append(OUTPUT_FIELDS)
    for row in rows:
        sheet.append([row[field] for field in OUTPUT_FIELDS])
    workbook.save(path)


def run_stok_check(
    pg,
    *,
    from_file: Path,
    gudang: str = "pusat",
    out_path: Path | None = None,
    pg_database: str | None = None,
) -> dict:
    path_in = from_file.expanduser().resolve()
    if not path_in.is_file():
        raise FileNotFoundError(path_in)

    _ensure_output_dir()
    path_out = out_path or DEFAULT_OUT
    if path_out.suffix.lower() != ".xlsx":
        path_out = path_out.with_suffix(".xlsx")

    with Spinner(f"Membaca Excel {path_in.name}..."):
        excel_rows = parse_saldo_stock_xlsx(path_in)

    gudang_id, gudang_name = resolve_gudang_id(pg, gudang)
    pg_index = _load_pg_index(pg, gudang_id)

    clean_count = sum(1 for nama, _ in excel_rows if not is_junk_row(nama))
    diff_rows = compare_stok_excel_vs_pg(excel_rows, pg_index)

    stats = {
        "pg_database": pg_database or "",
        "excel_raw_rows": len(excel_rows),
        "excel_clean_rows": clean_count,
        "excel_ok_both": clean_count - len(diff_rows),
        "diff_rows": len(diff_rows),
        "mismatch_gudang": sum(
            1
            for r in diff_rows
            if any(
                token in r["status"]
                for token in ("MISMATCH_GUDANG", "MISMATCH_BOTH", "NO_STOK_AKHIR")
            )
        ),
        "mismatch_barang": sum(
            1
            for r in diff_rows
            if any(
                token in r["status"]
                for token in ("MISMATCH_BARANG", "MISMATCH_BOTH")
            )
        ),
        "not_in_barang": sum(1 for r in diff_rows if r["status"] == "NOT_IN_BARANG"),
        "gudang": gudang_name,
        "gudang_id": gudang_id,
        "output_path": str(path_out),
    }

    try:
        _write_xlsx(path_out, diff_rows)
    except ImportError:
        path_out = path_out.with_suffix(".csv")
        _write_csv(path_out, diff_rows)
        stats["output_path"] = str(path_out)
        stats["note"] = "openpyxl tidak ada — export CSV. Install: pip install openpyxl"

    return stats
