-- DB: sragen | Golongan: 07/MULTIGARMENTAMA + PPN
-- Butuh MySQL 8+ (window function). Jalankan SELURUH file ini.

WITH barang_kode AS (
    SELECT
        s.cSTKpk,
        MIN(TRIM(sd.cSTDcode)) AS kode_barang,
        TRIM(s.cSTKdesc) AS nama_barang,
        TRIM(g.cGRPdesc) AS golongan
    FROM stock s
    INNER JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
    INNER JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
    WHERE s.nSTKsuspend = 0
      AND sd.cSTDcode IS NOT NULL
      AND TRIM(sd.cSTDcode) <> ''
    GROUP BY s.cSTKpk, TRIM(s.cSTKdesc), TRIM(g.cGRPdesc)
),
ringkasan AS (
    SELECT
        bk.kode_barang,
        bk.nama_barang,
        bk.golongan,
        SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok
    FROM invoicedetail d
    INNER JOIN barang_kode bk ON bk.cSTKpk = d.cIVDfkSTK
    WHERE bk.golongan = '07/MULTIGARMENTAMA + PPN'
    GROUP BY bk.kode_barang, bk.nama_barang, bk.golongan
    HAVING SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) < 0
),
trx AS (
    SELECT
        bk.kode_barang,
        bk.nama_barang,
        bk.golongan,
        r.net_stok AS net_stok_ringkasan,
        i.dINVdate AS tanggal,
        TRIM(COALESCE(i.cINVrefno, i.cINVpk)) AS no_faktur,
        TRIM(COALESCE(f.cINVremark, f.serino, '')) AS jenis_transaksi,
        TRIM(COALESCE(e.cENTdesc, '')) AS pihak,
        COALESCE(d.nIVDqtyin, 0) AS qty_masuk,
        COALESCE(d.nIVDqtyout, 0) AS qty_keluar,
        COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0) AS mutasi,
        d.cIVDpk
    FROM invoicedetail d
    INNER JOIN invoice i ON i.cINVpk = d.cIVDfkINV
    INNER JOIN barang_kode bk ON bk.cSTKpk = d.cIVDfkSTK
    INNER JOIN ringkasan r ON r.kode_barang = bk.kode_barang
    LEFT JOIN formula f ON f.cINVpk = i.cINVfkEXC
    LEFT JOIN entity e ON e.cENTpk = i.cINVfkENT
),
ledger AS (
    SELECT
        t.*,
        ROW_NUMBER() OVER (
            PARTITION BY t.kode_barang
            ORDER BY t.tanggal, t.no_faktur, t.cIVDpk
        ) AS urutan,
        COUNT(*) OVER (PARTITION BY t.kode_barang) AS total_transaksi,
        SUM(t.mutasi) OVER (
            PARTITION BY t.kode_barang
            ORDER BY t.tanggal, t.no_faktur, t.cIVDpk
            ROWS UNBOUNDED PRECEDING
        ) AS saldo_berjalan
    FROM trx t
)
SELECT
    kode_barang,
    nama_barang,
    golongan,
    net_stok_ringkasan,
    urutan,
    total_transaksi,
    tanggal,
    no_faktur,
    jenis_transaksi,
    pihak,
    qty_masuk,
    qty_keluar,
    mutasi,
    saldo_berjalan,
    CASE
        WHEN urutan = total_transaksi AND saldo_berjalan = net_stok_ringkasan THEN 'YA'
        ELSE ''
    END AS cocok_akhir
FROM ledger
ORDER BY kode_barang, urutan;
