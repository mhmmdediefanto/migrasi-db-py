-- DB: sragen | Golongan: 07/MULTIGARMENTAMA + PPN
-- Buka HANYA file ini di DBeaver → Ctrl+A → Execute

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
    SELECT bk.kode_barang
    FROM invoicedetail d
    INNER JOIN barang_kode bk ON bk.cSTKpk = d.cIVDfkSTK
    WHERE bk.golongan = '07/MULTIGARMENTAMA + PPN'
    GROUP BY bk.kode_barang
    HAVING SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) < 0
)
SELECT
    bk.kode_barang,
    bk.nama_barang,
    bk.golongan,
    i.dINVdate AS tanggal,
    TRIM(COALESCE(i.cINVrefno, i.cINVpk)) AS no_faktur,
    TRIM(COALESCE(f.cINVremark, f.serino, '')) AS jenis_transaksi,
    TRIM(COALESCE(e.cENTdesc, '')) AS pihak,
    COALESCE(d.nIVDqtyin, 0) AS qty_masuk,
    COALESCE(d.nIVDqtyout, 0) AS qty_keluar,
    COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0) AS mutasi,
    COALESCE(d.nIVDprice, 0) AS harga,
    d.cIVDpk
FROM invoicedetail d
INNER JOIN invoice i ON i.cINVpk = d.cIVDfkINV
INNER JOIN barang_kode bk ON bk.cSTKpk = d.cIVDfkSTK
INNER JOIN ringkasan r ON r.kode_barang = bk.kode_barang
LEFT JOIN formula f ON f.cINVpk = i.cINVfkEXC
LEFT JOIN entity e ON e.cENTpk = i.cINVfkENT
ORDER BY bk.kode_barang, i.dINVdate, no_faktur, d.cIVDpk;
