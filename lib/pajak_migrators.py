from __future__ import annotations

import csv
from pathlib import Path

from lib.config import (
    normalize_text,
    parse_ukuran,
    resolve_harga_beli,
    resolve_harga_beli_lama,
    resolve_harga_jual,
    resolve_harga_jual_lama,
    resolve_hpp,
    slug_code,
)
from lib.db import fetch_all_dict, fetch_one, lookup_id_map, save_id_map
from lib.migrators import default_gudang_id
from lib.progress import Spinner, finish_progress, show_progress

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

NET_STOK_PAJAK_BY_CODE_SQL = """
    SELECT sd.kode_barang AS code,
        SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net
    FROM invoicedetail d
    INNER JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
    INNER JOIN (
        SELECT cSTDfkSTK, MIN(TRIM(cSTDcode)) AS kode_barang
        FROM stockdetail
        WHERE cSTDcode IS NOT NULL AND TRIM(cSTDcode) <> ''
        GROUP BY cSTDfkSTK
    ) sd ON sd.cSTDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
    GROUP BY sd.kode_barang
"""

PAJAK_STOCK_DETAIL_SQL = """
    SELECT
        s.cSTKpk,
        s.cSTKdesc,
        s.cSTKfkGRP,
        s.cSTKfkENT,
        s.nSTKbuy,
        s.nstkhbeli,
        s.nHrgQty01,
        s.nSTKcogs,
        s.cSTKsize,
        s.nstkukuran,
        s.cSTKcolor,
        s.nstkkonsi,
        s.nstktdiscp,
        g.cGRPdesc AS golongan_nama,
        sd.kode_barang,
        sd.harga_retail,
        sd.harga_price,
        sd.harga_beli_lama_src,
        sd.harga_jual_lama_src
    FROM stock s
    INNER JOIN (
        SELECT
            cSTDfkSTK,
            MIN(TRIM(cSTDcode)) AS kode_barang,
            MAX(CASE WHEN nSTDretail > 0 THEN nSTDretail END) AS harga_retail,
            MAX(CASE WHEN nSTDprice > 0 THEN nSTDprice END) AS harga_price,
            MAX(CASE WHEN nSTDoprice > 0 THEN nSTDoprice END) AS harga_beli_lama_src,
            MAX(CASE WHEN nSTDoretail > 0 THEN nSTDoretail END) AS harga_jual_lama_src
        FROM stockdetail
        WHERE cSTDcode IS NOT NULL AND TRIM(cSTDcode) <> ''
        GROUP BY cSTDfkSTK
    ) sd ON sd.cSTDfkSTK = s.cSTKpk
    LEFT JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
    WHERE s.nSTKsuspend = 0
"""

BARANG_PAJAK_SOURCE_SQL = (
    PAJAK_STOCK_DETAIL_SQL
    + """
      AND sd.kode_barang IN ({placeholders})
    ORDER BY sd.kode_barang
"""
)

MASTER_KODE_BARANG_SQL = """
    SELECT DISTINCT TRIM(cSTDcode) AS kode
    FROM stockdetail
    WHERE cSTDcode IS NOT NULL AND TRIM(cSTDcode) <> ''
"""


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fetch_net_stok_pajak_by_code(mysql_pajak) -> dict[str, int]:
    """Net stok pajak per kode barang dari DB pajak terpisah."""
    result: dict[str, int] = {}
    with Spinner("Menghitung net stok pajak per kode (DB pajak)..."):
        rows = fetch_all_dict(mysql_pajak, NET_STOK_PAJAK_BY_CODE_SQL)
    for row in rows:
        code = normalize_text(row.get("code"), 255)
        if not code:
            continue
        try:
            net = int(round(float(row.get("net") or 0)))
        except (TypeError, ValueError):
            net = 0
        result[code] = result.get(code, 0) + net
    return result


def load_web_barang_index(pg) -> dict[str, dict]:
    """kode_barang -> {id, is_ppn}."""
    index: dict[str, dict] = {}
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT b.id, b.kode_barang, COALESCE(br.is_ppn, false) AS is_ppn
            FROM barang b
            LEFT JOIN brands br ON br.id = b.brand_id
            WHERE b.deleted_at IS NULL
              AND b.kode_barang IS NOT NULL
              AND TRIM(b.kode_barang) <> ''
            """
        )
        for barang_id, kode, is_ppn in cur.fetchall():
            code = str(kode).strip()
            if code:
                index[code] = {"id": int(barang_id), "is_ppn": bool(is_ppn)}
    return index


def resolve_pajak_kode_to_web(pajak_code: str, web_codes: set[str]) -> tuple[str, str] | None:
    """
    Map kode dari DB pajak ke kode barang di web.
    Tier 1: exact, Tier 2: buang suffix A.
    """
    code = pajak_code.strip()
    if not code:
        return None
    if code in web_codes:
        return code, "exact"
    if code.upper().endswith("A") and len(code) > 1:
        base = code[:-1]
        if base in web_codes:
            return base, "strip_a"
    return None


def normalize_golongan_pajak(name: str) -> str:
    text = normalize_text(name, 255)
    if text.upper().endswith(".PKP"):
        text = text[:-4].strip()
    return text


def golongan_is_ppn(name: str) -> bool:
    upper = name.upper()
    return "PPN" in upper or "PKP" in upper


def resolve_brand_id_for_pajak(
    pg,
    golongan_nama: str,
    *,
    dry_run: bool,
    user_id: int,
) -> int | None:
    """Cari brand by nama dinormalisasi; buat baru jika perlu."""
    raw = normalize_text(golongan_nama, 255)
    if not raw:
        return None

    normalized = normalize_golongan_pajak(raw)
    brand_id = fetch_one(
        pg,
        "SELECT id FROM brands WHERE TRIM(name) = %s AND deleted_at IS NULL LIMIT 1",
        (normalized,),
    )
    if brand_id:
        return int(brand_id)

    brand_id = fetch_one(
        pg,
        "SELECT id FROM brands WHERE TRIM(name) = %s AND deleted_at IS NULL LIMIT 1",
        (raw,),
    )
    if brand_id:
        return int(brand_id)

    brand_id = fetch_one(
        pg,
        "SELECT id FROM brands WHERE name ILIKE %s AND deleted_at IS NULL LIMIT 1",
        (f"%{normalized}%",),
    )
    if brand_id:
        return int(brand_id)

    if dry_run:
        return -1

    code_base = slug_code(normalized or raw)
    code = code_base
    suffix = 1
    while fetch_one(pg, "SELECT id FROM brands WHERE code = %s LIMIT 1", (code,)):
        suffix += 1
        code = f"{code_base[:max(1, 50 - len(str(suffix)) - 1)]}_{suffix}"

    is_ppn = golongan_is_ppn(raw)
    with pg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO brands (code, name, is_ppn, margin_percent, created_by, updated_by, created_at, updated_at)
            VALUES (%s, %s, %s, 0, %s, %s, NOW(), NOW())
            RETURNING id
            """,
            (code, normalized or raw, is_ppn, user_id, user_id),
        )
        return int(cur.fetchone()[0])


def upsert_stok_pajak(
    pg,
    *,
    barang_id: int,
    gudang_id: int,
    stok_pajak: int,
    harga_satuan: float,
    user_id: int,
) -> str:
    """Update atau insert baris stok_akhir; return 'updated' | 'inserted'."""
    existing_id = fetch_one(
        pg,
        """
        SELECT id FROM stok_akhir
        WHERE barang_id = %s AND gudang_id = %s AND deleted_at IS NULL
        LIMIT 1
        """,
        (barang_id, gudang_id),
    )
    if existing_id:
        with pg.cursor() as cur:
            cur.execute(
                """
                UPDATE stok_akhir
                SET stok_pajak = %s, updated_by = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (stok_pajak, user_id, existing_id),
            )
        return "updated"

    with pg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stok_akhir (
                barang_id, gudang_id, stok_akhir, stok_pajak, harga_satuan,
                created_by, updated_by, created_at, updated_at
            ) VALUES (%s, %s, 0, %s, %s, %s, %s, NOW(), NOW())
            """,
            (barang_id, gudang_id, stok_pajak, harga_satuan, user_id, user_id),
        )
    return "inserted"


def migrate_stok_pajak(
    mysql_pajak,
    pg,
    *,
    dry_run: bool,
    user_id: int,
) -> dict:
    """
    Isi stok_akhir.stok_pajak dari DB pajak untuk barang PPN yang sudah ada di web.
    Tidak mengubah stok_akhir fisik.
    """
    stats = {
        "pajak_codes": 0,
        "updated": 0,
        "inserted_stok_akhir": 0,
        "skipped_non_ppn": 0,
        "unmapped": 0,
    }

    net_by_code = fetch_net_stok_pajak_by_code(mysql_pajak)
    stats["pajak_codes"] = len(net_by_code)

    with Spinner("Memuat index barang web..."):
        web_index = load_web_barang_index(pg)
    web_codes = set(web_index)

    applied_rows: list[dict] = []
    unmapped_rows: list[dict] = []

    items = sorted(net_by_code.items(), key=lambda x: x[0])
    total = len(items)

    try:
        for i, (pajak_code, net) in enumerate(items, 1):
            if i % 500 == 0 or i == total:
                show_progress(i, total, f"updated {stats['updated']:,}")

            resolved = resolve_pajak_kode_to_web(pajak_code, web_codes)
            if not resolved:
                stats["unmapped"] += 1
                unmapped_rows.append(
                    {
                        "kode_pajak": pajak_code,
                        "stok_pajak_net": net,
                        "alasan": "tidak ada di web (tier exact & strip_a gagal)",
                    }
                )
                continue

            web_code, tier = resolved
            meta = web_index[web_code]
            if not meta["is_ppn"]:
                stats["skipped_non_ppn"] += 1
                continue

            if dry_run:
                stats["updated"] += 1
                applied_rows.append(
                    {
                        "kode_pajak": pajak_code,
                        "kode_web": web_code,
                        "tier": tier,
                        "stok_pajak_net": net,
                        "barang_id": meta["id"],
                    }
                )
                continue

            action = upsert_stok_pajak(
                pg,
                barang_id=meta["id"],
                gudang_id=default_gudang_id(pg),
                stok_pajak=net,
                harga_satuan=0,
                user_id=user_id,
            )
            if action == "inserted":
                stats["inserted_stok_akhir"] += 1
            else:
                stats["updated"] += 1
            applied_rows.append(
                {
                    "kode_pajak": pajak_code,
                    "kode_web": web_code,
                    "tier": tier,
                    "stok_pajak_net": net,
                    "barang_id": meta["id"],
                    "action": action,
                }
            )

        if not dry_run:
            pg.commit()
    except Exception:
        if not dry_run:
            pg.rollback()
        raise

    out = _ensure_output_dir()
    _write_csv(
        out / "stok_pajak_applied.csv",
        ["kode_pajak", "kode_web", "tier", "stok_pajak_net", "barang_id", "action"],
        applied_rows,
    )
    _write_csv(
        out / "stok_pajak_unmapped.csv",
        ["kode_pajak", "stok_pajak_net", "alasan"],
        unmapped_rows,
    )

    finish_progress()
    return stats


def fetch_master_kode_barang(mysql_master) -> set[str]:
    """Semua kode barang di DB master evacer."""
    codes: set[str] = set()
    with Spinner("Memuat kode barang dari DB master..."):
        rows = fetch_all_dict(mysql_master, MASTER_KODE_BARANG_SQL)
    for row in rows:
        code = normalize_text(row.get("kode"), 255)
        if code:
            codes.add(code)
    return codes


def fetch_pajak_barang_details(mysql_pajak, codes: list[str] | None = None) -> list[dict]:
    """Detail barang aktif dari DB pajak; filter opsional per kode."""
    if codes is None:
        sql = PAJAK_STOCK_DETAIL_SQL + " ORDER BY sd.kode_barang"
        with Spinner("Mengambil detail barang dari DB pajak..."):
            return fetch_all_dict(mysql_pajak, sql)

    placeholders = ", ".join(["%s"] * len(codes))
    sql = BARANG_PAJAK_SOURCE_SQL.format(placeholders=placeholders)
    with Spinner(f"Mengambil {len(codes):,} barang dari DB pajak..."):
        return fetch_all_dict(mysql_pajak, sql, tuple(codes))


def export_pajak_kode_reports(mysql_master, mysql_pajak, pg) -> dict:
    """
    Export CSV terpisah untuk kode pajak vs master evacer vs web.

    File output:
    - kode_pajak_tidak_ada_di_master.csv (semua, dengan kolom aksi)
    - kode_pajak_mapped_ke_web.csv (sudah ada di web via exact/strip_a)
    - kode_pajak_perlu_insert_baru.csv (belum ada di web, perlu barang baru)
    """
    stats = {
        "tidak_ada_di_master": 0,
        "mapped_ke_web": 0,
        "perlu_insert_baru": 0,
        "tanpa_transaksi": 0,
    }

    net_by_code = fetch_net_stok_pajak_by_code(mysql_pajak)
    master_codes = fetch_master_kode_barang(mysql_master)
    web_index = load_web_barang_index(pg)
    web_codes = set(web_index)

    with Spinner("Mengambil detail barang DB pajak..."):
        pajak_rows = fetch_pajak_barang_details(mysql_pajak)

    by_kode: dict[str, dict] = {}
    for row in pajak_rows:
        kode = normalize_text(row.get("kode_barang"), 255)
        if not kode or kode in by_kode:
            continue
        by_kode[kode] = row

    all_rows: list[dict] = []
    mapped_rows: list[dict] = []
    insert_rows: list[dict] = []
    tanpa_transaksi_rows: list[dict] = []

    missing_on_web = set(_pajak_codes_missing_on_web(mysql_pajak, pg))

    for kode in sorted(by_kode):
        if kode in master_codes:
            continue

        row = by_kode[kode]
        nama = normalize_text(row.get("cSTKdesc"), 255)
        golongan = normalize_text(row.get("golongan_nama"), 255)
        stok_pajak = int(net_by_code.get(kode, 0))
        legacy_pk = str(row.get("cSTKpk") or "").strip()
        in_ledger = kode in net_by_code

        resolved = resolve_pajak_kode_to_web(kode, web_codes)
        if resolved:
            web_code, tier = resolved
            aksi = "mapped_ke_web"
            mapped_rows.append(
                {
                    "kode_pajak": kode,
                    "kode_web": web_code,
                    "tier": tier,
                    "nama": nama,
                    "golongan": golongan,
                    "stok_pajak_net": stok_pajak,
                    "ada_di_ledger_pajak": "ya" if in_ledger else "tidak",
                }
            )
            stats["mapped_ke_web"] += 1
        elif kode in missing_on_web:
            aksi = "perlu_insert_baru"
            insert_rows.append(
                {
                    "kode_pajak": kode,
                    "nama": nama,
                    "golongan": golongan,
                    "stok_pajak_net": stok_pajak,
                    "cSTKpk_pajak": legacy_pk,
                }
            )
            stats["perlu_insert_baru"] += 1
        else:
            aksi = "belum_di_web_tanpa_ledger"
            tanpa_transaksi_rows.append(
                {
                    "kode_pajak": kode,
                    "nama": nama,
                    "golongan": golongan,
                    "stok_pajak_net": stok_pajak,
                    "cSTKpk_pajak": legacy_pk,
                    "catatan": "tidak ada transaksi pajak; tidak masuk migrasi otomatis",
                }
            )
            stats["tanpa_transaksi"] = stats.get("tanpa_transaksi", 0) + 1

        all_rows.append(
            {
                "kode_pajak": kode,
                "nama": nama,
                "golongan": golongan,
                "stok_pajak_net": stok_pajak,
                "cSTKpk_pajak": legacy_pk,
                "aksi": aksi,
                "kode_web": resolved[0] if resolved else "",
                "tier": resolved[1] if resolved else "",
            }
        )

    stats["tidak_ada_di_master"] = len(all_rows)
    out = _ensure_output_dir()

    _write_csv(
        out / "kode_pajak_tidak_ada_di_master.csv",
        [
            "kode_pajak",
            "nama",
            "golongan",
            "stok_pajak_net",
            "cSTKpk_pajak",
            "aksi",
            "kode_web",
            "tier",
        ],
        all_rows,
    )
    _write_csv(
        out / "kode_pajak_mapped_ke_web.csv",
        [
            "kode_pajak",
            "kode_web",
            "tier",
            "nama",
            "golongan",
            "stok_pajak_net",
            "ada_di_ledger_pajak",
        ],
        mapped_rows,
    )
    _write_csv(
        out / "kode_pajak_perlu_insert_baru.csv",
        ["kode_pajak", "nama", "golongan", "stok_pajak_net", "cSTKpk_pajak"],
        insert_rows,
    )
    _write_csv(
        out / "kode_pajak_tanpa_transaksi.csv",
        ["kode_pajak", "nama", "golongan", "stok_pajak_net", "cSTKpk_pajak", "catatan"],
        tanpa_transaksi_rows,
    )
    # Nama lama (kompatibilitas) — isi sama dengan tidak_ada_di_master tanpa kolom aksi/web
    _write_csv(
        out / "kode_barang_hanya_di_pajak.csv",
        ["kode_barang", "nama", "golongan", "stok_pajak_net", "cSTKpk_pajak"],
        [
            {
                "kode_barang": r["kode_pajak"],
                "nama": r["nama"],
                "golongan": r["golongan"],
                "stok_pajak_net": r["stok_pajak_net"],
                "cSTKpk_pajak": r["cSTKpk_pajak"],
            }
            for r in all_rows
        ],
    )

    print("\n=== Laporan kode pajak (CSV) ===")
    print(f"  Tidak ada di master evacer:     {stats['tidak_ada_di_master']:,}")
    print(f"    → sudah ada di web (mapping): {stats['mapped_ke_web']:,}  → output/kode_pajak_mapped_ke_web.csv")
    print(f"    → perlu barang baru:          {stats['perlu_insert_baru']:,}  → output/kode_pajak_perlu_insert_baru.csv")
    tanpa = stats.get("tanpa_transaksi", 0)
    if tanpa:
        print(f"    → tanpa transaksi pajak:      {tanpa:,}  → output/kode_pajak_tanpa_transaksi.csv")
    print(f"  Gabungan + kolom aksi:          output/kode_pajak_tidak_ada_di_master.csv")

    finish_progress()
    return stats


def _pajak_codes_missing_on_web(mysql_pajak, pg) -> list[str]:
    net_by_code = fetch_net_stok_pajak_by_code(mysql_pajak)
    web_index = load_web_barang_index(pg)
    web_codes = set(web_index)
    missing: list[str] = []
    for pajak_code in net_by_code:
        if resolve_pajak_kode_to_web(pajak_code, web_codes):
            continue
        if pajak_code in web_codes:
            continue
        missing.append(pajak_code)
    return sorted(set(missing))


def migrate_barang_pajak(
    mysql_pajak,
    pg,
    *,
    dry_run: bool,
    user_id: int,
) -> dict:
    """
    Insert barang dari DB pajak yang belum ada di web (kode pajak-only).
    stok fisik = 0, stok_pajak = net dari DB pajak.
    """
    stats = {
        "candidate_codes": 0,
        "inserted": 0,
        "skipped_existing": 0,
        "skipped_non_ppn": 0,
        "missing_brand": 0,
    }

    missing_codes = _pajak_codes_missing_on_web(mysql_pajak, pg)
    stats["candidate_codes"] = len(missing_codes)
    if not missing_codes:
        finish_progress()
        return stats

    net_by_code = fetch_net_stok_pajak_by_code(mysql_pajak)
    gudang_id = default_gudang_id(pg)
    placeholders = ", ".join(["%s"] * len(missing_codes))
    sql = BARANG_PAJAK_SOURCE_SQL.format(placeholders=placeholders)

    with Spinner(f"Mengambil {len(missing_codes):,} barang dari DB pajak..."):
        rows = fetch_all_dict(mysql_pajak, sql, tuple(missing_codes))

    inserted_rows: list[dict] = []
    total = len(rows)

    try:
        for i, row in enumerate(rows, 1):
            show_progress(i, total, f"inserted {stats['inserted']:,}")

            legacy_pk = str(row["cSTKpk"]).strip()
            kode = normalize_text(row["kode_barang"], 255)
            nama = normalize_text(row["cSTKdesc"], 255)
            if not kode or not nama:
                continue

            existing_id = lookup_id_map(pg, "barang_pajak", legacy_pk)
            if existing_id:
                stats["skipped_existing"] += 1
                continue

            existing_id = fetch_one(
                pg, "SELECT id FROM barang WHERE kode_barang = %s LIMIT 1", (kode,)
            )
            if existing_id:
                if not dry_run:
                    save_id_map(pg, "barang_pajak", legacy_pk, int(existing_id))
                stats["skipped_existing"] += 1
                continue

            golongan = str(row.get("golongan_nama") or "")
            if not golongan_is_ppn(golongan):
                stats["skipped_non_ppn"] += 1
                continue

            brand_id = resolve_brand_id_for_pajak(
                pg, golongan, dry_run=dry_run, user_id=user_id
            )
            if brand_id is None:
                stats["missing_brand"] += 1
                continue
            if brand_id == -1 and dry_run:
                brand_id = None

            supplier_id = None
            harga_beli = resolve_harga_beli(row)
            harga_jual = resolve_harga_jual(row)
            harga_beli_lama = resolve_harga_beli_lama(row)
            harga_jual_lama = resolve_harga_jual_lama(row)
            hpp = resolve_hpp(row)
            ukuran = parse_ukuran(row.get("nstkukuran"), row.get("cSTKsize"))
            is_consignment = int(row.get("nstkkonsi") or 0) == 1
            consignment_pct = row.get("nstktdiscp")
            stok_pajak = int(net_by_code.get(kode, 0))

            if dry_run:
                stats["inserted"] += 1
                inserted_rows.append(
                    {
                        "kode_barang": kode,
                        "nama": nama,
                        "stok_pajak": stok_pajak,
                        "golongan": normalize_golongan_pajak(golongan),
                    }
                )
                continue

            with pg.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO barang (
                        kode_barang, nama, stok_awal, stok_akhir, harga_beli, harga_jual,
                        harga_beli_lama, harga_jual_lama, hpp,
                        brand_id, supplier_id, ukuran, warna, type, is_consignment,
                        consignment_percentage, created_by, updated_by, created_at, updated_at
                    ) VALUES (
                        %s, %s, 0, 0, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, NOW(), NOW()
                    )
                    RETURNING id
                    """,
                    (
                        kode,
                        nama,
                        harga_beli,
                        harga_jual,
                        harga_beli_lama,
                        harga_jual_lama,
                        hpp,
                        brand_id,
                        supplier_id,
                        ukuran,
                        normalize_text(row.get("cSTKcolor"), 100) or None,
                        "consignment" if is_consignment else "regular",
                        is_consignment,
                        consignment_pct,
                        user_id,
                        user_id,
                    ),
                )
                new_id = int(cur.fetchone()[0])

            harga_satuan = float(harga_jual or harga_beli or 0)
            upsert_stok_pajak(
                pg,
                barang_id=new_id,
                gudang_id=gudang_id,
                stok_pajak=stok_pajak,
                harga_satuan=harga_satuan,
                user_id=user_id,
            )
            save_id_map(pg, "barang_pajak", legacy_pk, new_id)
            stats["inserted"] += 1
            inserted_rows.append(
                {
                    "kode_barang": kode,
                    "nama": nama,
                    "stok_pajak": stok_pajak,
                    "golongan": normalize_golongan_pajak(golongan),
                    "barang_id": new_id,
                }
            )

        if not dry_run:
            pg.commit()
    except Exception:
        if not dry_run:
            pg.rollback()
        raise

    _write_csv(
        _ensure_output_dir() / "barang_pajak_inserted.csv",
        ["kode_barang", "nama", "stok_pajak", "golongan", "barang_id"],
        inserted_rows,
    )

    finish_progress()
    return stats
