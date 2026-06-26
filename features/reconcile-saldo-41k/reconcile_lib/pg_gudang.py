"""Join penjualan → sales/kasir → toko → gudang."""

# Hitung stok per gudang: hanya penjualan.sales_id → users → master_store.gudang_id
PENJUALAN_SALES_JOIN = """
LEFT JOIN users u_sales ON u_sales.id = p.sales_id
LEFT JOIN master_store ms ON ms.id = u_sales.store_id
LEFT JOIN master_gudang mg_sales ON mg_sales.id = ms.gudang_id
"""

# Retur penjualan: ikut faktur asal → sales → toko → gudang
PENJUALAN_RETURN_SALES_JOIN = """
LEFT JOIN penjualan p ON p.id = pr.penjualan_id AND p.deleted_at IS NULL
LEFT JOIN users u_sales ON u_sales.id = p.sales_id
LEFT JOIN master_store ms ON ms.id = u_sales.store_id
LEFT JOIN master_gudang mg_sales ON mg_sales.id = ms.gudang_id
"""

# Export/display: fallback ke created_by jika sales kosong (hanya label, bukan hitung stok)
PENJUALAN_KASIR_JOIN = """
LEFT JOIN users u_kasir ON u_kasir.id = COALESCE(p.sales_id, p.created_by)
LEFT JOIN master_store ms ON ms.id = u_kasir.store_id
LEFT JOIN master_gudang mg_kasir ON mg_kasir.id = ms.gudang_id
"""

PENJUALAN_KASIR_SELECT = """
    u_kasir.name AS kasir,
    ms.name AS toko,
    mg_kasir.name AS gudang_kasir,
    mg_kasir.id AS gudang_kasir_id
"""

PEMBELIAN_GUDANG_JOIN = """
LEFT JOIN master_gudang mg ON mg.id = p.gudang_id
"""

PEMBELIAN_GUDANG_SELECT = """
    mg.name AS gudang,
    p.gudang_id AS gudang_id
"""
