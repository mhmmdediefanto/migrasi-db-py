-- DB: sragen | Golongan: 07/MULTIGARMENTAMA + PPN
-- Jalankan SELURUH isi file ini (Ctrl+Enter)

SELECT
    bk.golongan,
    bk.kode_barang,
    bk.nama_barang,
    SUM(COALESCE(d.nIVDqtyin, 0)) AS total_masuk,
    SUM(COALESCE(d.nIVDqtyout, 0)) AS total_keluar,
    SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok,
    COUNT(*) AS jumlah_transaksi
FROM invoicedetail d
INNER JOIN (
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
) bk ON bk.cSTKpk = d.cIVDfkSTK
WHERE bk.golongan = '07/MULTIGARMENTAMA + PPN'
GROUP BY bk.golongan, bk.kode_barang, bk.nama_barang
HAVING SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) < 0
ORDER BY bk.kode_barang;
