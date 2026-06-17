from __future__ import annotations

from lib.config import (
    normalize_text,
    parse_ukuran,
    resolve_harga_beli,
    resolve_harga_beli_lama,
    resolve_harga_jual,
    resolve_harga_jual_lama,
    resolve_hpp,
    resolve_stok_qty,
    resolve_supplier_fields,
    slug_code,
)
from lib.db import fetch_all_dict, fetch_one, lookup_id_map, save_id_map
from lib.progress import Spinner, finish_progress, print_step, show_progress


def migrate_suppliers(mysql, pg, *, dry_run: bool, user_id: int) -> dict:
    stats = {"read": 0, "inserted": 0, "skipped": 0, "mapped": 0}

    with Spinner("Membaca supplier dari MySQL..."):
        rows = fetch_all_dict(
            mysql,
            """
            SELECT
                e.cENTpk, e.cENTcode, e.cENTdesc,
                e.cENTadd1, e.cENTadd2, e.cENTadd3, e.cENTadd4, e.cENTadd5,
                e.cENTmemo, e.cENTrek, e.cENTacc, e.cENTimage,
                e.centadd1s, e.centadd2s, e.centadd3s, e.centadd4s, e.centadd5s,
                e.nENTsuspend,
                c.cCITdesc AS wilayah
            FROM entity e
            LEFT JOIN city c ON c.cCITpk = e.cENTfkCIT
            WHERE e.nENTsupp = 1
            ORDER BY e.cENTdesc
            """,
        )

    total = len(rows)
    for row in rows:
        stats["read"] += 1
        show_progress(stats["read"], total, "supplier")
        legacy_pk = str(row["cENTpk"]).strip()
        code = normalize_text(row["cENTcode"], 50)
        name = normalize_text(row["cENTdesc"], 150)

        if not code or not name:
            stats["skipped"] += 1
            continue

        existing_id = lookup_id_map(pg, "supplier", legacy_pk)
        if existing_id:
            stats["mapped"] += 1
            continue

        existing_id = fetch_one(pg, "SELECT id FROM supplier WHERE code = %s LIMIT 1", (code,))
        if existing_id:
            if not dry_run:
                save_id_map(pg, "supplier", legacy_pk, existing_id)
                pg.commit()
            stats["mapped"] += 1
            continue

        fields = resolve_supplier_fields(row)

        if dry_run:
            stats["inserted"] += 1
            continue

        with pg.cursor() as cur:
            cur.execute(
                """
                INSERT INTO supplier (
                    code, name, address, wilayah, phone, nama_bank, nomor_rekening,
                    nama_pemilik_rekening, status, created_by, updated_by, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (
                    code,
                    name,
                    fields["address"],
                    fields["wilayah"],
                    fields["phone"],
                    fields["nama_bank"],
                    fields["nomor_rekening"],
                    fields["nama_pemilik_rekening"],
                    fields["status"],
                    user_id,
                    user_id,
                ),
            )
            new_id = cur.fetchone()[0]
        save_id_map(pg, "supplier", legacy_pk, new_id)
        pg.commit()
        stats["inserted"] += 1

    finish_progress()
    return stats


def migrate_brands(mysql, pg, *, dry_run: bool, user_id: int) -> dict:
    stats = {"read": 0, "inserted": 0, "skipped": 0, "mapped": 0}

    with Spinner("Membaca golongan dari MySQL..."):
        rows = fetch_all_dict(
            mysql,
            "SELECT cGRPpk, cGRPdesc, serino, markpersen1 FROM stockgroup ORDER BY cGRPdesc",
        )

    with pg.cursor() as cur:
        cur.execute("SELECT code FROM brands WHERE code IS NOT NULL")
        used_codes = {str(row[0]).upper() for row in cur.fetchall()}

    for row in rows:
        stats["read"] += 1
        show_progress(stats["read"], len(rows), "golongan")
        legacy_pk = str(row["cGRPpk"]).strip()
        name = normalize_text(row["cGRPdesc"], 255)

        if not name:
            stats["skipped"] += 1
            continue

        existing_id = lookup_id_map(pg, "brand", legacy_pk)
        if existing_id:
            stats["mapped"] += 1
            continue

        existing_id = fetch_one(pg, "SELECT id FROM brands WHERE name = %s LIMIT 1", (name,))
        if existing_id:
            if not dry_run:
                save_id_map(pg, "brand", legacy_pk, existing_id)
                pg.commit()
            stats["mapped"] += 1
            continue

        code = normalize_text(row["serino"], 50) or slug_code(name)
        base_code = code
        suffix = 1
        while code.upper() in used_codes:
            suffix += 1
            code = f"{base_code[:max(1, 50 - len(str(suffix)) - 1)]}_{suffix}"
        used_codes.add(code.upper())

        margin = float(row["markpersen1"] or 0)
        is_ppn = "PPN" in name.upper()

        if dry_run:
            stats["inserted"] += 1
            continue

        with pg.cursor() as cur:
            cur.execute(
                """
                INSERT INTO brands (code, name, is_ppn, margin_percent, created_by, updated_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (code, name, is_ppn, margin, user_id, user_id),
            )
            new_id = cur.fetchone()[0]
        save_id_map(pg, "brand", legacy_pk, new_id)
        pg.commit()
        stats["inserted"] += 1

    finish_progress()
    return stats


def migrate_gudang(mysql, pg, *, dry_run: bool, user_id: int) -> dict:
    stats = {"read": 0, "inserted": 0, "skipped": 0, "mapped": 0}
    rows = fetch_all_dict(mysql, "SELECT cWHSpk, cWHSdesc FROM warehouse ORDER BY cWHSdesc")

    for row in rows:
        stats["read"] += 1
        legacy_pk = str(row["cWHSpk"]).strip()
        name = normalize_text(row["cWHSdesc"], 150)

        if not name:
            stats["skipped"] += 1
            continue

        existing_id = lookup_id_map(pg, "gudang", legacy_pk)
        if existing_id:
            stats["mapped"] += 1
            continue

        existing_id = fetch_one(pg, "SELECT id FROM master_gudang WHERE name = %s LIMIT 1", (name,))
        if existing_id:
            if not dry_run:
                save_id_map(pg, "gudang", legacy_pk, existing_id)
                pg.commit()
            stats["mapped"] += 1
            continue

        if dry_run:
            stats["inserted"] += 1
            continue

        with pg.cursor() as cur:
            cur.execute(
                """
                INSERT INTO master_gudang (name, status, created_by, updated_by, created_at, updated_at)
                VALUES (%s, true, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (name, user_id, user_id),
            )
            new_id = cur.fetchone()[0]
        save_id_map(pg, "gudang", legacy_pk, new_id)
        pg.commit()
        stats["inserted"] += 1

    return stats


def default_gudang_id(pg) -> int:
    mapped = fetch_one(
        pg,
        "SELECT new_id FROM migration_id_map WHERE entity_type = 'gudang' ORDER BY new_id LIMIT 1",
    )
    if mapped:
        return int(mapped)

    existing = fetch_one(pg, "SELECT id FROM master_gudang ORDER BY id LIMIT 1")
    if not existing:
        raise RuntimeError("Tidak ada master_gudang. Jalankan migrasi gudang dulu.")
    return int(existing)


def migrate_store(mysql, pg, *, dry_run: bool, user_id: int) -> dict:
    stats = {"read": 0, "inserted": 0, "skipped": 0, "mapped": 0}
    gudang_id = default_gudang_id(pg)
    rows = fetch_all_dict(mysql, "SELECT coutpk, coutdesc FROM outlet ORDER BY coutdesc")

    for row in rows:
        stats["read"] += 1
        legacy_pk = str(row["coutpk"]).strip()
        name = normalize_text(row["coutdesc"], 150)

        if not name or name.upper() == "N/A":
            stats["skipped"] += 1
            continue

        existing_id = lookup_id_map(pg, "store", legacy_pk)
        if existing_id:
            stats["mapped"] += 1
            continue

        existing_id = fetch_one(pg, "SELECT id FROM master_store WHERE name = %s LIMIT 1", (name,))
        if existing_id:
            if not dry_run:
                save_id_map(pg, "store", legacy_pk, existing_id)
                pg.commit()
            stats["mapped"] += 1
            continue

        if dry_run:
            stats["inserted"] += 1
            continue

        with pg.cursor() as cur:
            cur.execute(
                """
                INSERT INTO master_store (name, gudang_id, status, created_by, updated_by, created_at, updated_at)
                VALUES (%s, %s, true, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (name, gudang_id, user_id, user_id),
            )
            new_id = cur.fetchone()[0]
        save_id_map(pg, "store", legacy_pk, new_id)
        pg.commit()
        stats["inserted"] += 1

    return stats


BARANG_SOURCE_SQL = """
    SELECT
        s.cSTKpk,
        s.cSTKdesc,
        s.cSTKfkGRP,
        s.cSTKfkENT,
        s.nSTKbuy,
        s.nstkhbeli,
        s.nHrgQty01,
        s.nSTKcogs,
        s.nSTKopen,
        s.cSTKsize,
        s.nstkukuran,
        s.cSTKcolor,
        s.nstkkonsi,
        s.nstktdiscp,
        sd.kode_barang,
        sd.harga_retail,
        sd.harga_price,
        sd.stok_outlet_total
    FROM stock s
    LEFT JOIN (
        SELECT
            cSTDfkSTK,
            MIN(CASE WHEN cSTDcode <> '' THEN cSTDcode END) AS kode_barang,
            MAX(CASE WHEN nSTDretail > 0 THEN nSTDretail END) AS harga_retail,
            MAX(CASE WHEN nSTDprice > 0 THEN nSTDprice END) AS harga_price,
            MAX(CASE WHEN nSTDoprice > 0 THEN nSTDoprice END) AS harga_beli_lama_src,
            MAX(CASE WHEN nSTDoretail > 0 THEN nSTDoretail END) AS harga_jual_lama_src,
            SUM(
                COALESCE(outlet01, 0) + COALESCE(outlet02, 0) + COALESCE(outlet03, 0)
                + COALESCE(outlet04, 0) + COALESCE(outlet05, 0) + COALESCE(outlet06, 0)
                + COALESCE(outlet07, 0) + COALESCE(outlet08, 0) + COALESCE(outlet09, 0)
                + COALESCE(outlet10, 0) + COALESCE(outlet11, 0) + COALESCE(outlet12, 0)
                + COALESCE(outlet13, 0) + COALESCE(outlet14, 0) + COALESCE(outlet15, 0)
                + COALESCE(outlet16, 0) + COALESCE(outlet17, 0) + COALESCE(outlet18, 0)
                + COALESCE(outlet19, 0) + COALESCE(outlet20, 0)
            ) AS stok_outlet_total
        FROM stockdetail
        GROUP BY cSTDfkSTK
    ) sd ON sd.cSTDfkSTK = s.cSTKpk
    WHERE s.nSTKsuspend = 0
    ORDER BY s.cSTKpk
    LIMIT %s OFFSET %s
"""


def seed_stok_for_barang(
    pg,
    *,
    barang_id: int,
    gudang_id: int,
    qty: int,
    harga_satuan: float | None,
    user_id: int,
) -> None:
    """Isi stok_akhir + jejak stok awal (selaras dengan AutoStockService web)."""
    # Client memperbolehkan stok minus (hasil net transaksi)
    if qty == 0:
        return

    harga = float(harga_satuan or 0)
    jenis = "masuk" if qty > 0 else "keluar"
    masuk = qty if qty > 0 else 0
    keluar = abs(qty) if qty < 0 else 0

    with pg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stok_akhir (
                barang_id, gudang_id, stok_akhir, harga_satuan,
                created_by, updated_by, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (barang_id, gudang_id, qty, harga, user_id, user_id),
        )
        cur.execute(
            """
            INSERT INTO stok (
                barang_id, gudang_id, keterangan,
                stok_sebelum, stok_masuk, stok_keluar, stok_akhir,
                jenis, harga_satuan, created_by, updated_by, created_at, updated_at
            ) VALUES (
                %s, %s, %s,
                0, %s, %s, %s,
                %s, %s, %s, %s, NOW(), NOW()
            )
            """,
            (
                barang_id,
                gudang_id,
                "Stok Awal - Migrasi Desktop",
                masuk,
                keluar,
                qty,
                jenis,
                harga,
                user_id,
                user_id,
            ),
        )


def fetch_net_stok_from_trx(mysql_trx, legacy_pks: list[str]) -> dict[str, int]:
    """
    Hitung stok net dari tabel transaksi: SUM(qtyin - qtyout) per barang.
    Note: legacy_pks adalah cSTKpk.
    """
    if not legacy_pks:
        return {}

    placeholders = ", ".join(["%s"] * len(legacy_pks))
    sql = f"""
        SELECT cIVDfkSTK,
            SUM(COALESCE(nIVDqtyin,0) - COALESCE(nIVDqtyout,0)) AS net
        FROM invoicedetail
        WHERE cIVDfkSTK IN ({placeholders})
        GROUP BY cIVDfkSTK
    """

    result: dict[str, int] = {}
    with mysql_trx.cursor() as cur:
        cur.execute(sql, tuple(legacy_pks))
        for row in cur.fetchall():
            pk = str(row["cIVDfkSTK"]).strip()
            try:
                net = int(round(float(row["net"] or 0)))
            except (TypeError, ValueError):
                net = 0
            result[pk] = net
    return result


def migrate_barang(
    mysql_master,
    mysql_trx,
    pg,
    *,
    dry_run: bool,
    user_id: int,
    batch_size: int,
    limit: int = 0,
    offset: int = 0,
) -> dict:
    stats = {
        "read": 0,
        "inserted": 0,
        "skipped": 0,
        "mapped": 0,
        "missing_brand": 0,
        "missing_supplier": 0,
        "with_stok": 0,
        "with_stok_negative": 0,
        "total": 0,
    }

    gudang_id = default_gudang_id(pg)

    with Spinner("Menghitung total barang di MySQL..."):
        with mysql_master.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM stock WHERE nSTKsuspend = 0")
            total = int(cur.fetchone()["total"])
    stats["total"] = total

    max_rows = min(limit, total - offset) if limit > 0 else (total - offset)
    if max_rows <= 0:
        return stats

    processed = 0
    while processed < max_rows:
        current_limit = min(batch_size, max_rows - processed)
        with Spinner(f"Mengambil batch barang ({processed:,}/{max_rows:,})..."):
            rows = fetch_all_dict(
                mysql_master, BARANG_SOURCE_SQL, (current_limit, offset + processed)
            )
        if not rows:
            break

        # Ambil stok net dari transaksi (full backup).
        legacy_pks = [str(r["cSTKpk"]).strip() for r in rows]
        net_by_pk = {}
        try:
            with Spinner(
                f"Menghitung stok transaksi ({processed:,}/{max_rows:,})..."
            ):
                net_by_pk = fetch_net_stok_from_trx(mysql_trx, legacy_pks)
        except Exception:
            net_by_pk = {}

        batch_start = processed
        try:
            for i, row in enumerate(rows, 1):
                stats["read"] += 1
                if i % 25 == 0 or i == len(rows):
                    show_progress(
                        batch_start + i,
                        max_rows,
                        f"inserted {stats['inserted']:,} | mapped {stats['mapped']:,}",
                    )
                legacy_pk = str(row["cSTKpk"]).strip()
                kode = normalize_text(row["kode_barang"], 255)
                nama = normalize_text(row["cSTKdesc"], 255)

                if not kode or not nama:
                    stats["skipped"] += 1
                    continue

                # Untuk validasi (dry-run), hitung stok walaupun data sudah pernah dimapping.
                if dry_run:
                    stok_qty_preview = net_by_pk.get(legacy_pk)
                    if stok_qty_preview is None:
                        stok_qty_preview = resolve_stok_qty(row)
                    if stok_qty_preview > 0:
                        stats["with_stok"] += 1
                    elif stok_qty_preview < 0:
                        stats["with_stok_negative"] += 1

                existing_id = lookup_id_map(pg, "barang", legacy_pk)
                if existing_id:
                    stats["mapped"] += 1
                    continue

                existing_id = fetch_one(
                    pg, "SELECT id FROM barang WHERE kode_barang = %s LIMIT 1", (kode,)
                )
                if existing_id:
                    if not dry_run:
                        save_id_map(pg, "barang", legacy_pk, existing_id)
                    stats["mapped"] += 1
                    continue

                brand_id = lookup_id_map(pg, "brand", str(row.get("cSTKfkGRP") or "").strip())
                supplier_id = lookup_id_map(
                    pg, "supplier", str(row.get("cSTKfkENT") or "").strip()
                )
                if row.get("cSTKfkGRP") and not brand_id:
                    stats["missing_brand"] += 1
                if row.get("cSTKfkENT") and not supplier_id:
                    stats["missing_supplier"] += 1

                ukuran = parse_ukuran(row.get("nstkukuran"), row.get("cSTKsize"))
                is_consignment = int(row.get("nstkkonsi") or 0) == 1
                consignment_pct = row.get("nstktdiscp")
                harga_beli = resolve_harga_beli(row)
                harga_jual = resolve_harga_jual(row)
                harga_beli_lama = resolve_harga_beli_lama(row)
                harga_jual_lama = resolve_harga_jual_lama(row)
                hpp = resolve_hpp(row)
                # Prioritas stok:
                # 1) transaksi (invoicedetail) jika tersedia
                # 2) outlet total (stockdetail.outlet01-20)
                # 3) opening (stock.nSTKopen)
                stok_qty = net_by_pk.get(legacy_pk)
                if stok_qty is None:
                    stok_qty = resolve_stok_qty(row)
                if stok_qty > 0:
                    stats["with_stok"] += 1
                elif stok_qty < 0:
                    stats["with_stok_negative"] += 1

                if dry_run:
                    stats["inserted"] += 1
                    continue

                harga_satuan = harga_jual or harga_beli or 0

                with pg.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO barang (
                            kode_barang, nama, stok_awal, stok_akhir, harga_beli, harga_jual,
                            harga_beli_lama, harga_jual_lama, hpp,
                            brand_id, supplier_id, ukuran, warna, type, is_consignment,
                            consignment_percentage, created_by, updated_by, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, NOW(), NOW()
                        )
                        RETURNING id
                        """,
                        (
                            kode,
                            nama,
                            stok_qty,
                            stok_qty,
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
                    new_id = cur.fetchone()[0]

                seed_stok_for_barang(
                    pg,
                    barang_id=new_id,
                    gudang_id=gudang_id,
                    qty=stok_qty,
                    harga_satuan=harga_satuan,
                    user_id=user_id,
                )
                save_id_map(pg, "barang", legacy_pk, new_id)
                stats["inserted"] += 1

            if not dry_run:
                pg.commit()
        except Exception:
            if not dry_run:
                pg.rollback()
            raise

        processed += len(rows)
        show_progress(processed, max_rows, f"inserted {stats['inserted']:,}")

    finish_progress()
    return stats
