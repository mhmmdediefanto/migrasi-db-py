"""
Mapping tabel/kolom: MySQL desktop (evacer) → PostgreSQL web (Laravel).

Relasi master (urutan migrasi wajib):
  supplier → brand (golongan) → barang

Catatan:
- "Golongan" di menu web = tabel brands, BUKAN tabel MySQL brand (kosong).
- Golongan desktop disimpan di stockgroup (cGRPpk → brands.id via migration_id_map).
- Barang.brand_id ← stock.cSTKfkGRP → map brand
- Barang.supplier_id ← stock.cSTKfkENT → map supplier (hanya jika ada di entity)
- Group Barang (footwear) tidak ada di MySQL — setup manual di web.
- Stok: stockdetail.outlet01–20 (utama), fallback stock.nSTKopen → barang + stok_akhir + tabel stok.
"""

OUTLET_STOCK_SUM_SQL = " + ".join(f"COALESCE(outlet{i:02d}, 0)" for i in range(1, 21))

STOCK_SOURCES = {
    "primary": {
        "table": "stockdetail",
        "columns": "outlet01 .. outlet20",
        "note": "Jumlah stok per barang per outlet; dijumlahkan ke gudang default web",
    },
    "fallback": {
        "table": "stock",
        "column": "nSTKopen",
        "note": "Stok pembukaan jika outlet semua 0",
    },
    "future": {
        "table": "formuladetail",
        "columns": "nIVDqtyin, nIVDqtyout",
        "note": "Mutasi stok dari transaksi — belum ada data di dump saat ini",
    },
}

COLUMN_MAPPING = {
    "supplier": {
        "source_table": "entity",
        "source_filter": "nENTsupp = 1",
        "target_table": "supplier",
        "columns": {
            "cENTpk": "legacy_pk → migration_id_map",
            "cENTcode": "code",
            "cENTdesc": "name",
            "cENTadd1..5": "address (gabung, skip baris telepon/rekening)",
            "city.cCITdesc": "wilayah (via cENTfkCIT)",
            "cENTadd1/5, centadd*s, cENTimage": "phone (ekstrak regex)",
            "cENTmemo": "nama_pemilik_rekening, nomor_rekening, nama_bank (parse)",
            "cENTrek": "nomor_rekening (fallback, jarang terisi)",
            "cENTacc": "nama_bank (fallback, jarang terisi)",
            "nENTsuspend": "status (0=aktif)",
        },
    },
    "brand": {
        "source_table": "stockgroup",
        "target_table": "brands",
        "ui_label": "Golongan",
        "columns": {
            "cGRPpk": "legacy_pk → migration_id_map",
            "cGRPdesc": "name",
            "serino": "code (fallback slug dari name)",
            "markpersen1": "margin_percent",
            "nama mengandung PPN": "is_ppn = true",
        },
    },
    "gudang": {
        "source_table": "warehouse",
        "target_table": "master_gudang",
        "note": "Opsional — biasanya pakai gudang yang sudah ada di web",
        "columns": {
            "cWHSpk": "legacy_pk",
            "cWHSdesc": "name",
        },
    },
    "store": {
        "source_table": "outlet",
        "target_table": "master_store",
        "note": "Opsional — data desktop sering N/A, pakai master_store web",
        "columns": {
            "coutpk": "legacy_pk",
            "coutdesc": "name",
        },
    },
    "barang": {
        "source_table": "stock + stockdetail",
        "target_table": "barang",
        "depends_on": ["supplier", "brand"],
        "columns": {
            "sd.cSTDcode": "kode_barang (MIN per stock)",
            "cSTKdesc": "nama",
            "nSTKbuy": "harga_beli (prioritas 1)",
            "sd.nSTDprice": "harga_beli (fallback)",
            "sd.nSTDoprice": "harga_beli_lama",
            "stock.nstkhbeli": "harga_beli_lama (fallback jika beda dari nSTKbuy)",
            "sd.nSTDretail": "harga_jual (prioritas 1 — harga aktif desktop/POS)",
            "sd.nSTDprice": "harga_jual (fallback jika retail kosong)",
            "nHrgQty01": "harga_jual_lama (legacy, BUKAN harga jual aktif)",
            "sd.nSTDoretail": "harga_jual_lama (prioritas 1)",
            "nSTKcogs": "hpp (prioritas 1)",
            "nSTKbuy": "hpp (fallback)",
            "cSTKfkGRP": "brand_id via migration_id_map",
            "cSTKfkENT": "supplier_id via migration_id_map",
            "nstkukuran / cSTKsize": "ukuran",
            "cSTKcolor": "warna",
            "nstkkonsi": "is_consignment + type",
            "nstktdiscp": "consignment_percentage",
            "stockdetail.outlet01-20": "stok → barang.stok_awal/akhir + stok_akhir",
            "stock.nSTKopen": "stok fallback jika outlet kosong",
        },
    },
    "stok": STOCK_SOURCES,
}

TRUNCATE_ORDER = ["barang", "brand", "supplier", "store", "gudang"]

TRUNCATE_TABLES = {
    "barang": "barang",
    "brand": "brands",
    "supplier": "supplier",
    "store": "master_store",
    "gudang": "master_gudang",
}

SKIP_TRUNCATE = {"store", "gudang"}
