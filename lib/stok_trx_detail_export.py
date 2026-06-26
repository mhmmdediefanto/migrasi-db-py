from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from lib.progress import Spinner
from lib.stok_trx_check import load_selisih_rows

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

# Internal field keys → label Excel (Bahasa Indonesia)
RINGKASAN_FIELDS = [
    ("kode_barang", "Kode"),
    ("nama_barang", "Nama Barang"),
    ("stok_excel", "Stok Excel (18/6)"),
    ("stok_barang_system", "Stok System"),
    ("selisih_barang", "Selisih"),
    ("kategori_trx", "Kategori Trx"),
    ("net_pembelian", "Net Beli"),
    ("jumlah_faktur_pembelian", "Faktur Beli"),
    ("tanggal_pembelian_terakhir", "Tgl Beli Terakhir"),
    ("net_penjualan", "Net Jual"),
    ("jumlah_faktur_penjualan", "Faktur Jual"),
    ("tanggal_penjualan_terakhir", "Tgl Jual Terakhir"),
]

PEMBELIAN_FIELDS = [
    ("kode_barang", "Kode"),
    ("nama_barang", "Nama Barang"),
    ("stok_excel", "Stok Excel"),
    ("stok_barang_system", "Stok System"),
    ("selisih_barang", "Selisih"),
    ("no_faktur", "No Faktur"),
    ("tanggal", "Tanggal"),
    ("gudang", "Gudang"),
    ("jumlah", "Qty"),
    ("harga", "Harga"),
    ("total", "Total"),
]

PENJUALAN_FIELDS = [
    ("kode_barang", "Kode"),
    ("nama_barang", "Nama Barang"),
    ("stok_excel", "Stok Excel"),
    ("stok_barang_system", "Stok System"),
    ("selisih_barang", "Selisih"),
    ("no_faktur", "No Faktur"),
    ("tanggal", "Tanggal"),
    ("jumlah", "Qty"),
    ("harga_satuan", "Harga"),
    ("total", "Total"),
]

GABUNGAN_FIELDS = [
    ("kode_barang", "Kode"),
    ("nama_barang", "Nama Barang"),
    ("stok_excel", "Stok Excel"),
    ("stok_barang_system", "Stok System"),
    ("selisih_barang", "Selisih"),
    ("tipe", "Tipe"),
    ("no_faktur", "No Faktur"),
    ("tanggal", "Tanggal"),
    ("gudang", "Gudang"),
    ("qty", "Qty"),
    ("harga", "Harga"),
    ("total", "Total"),
]

KATEGORI_ORDER = {
    "PEMBELIAN + PENJUALAN": 0,
    "PEMBELIAN saja": 1,
    "PENJUALAN saja": 2,
    "TIDAK ADA TRX": 3,
}


def _fmt_date(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _stok_map_from_selisih(path: Path) -> dict[str, dict]:
    rows = load_selisih_rows(path)
    return {
        r["kode_barang"]: {
            "stok_excel": r.get("stok_excel"),
            "stok_barang_system": r.get("stok_barang_system"),
            "selisih_barang": r.get("selisih_barang"),
            "nama_barang": r.get("nama_barang"),
        }
        for r in rows
        if r.get("kode_barang")
    }


def _fetch_pembelian_details(
    pg, kodes: list[str], gudang_id: int | None = None, since: date | None = None,
) -> list[dict]:
    gudang_clause = ""
    since_clause = ""
    params: list = [kodes]
    if gudang_id is not None:
        gudang_clause = " AND p.gudang_id = %s"
        params.append(gudang_id)
    if since is not None:
        since_clause = " AND p.tanggal >= %s"
        params.append(since)
    with pg.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                b.kode_barang, b.nama, b.stok_akhir,
                p.id, p.no_faktur, p.tanggal, g.name,
                pd.jumlah, pd.harga, pd.total
            FROM barang b
            INNER JOIN pembelian_detail pd ON pd.barang_id = b.id AND pd.deleted_at IS NULL
            INNER JOIN pembelian p ON p.id = pd.pembelian_id AND p.deleted_at IS NULL
            LEFT JOIN master_gudang g ON g.id = p.gudang_id
            WHERE b.deleted_at IS NULL AND b.kode_barang = ANY(%s){gudang_clause}{since_clause}
            ORDER BY p.tanggal, b.kode_barang, p.no_faktur
            """,
            tuple(params),
        )
        rows = cur.fetchall()
    return [
        {
            "kode_barang": kode,
            "nama_barang": nama,
            "stok_barang_system": stok,
            "pembelian_id": pid,
            "no_faktur": no_faktur,
            "tanggal": _fmt_date(tanggal),
            "gudang": gudang or "",
            "jumlah": int(jumlah or 0),
            "harga": harga,
            "total": total,
        }
        for kode, nama, stok, pid, no_faktur, tanggal, gudang, jumlah, harga, total in rows
    ]


def _fetch_penjualan_details(pg, kodes: list[str], since: date | None = None) -> list[dict]:
    since_clause = ""
    params: list = [kodes]
    if since is not None:
        since_clause = " AND p.tanggal::date >= %s"
        params.append(since)
    with pg.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                b.kode_barang, b.nama, b.stok_akhir,
                p.id, p.no_faktur, p.tanggal,
                pd.jumlah, pd.harga_satuan, pd.total
            FROM barang b
            INNER JOIN penjualan_detail pd ON pd.barang_id = b.id AND pd.deleted_at IS NULL
            INNER JOIN penjualan p ON p.id = pd.penjualan_id AND p.deleted_at IS NULL
            WHERE b.deleted_at IS NULL AND b.kode_barang = ANY(%s){since_clause}
            ORDER BY p.tanggal, b.kode_barang, p.no_faktur
            """,
            tuple(params),
        )
        rows = cur.fetchall()
    return [
        {
            "kode_barang": kode,
            "nama_barang": nama,
            "stok_barang_system": stok,
            "penjualan_id": pid,
            "no_faktur": no_faktur,
            "tanggal": _fmt_date(tanggal),
            "jumlah": int(round(float(jumlah or 0))),
            "harga_satuan": harga,
            "total": total,
        }
        for kode, nama, stok, pid, no_faktur, tanggal, jumlah, harga, total in rows
    ]


def _fetch_pembelian_returns(pg, kodes: list[str], gudang_id: int | None = None) -> list[tuple]:
    gudang_clause = ""
    params: list = [kodes]
    if gudang_id is not None:
        gudang_clause = " AND pr.gudang_id = %s"
        params.append(gudang_id)
    with pg.cursor() as cur:
        cur.execute(
            f"""
            SELECT b.kode_barang, prd.qty_return
            FROM pembelian_return_detail prd
            INNER JOIN pembelian_return pr ON pr.id = prd.pembelian_return_id AND pr.deleted_at IS NULL
            INNER JOIN barang b ON b.id = prd.barang_id AND b.deleted_at IS NULL
            WHERE prd.deleted_at IS NULL AND b.kode_barang = ANY(%s){gudang_clause}
            """,
            tuple(params),
        )
        return cur.fetchall()


def _fetch_penjualan_returns(pg, kodes: list[str]) -> list[dict]:
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.kode_barang, prd.qty_return
            FROM penjualan_return_detail prd
            INNER JOIN penjualan_return pr ON pr.id = prd.penjualan_return_id AND pr.deleted_at IS NULL
            INNER JOIN barang b ON b.id = prd.barang_id
            WHERE b.kode_barang = ANY(%s)
            """,
            (kodes,),
        )
        return cur.fetchall()


def _merge_stok_context(row: dict, stok_map: dict[str, dict]) -> dict:
    kode = row["kode_barang"]
    ctx = stok_map.get(kode, {})
    row["stok_excel"] = ctx.get("stok_excel", "")
    row["selisih_barang"] = ctx.get("selisih_barang", "")
    if not row.get("nama_barang"):
        row["nama_barang"] = ctx.get("nama_barang", "")
    if row.get("stok_barang_system") is None and ctx.get("stok_barang_system") is not None:
        row["stok_barang_system"] = ctx.get("stok_barang_system")
    return row


def _build_ringkasan(
    kodes: list[str],
    stok_map: dict[str, dict],
    pembelian: list[dict],
    penjualan: list[dict],
    pembelian_ret: dict[str, int],
    penjualan_ret: dict[str, int],
) -> list[dict]:
    rows: list[dict] = []
    for kode in kodes:
        ctx = stok_map.get(kode, {})
        p_lines = [r for r in pembelian if r["kode_barang"] == kode]
        s_lines = [r for r in penjualan if r["kode_barang"] == kode]

        qty_p = sum(r["jumlah"] for r in p_lines)
        qty_p_ret = pembelian_ret.get(kode, 0)
        qty_s = sum(r["jumlah"] for r in s_lines)
        qty_sr = penjualan_ret.get(kode, 0)

        has_p = qty_p > 0 or qty_p_ret > 0
        has_s = qty_s > 0 or qty_sr > 0
        if has_p and has_s:
            kategori = "PEMBELIAN + PENJUALAN"
        elif has_p:
            kategori = "PEMBELIAN saja"
        elif has_s:
            kategori = "PENJUALAN saja"
        else:
            kategori = "TIDAK ADA TRX"

        p_dates = [r["tanggal"] for r in p_lines if r.get("tanggal")]
        s_dates = [r["tanggal"] for r in s_lines if r.get("tanggal")]

        rows.append(
            {
                "kode_barang": kode,
                "nama_barang": ctx.get("nama_barang") or (
                    p_lines[0]["nama_barang"] if p_lines else s_lines[0]["nama_barang"] if s_lines else ""
                ),
                "stok_excel": ctx.get("stok_excel", ""),
                "stok_barang_system": ctx.get("stok_barang_system", ""),
                "selisih_barang": ctx.get("selisih_barang", ""),
                "kategori_trx": kategori,
                "net_pembelian": qty_p - qty_p_ret,
                "jumlah_faktur_pembelian": len({r["pembelian_id"] for r in p_lines}),
                "tanggal_pembelian_terakhir": max(p_dates) if p_dates else "",
                "net_penjualan": qty_s - qty_sr,
                "jumlah_faktur_penjualan": len({r["penjualan_id"] for r in s_lines}),
                "tanggal_penjualan_terakhir": max(s_dates) if s_dates else "",
            }
        )

    rows.sort(key=lambda r: (KATEGORI_ORDER.get(r["kategori_trx"], 9), r["kode_barang"]))
    return rows


def _build_gabungan(pembelian: list[dict], penjualan: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for row in pembelian:
        rows.append(
            {
                "kode_barang": row["kode_barang"],
                "nama_barang": row["nama_barang"],
                "stok_excel": row.get("stok_excel", ""),
                "stok_barang_system": row.get("stok_barang_system", ""),
                "selisih_barang": row.get("selisih_barang", ""),
                "tipe": "PEMBELIAN",
                "no_faktur": row["no_faktur"],
                "tanggal": row["tanggal"],
                "gudang": row["gudang"],
                "qty": row["jumlah"],
                "harga": row["harga"],
                "total": row["total"],
            }
        )
    for row in penjualan:
        rows.append(
            {
                "kode_barang": row["kode_barang"],
                "nama_barang": row["nama_barang"],
                "stok_excel": row.get("stok_excel", ""),
                "stok_barang_system": row.get("stok_barang_system", ""),
                "selisih_barang": row.get("selisih_barang", ""),
                "tipe": "PENJUALAN",
                "no_faktur": row["no_faktur"],
                "tanggal": row["tanggal"],
                "gudang": "",
                "qty": row["jumlah"],
                "harga": row.get("harga_satuan", ""),
                "total": row["total"],
            }
        )
    return rows


def _style_sheet(ws, header_count: int) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    header_fill = PatternFill("solid", fgColor="2F5496")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    for col in range(1, header_count + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 28

    for col_cells in ws.columns:
        letter = col_cells[0].column_letter
        width = 10
        for cell in col_cells:
            if cell.value is not None:
                width = max(width, min(len(str(cell.value)) + 2, 45))
        ws.column_dimensions[letter].width = width

    if ws.max_row > 1:
        ws.auto_filter.ref = f"A1:{ws.cell(1, header_count).column_letter}{ws.max_row}"


def _write_data_sheet(ws, field_map: list[tuple[str, str]], rows: list[dict]) -> None:
    keys = [k for k, _ in field_map]
    labels = [label for _, label in field_map]
    ws.append(labels)
    for row in rows:
        ws.append([row.get(k, "") for k in keys])
    _style_sheet(ws, len(labels))


def _write_info_sheet(ws, stats: dict) -> None:
    from openpyxl.styles import Font

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 55
    title_font = Font(bold=True, size=14)
    ws["A1"] = stats.get("title", "Rekap Selisih Stok + Transaksi Web")
    ws["A1"].font = title_font
    lines = [
        ("Gudang / cabang", stats.get("gudang_label", "")),
        ("Tanggal laporan Excel", stats.get("laporan_tanggal", "18-06-2026 (Saldo Stock Sragen)")),
        ("Trx sejak tanggal", stats.get("since_date", "")),
        ("Sumber DB", stats.get("db_name", "")),
        ("Total barang (ada trx web)", stats.get("barang_count", "")),
        ("Barang pembelian + penjualan", stats.get("count_both", "")),
        ("Barang pembelian saja", stats.get("count_beli", "")),
        ("Barang penjualan saja", stats.get("count_jual", "")),
        ("Line pembelian", stats.get("pembelian_lines", "")),
        ("Line penjualan", stats.get("penjualan_lines", "")),
        ("", ""),
        ("Sheet Ringkasan", "1 baris per barang — stok vs trx"),
        ("Sheet Gabungan Trx", "Semua faktur beli & jual (filter Kode)"),
        ("Sheet Pembelian", "Detail faktur pembelian saja"),
        ("Sheet Penjualan", "Detail faktur penjualan saja"),
    ]
    for idx, (label, value) in enumerate(lines, start=3):
        ws.cell(row=idx, column=1, value=label)
        ws.cell(row=idx, column=2, value=value)


def export_stok_trx_detail_xlsx(
    pg,
    *,
    trx_file: Path,
    selisih_file: Path,
    out_path: Path,
    db_name: str = "",
    gudang: str | None = None,
    since_date: date | None = None,
) -> dict:
    trx_path = trx_file.expanduser().resolve()
    selisih_path = selisih_file.expanduser().resolve()
    out = out_path.expanduser().resolve()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    gudang_id: int | None = None
    gudang_label = "Semua gudang"
    if gudang:
        from lib.stok_trx_check import resolve_gudang_id

        gudang_id, gudang_label = resolve_gudang_id(pg, gudang)

    with Spinner(f"Membaca daftar trx {trx_path.name}..."):
        trx_rows = load_selisih_rows(trx_path)
        kodes = [r["kode_barang"] for r in trx_rows if r.get("kode_barang")]
        stok_map = _stok_map_from_selisih(selisih_path)
        for r in trx_rows:
            if r["kode_barang"] in stok_map:
                stok_map[r["kode_barang"]].update(
                    {
                        k: r.get(k)
                        for k in ("stok_excel", "stok_barang_system", "selisih_barang", "nama_barang")
                        if r.get(k) is not None
                    }
                )

    with Spinner(f"Query pembelian ({len(kodes)} kode, {gudang_label})..."):
        pembelian = _fetch_pembelian_details(pg, kodes, gudang_id, since_date)
    with Spinner(f"Query penjualan ({len(kodes)} kode)..."):
        penjualan = _fetch_penjualan_details(pg, kodes, since_date)

    pembelian_ret: dict[str, int] = {}
    for kode, qty in _fetch_pembelian_returns(pg, kodes, gudang_id):
        pembelian_ret[str(kode)] = pembelian_ret.get(str(kode), 0) + int(qty or 0)
    penjualan_ret: dict[str, int] = {}
    for kode, qty in _fetch_penjualan_returns(pg, kodes):
        penjualan_ret[str(kode)] = penjualan_ret.get(str(kode), 0) + int(qty or 0)

    for row in pembelian:
        _merge_stok_context(row, stok_map)
    for row in penjualan:
        _merge_stok_context(row, stok_map)

    ringkasan = _build_ringkasan(kodes, stok_map, pembelian, penjualan, pembelian_ret, penjualan_ret)
    gabungan = _build_gabungan(pembelian, penjualan)

    count_both = sum(1 for r in ringkasan if r["kategori_trx"] == "PEMBELIAN + PENJUALAN")
    count_beli = sum(1 for r in ringkasan if r["kategori_trx"] == "PEMBELIAN saja")
    count_jual = sum(1 for r in ringkasan if r["kategori_trx"] == "PENJUALAN saja")

    from openpyxl import Workbook

    wb = Workbook()
    info_ws = wb.active
    info_ws.title = "Info"
    _write_info_sheet(
        info_ws,
        {
            "title": f"Rekap Selisih Stok — {gudang_label}",
            "gudang_label": gudang_label,
            "db_name": db_name,
            "barang_count": len(kodes),
            "count_both": count_both,
            "count_beli": count_beli,
            "count_jual": count_jual,
            "pembelian_lines": len(pembelian),
            "penjualan_lines": len(penjualan),
            "since_date": since_date.isoformat() if since_date else "",
        },
    )

    ws_ring = wb.create_sheet("Ringkasan Barang")
    _write_data_sheet(ws_ring, RINGKASAN_FIELDS, ringkasan)

    ws_gab = wb.create_sheet("Gabungan Trx")
    _write_data_sheet(ws_gab, GABUNGAN_FIELDS, gabungan)

    if pembelian:
        ws_beli = wb.create_sheet("Pembelian")
        _write_data_sheet(ws_beli, PEMBELIAN_FIELDS, pembelian)

    if penjualan:
        ws_jual = wb.create_sheet("Penjualan")
        _write_data_sheet(ws_jual, PENJUALAN_FIELDS, penjualan)

    wb.save(out)

    return {
        "barang_count": len(kodes),
        "ringkasan_rows": len(ringkasan),
        "pembelian_lines": len(pembelian),
        "penjualan_lines": len(penjualan),
        "gabungan_lines": len(gabungan),
        "count_pembelian_saja": count_beli,
        "count_penjualan_saja": count_jual,
        "count_keduanya": count_both,
        "gudang": gudang_label,
        "gudang_id": gudang_id,
        "output_path": str(out),
    }
