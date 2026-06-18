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
from lib.migrators import seed_stok_for_barang
from lib.progress import Spinner, finish_progress, show_progress

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

KODE_AKTIF_SQL = """
    SELECT DISTINCT TRIM(sd.cSTDcode) AS kode
    FROM stock s
    INNER JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
      AND sd.cSTDcode IS NOT NULL
      AND TRIM(sd.cSTDcode) <> ''
"""

NET_STOK_BY_KODE_SQL = """
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

CABANG_BARANG_SQL = """
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
        e.cENTcode AS supplier_code,
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
    LEFT JOIN entity e ON e.cENTpk = s.cSTKfkENT
    WHERE s.nSTKsuspend = 0
      AND sd.kode_barang IN ({placeholders})
"""

BRANCH_STOCK_TARGETS = (
    ("ngawi", "Gudang Ngawi"),
    ("caruban", "Gudang Caruban"),
)


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fetch_kode_aktif(mysql) -> set[str]:
    codes: set[str] = set()
    rows = fetch_all_dict(mysql, KODE_AKTIF_SQL)
    for row in rows:
        code = normalize_text(row.get("kode"), 255)
        if code:
            codes.add(code)
    return codes


def fetch_net_stok_by_kode(mysql) -> dict[str, int]:
    result: dict[str, int] = {}
    with Spinner("Menghitung net stok per kode..."):
        rows = fetch_all_dict(mysql, NET_STOK_BY_KODE_SQL)
    for row in rows:
        code = normalize_text(row.get("code"), 255)
        if not code:
            continue
        try:
            net = int(round(float(row.get("net") or 0)))
        except (TypeError, ValueError):
            net = 0
        result[code] = net
    return result


def load_web_kode_index(pg) -> dict[str, int]:
    index: dict[str, int] = {}
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT id, kode_barang
            FROM barang
            WHERE deleted_at IS NULL
              AND kode_barang IS NOT NULL
              AND TRIM(kode_barang) <> ''
            """
        )
        for barang_id, kode in cur.fetchall():
            code = str(kode).strip()
            if code:
                index[code] = int(barang_id)
    return index


def golongan_is_ppn(name: str) -> bool:
    upper = name.upper()
    return "PPN" in upper or "PKP" in upper


def normalize_golongan_cabang(name: str) -> str:
    text = normalize_text(name, 255)
    if text.upper().endswith(".PKP"):
        text = text[:-4].strip()
    return text


def resolve_brand_id_for_cabang(
    pg,
    golongan_nama: str,
    *,
    dry_run: bool,
    user_id: int,
) -> int | None:
    raw = normalize_text(golongan_nama, 255)
    if not raw:
        return None

    normalized = normalize_golongan_cabang(raw)
    for candidate in (normalized, raw):
        brand_id = fetch_one(
            pg,
            "SELECT id FROM brands WHERE TRIM(name) = %s AND deleted_at IS NULL LIMIT 1",
            (candidate,),
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

    with pg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO brands (code, name, is_ppn, margin_percent, created_by, updated_by, created_at, updated_at)
            VALUES (%s, %s, %s, 0, %s, %s, NOW(), NOW())
            RETURNING id
            """,
            (code, normalized or raw, golongan_is_ppn(raw), user_id, user_id),
        )
        return int(cur.fetchone()[0])


def resolve_gudang_id(pg, name_hint: str) -> int:
    gid = fetch_one(
        pg,
        """
        SELECT id FROM master_gudang
        WHERE deleted_at IS NULL AND name ILIKE %s
        ORDER BY id LIMIT 1
        """,
        (f"%{name_hint}%",),
    )
    if gid:
        return int(gid)
    raise RuntimeError(f"Gudang tidak ditemukan di web (cari: {name_hint})")


def resolve_supplier_id_by_code(pg, supplier_code: str | None) -> int | None:
    code = normalize_text(supplier_code, 50)
    if not code:
        return None
    supplier_id = fetch_one(
        pg, "SELECT id FROM supplier WHERE code = %s LIMIT 1", (code,)
    )
    return int(supplier_id) if supplier_id else None


def fetch_barang_rows_by_kode(mysql, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    placeholders = ", ".join(["%s"] * len(codes))
    sql = CABANG_BARANG_SQL.format(placeholders=placeholders)
    rows = fetch_all_dict(mysql, sql, tuple(codes))
    by_kode: dict[str, dict] = {}
    for row in rows:
        kode = normalize_text(row.get("kode_barang"), 255)
        if kode and kode not in by_kode:
            by_kode[kode] = row
    return by_kode


def upsert_stok_fisik_cabang(
    pg,
    *,
    barang_id: int,
    gudang_id: int,
    qty: int,
    harga_satuan: float,
    user_id: int,
    cabang_label: str,
) -> str:
    """Update/insert stok_akhir fisik per cabang; skip jika qty=0."""
    if qty == 0:
        return "skipped_zero"

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
                SET stok_akhir = %s, updated_by = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (qty, user_id, existing_id),
            )
        return "updated"

    seed_stok_for_barang(
        pg,
        barang_id=barang_id,
        gudang_id=gudang_id,
        qty=qty,
        harga_satuan=harga_satuan,
        user_id=user_id,
        keterangan=f"Stok Awal - Migrasi Cabang {cabang_label}",
    )
    return "inserted"


def migrate_barang_union(
    mysql_ngawi,
    mysql_caruban,
    pg,
    *,
    dry_run: bool,
    user_id: int,
    batch_size: int = 500,
) -> dict:
    """
    Tambah barang ke katalog web dari union kode Ngawi + Caruban yang belum ada.
    Stok cabang diisi terpisah lewat migrate_stok_cabang.
    """
    stats = {
        "union_kode": 0,
        "candidate_missing": 0,
        "inserted": 0,
        "skipped_existing": 0,
        "missing_brand": 0,
        "from_ngawi": 0,
        "from_caruban": 0,
    }

    with Spinner("Memuat kode web..."):
        web_index = load_web_kode_index(pg)

    sources: list[tuple[str, object]] = []
    if mysql_ngawi:
        sources.append(("ngawi", mysql_ngawi))
    if mysql_caruban:
        sources.append(("caruban", mysql_caruban))
    if not sources:
        raise RuntimeError("DB cabang Ngawi/Caruban belum dikonfigurasi di .env")

    union_codes: set[str] = set()
    for label, conn in sources:
        with Spinner(f"Memuat kode aktif {label}..."):
            union_codes |= fetch_kode_aktif(conn)
    stats["union_kode"] = len(union_codes)

    missing = sorted(union_codes - set(web_index))
    stats["candidate_missing"] = len(missing)
    if not missing:
        finish_progress()
        return stats

    inserted_rows: list[dict] = []
    total = len(missing)

    try:
        for batch_start in range(0, total, batch_size):
            batch = missing[batch_start : batch_start + batch_size]
            found: dict[str, dict] = {}
            source_by_kode: dict[str, str] = {}

            if mysql_ngawi:
                for kode, row in fetch_barang_rows_by_kode(mysql_ngawi, batch).items():
                    found[kode] = row
                    source_by_kode[kode] = "ngawi"
            if mysql_caruban:
                remaining = [k for k in batch if k not in found]
                if remaining:
                    for kode, row in fetch_barang_rows_by_kode(
                        mysql_caruban, remaining
                    ).items():
                        found[kode] = row
                        source_by_kode[kode] = "caruban"

            for i, kode in enumerate(batch, 1):
                pos = batch_start + i
                if pos % 100 == 0 or pos == total:
                    show_progress(pos, total, f"inserted {stats['inserted']:,}")

                if kode in web_index:
                    stats["skipped_existing"] += 1
                    continue

                row = found.get(kode)
                if not row:
                    continue

                legacy_pk = str(row["cSTKpk"]).strip()
                nama = normalize_text(row["cSTKdesc"], 255)
                if not nama:
                    continue

                src = source_by_kode.get(kode, "unknown")
                map_type = f"barang_{src}"
                existing_id = lookup_id_map(pg, map_type, legacy_pk)
                if existing_id:
                    stats["skipped_existing"] += 1
                    continue

                golongan = str(row.get("golongan_nama") or "")
                brand_id = resolve_brand_id_for_cabang(
                    pg, golongan, dry_run=dry_run, user_id=user_id
                )
                if brand_id is None:
                    stats["missing_brand"] += 1
                    continue

                supplier_id = resolve_supplier_id_by_code(pg, row.get("supplier_code"))
                harga_beli = resolve_harga_beli(row)
                harga_jual = resolve_harga_jual(row)
                harga_beli_lama = resolve_harga_beli_lama(row)
                harga_jual_lama = resolve_harga_jual_lama(row)
                hpp = resolve_hpp(row)
                ukuran = parse_ukuran(row.get("nstkukuran"), row.get("cSTKsize"))
                is_consignment = int(row.get("nstkkonsi") or 0) == 1
                consignment_pct = row.get("nstktdiscp")

                if dry_run:
                    stats["inserted"] += 1
                    if src == "ngawi":
                        stats["from_ngawi"] += 1
                    elif src == "caruban":
                        stats["from_caruban"] += 1
                    inserted_rows.append(
                        {
                            "kode_barang": kode,
                            "nama": nama,
                            "sumber": src,
                            "golongan": golongan,
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

                save_id_map(pg, map_type, legacy_pk, new_id)
                web_index[kode] = new_id
                stats["inserted"] += 1
                if src == "ngawi":
                    stats["from_ngawi"] += 1
                elif src == "caruban":
                    stats["from_caruban"] += 1
                inserted_rows.append(
                    {
                        "kode_barang": kode,
                        "nama": nama,
                        "sumber": src,
                        "golongan": golongan,
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
        _ensure_output_dir() / "barang_union_inserted.csv",
        ["kode_barang", "nama", "sumber", "golongan", "barang_id"],
        inserted_rows,
    )
    finish_progress()
    return stats


def migrate_stok_cabang(
    branch_connections: dict[str, object],
    pg,
    *,
    dry_run: bool,
    user_id: int,
) -> dict:
    """
    Isi stok_akhir fisik per gudang cabang (Ngawi, Caruban) dari DB backup masing-masing.
    Hanya baris dengan net stok != 0 (sama seperti migrasi Sragen).
    """
    stats: dict[str, int | dict] = {
        "branches": {},
    }

    with Spinner("Memuat index barang web..."):
        web_index = load_web_kode_index(pg)

    try:
        for branch_key, gudang_hint in BRANCH_STOCK_TARGETS:
            mysql = branch_connections.get(branch_key)
            if mysql is None:
                continue

            branch_stats = {
                "ledger_kodes": 0,
                "updated": 0,
                "inserted": 0,
                "skipped_zero": 0,
                "unmapped": 0,
            }

            gudang_id = resolve_gudang_id(pg, gudang_hint)
            net_by_code = fetch_net_stok_by_kode(mysql)
            branch_stats["ledger_kodes"] = len(net_by_code)

            applied_rows: list[dict] = []
            unmapped_rows: list[dict] = []
            items = sorted(net_by_code.items(), key=lambda x: x[0])
            total = len(items)

            for i, (kode, net) in enumerate(items, 1):
                if i % 500 == 0 or i == total:
                    show_progress(
                        i,
                        total,
                        f"{branch_key} updated {branch_stats['updated']:,}",
                    )

                barang_id = web_index.get(kode)
                if not barang_id:
                    branch_stats["unmapped"] += 1
                    if net != 0:
                        unmapped_rows.append(
                            {"kode_barang": kode, "stok_net": net, "cabang": branch_key}
                        )
                    continue

                if net == 0:
                    branch_stats["skipped_zero"] += 1
                    continue

                if dry_run:
                    branch_stats["updated"] += 1
                    applied_rows.append(
                        {
                            "kode_barang": kode,
                            "stok_net": net,
                            "barang_id": barang_id,
                            "cabang": branch_key,
                        }
                    )
                    continue

                action = upsert_stok_fisik_cabang(
                    pg,
                    barang_id=barang_id,
                    gudang_id=gudang_id,
                    qty=net,
                    harga_satuan=0,
                    user_id=user_id,
                    cabang_label=branch_key,
                )
                if action == "inserted":
                    branch_stats["inserted"] += 1
                else:
                    branch_stats["updated"] += 1
                applied_rows.append(
                    {
                        "kode_barang": kode,
                        "stok_net": net,
                        "barang_id": barang_id,
                        "cabang": branch_key,
                        "action": action,
                    }
                )

            out = _ensure_output_dir()
            _write_csv(
                out / f"stok_cabang_{branch_key}_applied.csv",
                ["kode_barang", "stok_net", "barang_id", "cabang", "action"],
                applied_rows,
            )
            _write_csv(
                out / f"stok_cabang_{branch_key}_unmapped.csv",
                ["kode_barang", "stok_net", "cabang"],
                unmapped_rows,
            )
            stats["branches"][branch_key] = branch_stats

        if not dry_run:
            pg.commit()
    except Exception:
        if not dry_run:
            pg.rollback()
        raise

    finish_progress()
    flat = {"branches": len(stats["branches"])}
    for key, bstats in stats["branches"].items():
        for metric, value in bstats.items():
            flat[f"{key}_{metric}"] = value
    return flat
