# Migrasi Stok — Sumber Data, Mapping Kolom, & Troubleshooting

Dokumen ini menjelaskan **dari kolom MySQL mana stok diambil**, bagaimana ditulis ke PostgreSQL (web), dan **kenapa angka stok bisa beda** dengan aplikasi desktop lama.

Dokumen terkait:

- [`MIGRASI-URUTAN.md`](MIGRASI-URUTAN.md) — urutan migrasi lengkap
- [`MIGRASI-CABANG.md`](MIGRASI-CABANG.md) — stok Ngawi & Caruban
- [`MIGRASI-STOK-PAJAK.md`](MIGRASI-STOK-PAJAK.md) — kolom `stok_pajak` (terpisah dari stok fisik)
- [`DOKUMENTASI-MIGRASI.md`](DOKUMENTASI-MIGRASI.md) — dokumentasi migrasi umum

---

## Ringkasan

Di web, stok dibaca **per gudang** dari tabel `stok_akhir`:

| Cabang di UI web | Gudang PostgreSQL | Database MySQL sumber |
|------------------|-------------------|------------------------|
| **Sragen** | Gudang Pusat | `MYSQL_TRX_DATABASE` (mis. `evacer_sragen`) |
| **Ngawi** | Gudang Ngawi | `MYSQL_TRX_DATABASE_NGAWI` |
| **Caruban** | Gudang Caruban | `MYSQL_TRX_DATABASE_CARUBAN` |

Kolom terpisah (bukan stok fisik cabang):

| Kolom PG | Sumber | Keterangan |
|----------|--------|------------|
| `stok_akhir.stok_pajak` | `evacer_pajak_backup` | Stok pajak — lihat [`MIGRASI-STOK-PAJAK.md`](MIGRASI-STOK-PAJAK.md) |

---

## Kolom MySQL yang dipakai migrasi

### Stok fisik — prioritas utama: ledger transaksi

Migrasi **tidak** membaca angka stok statis di master barang sebagai sumber utama. Sumber utama adalah **net transaksi**:

```sql
SUM(COALESCE(nIVDqtyin, 0) - COALESCE(nIVDqtyout, 0))
```

| Tabel | Kolom | Fungsi |
|-------|-------|--------|
| `invoicedetail` | `nIVDqtyin` | Qty masuk (pembelian, retur masuk, adjustment in, dll.) |
| `invoicedetail` | `nIVDqtyout` | Qty keluar (penjualan, retur keluar, adjustment out, dll.) |
| `invoicedetail` | `cIVDfkSTK` | FK ke `stock.cSTKpk` |
| `stock` | `cSTKpk` | Primary key barang desktop |
| `stockdetail` | `cSTDcode` | Kode barang (`kode_barang` di web) |
| `stockdetail` | `cSTDfkSTK` | FK ke `stock` |

Implementasi:

- **Sragen / Pusat** → `lib/migrators.py` → `fetch_net_stok_from_trx()`
- **Ngawi & Caruban** → `lib/cabang_migrators.py` → `NET_STOK_BY_KODE_SQL`

### Stok fisik — fallback (hanya Sragen saat migrasi `barang`)

Jika ledger transaksi kosong / tidak tersedia untuk barang tertentu:

| Prioritas | Tabel | Kolom | Catatan |
|-----------|-------|-------|---------|
| 2 | `stockdetail` | `outlet01` … `outlet20` | Dijumlahkan (`stok_outlet_total`) |
| 3 | `stock` | `nSTKopen` | Stok pembukaan |

Fungsi: `resolve_stok_qty()` di `lib/config.py`.

> **Penting:** Di backup production saat ini, `outlet01–20` dan `nSTKopen` hampir selalu **0**. Stok Sragen praktis **100% dari `invoicedetail`**.

### Kolom yang TIDAK dipakai untuk stok fisik

| Tabel | Kolom | Alasan |
|-------|-------|--------|
| `stock` | `nHrgQty01` | Harga jual lama — bukan stok |
| `stockdetail` | `nSTDretail`, `nSTDprice` | Harga — bukan stok |
| `formuladetail` | `nIVDqtyin`, `nIVDqtyout` | Ada di mapping masa depan, belum diimplementasi |

---

## Alur per cabang

### 1. Sragen — Gudang Pusat (`--only=barang`)

**Kapan:** migrasi barang pertama kali.

**Sumber MySQL:** `MYSQL_TRX_DATABASE` (full backup Sragen).

**Rumus stok:**

```
1. invoicedetail: SUM(nIVDqtyin - nIVDqtyout) per cSTKpk
2. fallback: SUM(stockdetail.outlet01..20)
3. fallback: stock.nSTKopen
```

**Ditulis ke PostgreSQL:**

| Tabel PG | Kolom |
|----------|-------|
| `barang` | `stok_awal`, `stok_akhir` |
| `stok_akhir` | `stok_akhir` @ Gudang Pusat |
| `stok` | jejak ledger "Stok Awal - Migrasi Desktop" |

**Kebijakan:** stok **boleh minus** (sesuai ketentuan client).

```bash
./run.sh run --only=barang
```

---

### 2. Ngawi & Caruban (`--only=stok_cabang`)

**Kapan:** setelah katalog barang sudah ada di web.

**Sumber MySQL:** DB backup masing-masing cabang.

**Rumus stok:**

```sql
SUM(nIVDqtyin - nIVDqtyout) GROUP BY kode_barang
```

**Tidak pakai** `outlet01–20` atau `nSTKopen`.

**Ditulis ke PostgreSQL:**

| Cabang | Gudang | Kolom |
|--------|--------|-------|
| Ngawi | Gudang Ngawi | `stok_akhir.stok_akhir` |
| Caruban | Gudang Caruban | `stok_akhir.stok_akhir` |

Hanya baris dengan **net ≠ 0** yang di-insert/update. Net = 0 di-skip.

```bash
./run.sh run --only=stok_cabang --dry-run
./run.sh run --only=stok_cabang
```

**Tidak mengubah** stok Gudang Pusat (Sragen sudah dari langkah `barang`).

Detail: [`MIGRASI-CABANG.md`](MIGRASI-CABANG.md)

---

### 3. Stok pajak (`--only=stok_pajak`)

Kolom **`stok_pajak`** terpisah — bukan angka yang tampil di kolom Sragen/Ngawi/Caruban di UI pencarian barang.

Detail: [`MIGRASI-STOK-PAJAK.md`](MIGRASI-STOK-PAJAK.md)

---

## Mapping `.env` → sumber stok

```env
# Sragen / Pusat — ledger transaksi utama
MYSQL_TRX_DATABASE=evacer_sragen

# Master barang (harga, nama, kode) — BUKAN sumber stok utama
MYSQL_MASTER_DATABASE=evacer_sragen

# Cabang
MYSQL_TRX_DATABASE_NGAWI=evacer_ngawi_fullbackup
MYSQL_TRX_DATABASE_CARUBAN=evacer_caruban_fullbackup

# Pajak (kolom stok_pajak saja)
MYSQL_TRX_PAJAK_DATABASE=evacer_pajak_backup
```

| Variabel | Dipakai untuk stok? |
|----------|---------------------|
| `MYSQL_TRX_DATABASE` | ✅ Sragen / Gudang Pusat |
| `MYSQL_MASTER_DATABASE` | ❌ (master data barang saja) |
| `MYSQL_TRX_DATABASE_NGAWI` | ✅ Ngawi |
| `MYSQL_TRX_DATABASE_CARUBAN` | ✅ Caruban |
| `MYSQL_TRX_PAJAK_DATABASE` | ✅ `stok_pajak` saja |

---

## Kenapa stok web ≠ desktop?

### 1. Sumber data berbeda

| Aplikasi | Kemungkinan sumber tampilan |
|----------|----------------------------|
| **Desktop lama** | Bisa tampilkan stok dari kolom master, outlet, atau snapshot UI |
| **Migrasi web** | **Selalu** hitung net dari `invoicedetail` (ledger penuh) |

Kalau desktop menampilkan angka dari kolom selain ledger, hasilnya bisa beda meski backup sama tanggal.

### 2. Ledger vs snapshot

Migrasi menghitung **seluruh riwayat transaksi** di backup:

```
stok migrasi = total masuk - total keluar (semua invoice di DB)
```

Desktop bisa menampilkan:

- stok **saat ini** setelah transaksi terakhir, atau
- stok **sebelum** transaksi tertentu, atau
- angka dari kolom yang tidak di-update oleh ledger

**Contoh `390-119` (Sragen):**

| Sumber | Nilai | Keterangan |
|--------|-------|------------|
| Desktop (yang diharapkan user) | 23 | Setelah masuk +24 (23 Mei), sebelum keluar -3 |
| Ledger backup s/d 18 Jun | **20** | Sudah termasuk keluar -3 (14 Jun) |
| Web (dari migrasi) | 20 | Sesuai ledger |

### 3. Cabang belum di-sync

| Gejala | Penyebab |
|--------|----------|
| Ngawi/Caruban = 0 di web | `stok_cabang` belum dijalankan |
| Hanya Sragen terisi | Normal setelah `barang` saja |

### 4. Backup cabang tidak sama dengan desktop live

File backup di Drive bisa sama tanggal (mis. 18 Juni), tapi isi ledger per cabang bisa berbeda dari angka di desktop live saat dicek manual.

**Contoh `390-119` (Caruban, backup 18 Jun):**

| Transaksi | Qty |
|-----------|-----|
| 25 Mei: masuk | +24 |
| 8 Juni: keluar | -2 |
| **Net ledger** | **2** |

Jika desktop Caruban menampilkan 26, data live kemungkinan sudah berubah setelah backup, atau desktop membaca sumber kolom lain.

### 5. Kolom outlet kosong di backup

```sql
-- Di backup saat ini biasanya semua 0
SELECT outlet01, outlet02, ..., outlet20 FROM stockdetail;
SELECT nSTKopen FROM stock;
```

Migrasi **tidak bisa** memecah stok per outlet karena data outlet tidak terisi.

### 6. Barang tanpa baris `stok_akhir` di cabang

`stok_cabang` hanya insert/update baris dengan net ≠ 0. Barang tanpa row di gudang cabang = tampil **0** di web (bukan error).

---

## Query verifikasi (MySQL)

Ganti `'KODE-BARANG'` dengan kode yang ingin dicek.

### Net stok dari ledger (sama seperti migrasi)

```sql
SELECT
    TRIM(sd.cSTDcode) AS kode_barang,
    SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok
FROM invoicedetail d
JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
WHERE TRIM(sd.cSTDcode) = 'KODE-BARANG'
  AND s.nSTKsuspend = 0
GROUP BY TRIM(sd.cSTDcode);
```

### Fallback outlet (biasanya 0)

```sql
SELECT
    sd.outlet01, sd.outlet02, sd.outlet03, sd.outlet04, sd.outlet05,
    sd.outlet06, sd.outlet07, sd.outlet08, sd.outlet09, sd.outlet10,
    sd.outlet11, sd.outlet12, sd.outlet13, sd.outlet14, sd.outlet15,
    sd.outlet16, sd.outlet17, sd.outlet18, sd.outlet19, sd.outlet20
FROM stockdetail sd
WHERE TRIM(sd.cSTDcode) = 'KODE-BARANG';
```

### Fallback stok pembukaan

```sql
SELECT s.nSTKopen
FROM stock s
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
WHERE TRIM(sd.cSTDcode) = 'KODE-BARANG';
```

### Transaksi terakhir (audit selisih)

```sql
SELECT i.dINVdate, d.nIVDqtyin, d.nIVDqtyout, d.nIVDprice
FROM invoicedetail d
JOIN invoice i ON i.cINVpk = d.cIVDfkINV
JOIN stock s ON s.cSTKpk = d.cIVDfkSTK
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
WHERE TRIM(sd.cSTDcode) = 'KODE-BARANG'
ORDER BY i.dINVdate DESC
LIMIT 10;
```

Jalankan query di atas **per database cabang**:

- Sragen → `evacer_sragen`
- Ngawi → `evacer_ngawi_fullbackup`
- Caruban → `evacer_caruban_fullbackup`

---

### Net stok per golongan — bukti stok minus + transaksi

File SQL lengkap (3 query: ringkasan, transaksi, gabungan ledger):

- [`scripts/sql/stok_minus_bukti.sql`](../scripts/sql/stok_minus_bukti.sql)

Export Excel otomatis (3 sheet: Ringkasan Minus, Transaksi, Gabungan Bukti):

```bash
./run.sh export-stok-minus-bukti \
  --golongan '07/MULTIGARMENTAMA + PPN' \
  --mysql-db trx
# → output/stok_minus_07_multigarmentama_ppn_sragen.xlsx (nama otomatis dari golongan + DB)
```

**5. Detail stok minus + transaksi (gabungan bukti, MySQL 8+)**

Ganti `@golongan` lalu jalankan query #3 di `scripts/sql/stok_minus_bukti.sql`. Kolom `cocok_akhir = YA` artinya saldo berjalan baris terakhir = net stok ringkasan.

---

### Net stok per golongan

Relasi golongan: `stock.cSTKfkGRP` → `stockgroup.cGRPpk`, nama golongan = `stockgroup.cGRPdesc`.

**1. Ringkasan — total net stok per golongan**

```sql
SELECT
    TRIM(g.cGRPdesc) AS golongan,
    g.cGRPpk AS golongan_pk,
    COUNT(DISTINCT TRIM(sd.cSTDcode)) AS jumlah_kode_barang,
    SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok_total,
    SUM(COALESCE(d.nIVDqtyin, 0)) AS total_masuk,
    SUM(COALESCE(d.nIVDqtyout, 0)) AS total_keluar
FROM invoicedetail d
JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
LEFT JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
WHERE s.nSTKsuspend = 0
  AND sd.cSTDcode IS NOT NULL
  AND TRIM(sd.cSTDcode) <> ''
GROUP BY g.cGRPpk, TRIM(g.cGRPdesc)
ORDER BY net_stok_total DESC;
```

**2. Detail — net stok per kode barang dalam satu golongan**

Ganti `%390%` dengan pola nama golongan (atau pakai query #3 untuk exact match).

```sql
SELECT
    TRIM(g.cGRPdesc) AS golongan,
    TRIM(sd.cSTDcode) AS kode_barang,
    s.cSTKdesc AS nama,
    SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok,
    SUM(COALESCE(d.nIVDqtyin, 0)) AS total_masuk,
    SUM(COALESCE(d.nIVDqtyout, 0)) AS total_keluar,
    COUNT(*) AS jumlah_transaksi
FROM invoicedetail d
JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
LEFT JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
WHERE s.nSTKsuspend = 0
  AND TRIM(g.cGRPdesc) LIKE '%390%'
GROUP BY TRIM(g.cGRPdesc), TRIM(sd.cSTDcode), s.cSTKdesc
HAVING net_stok <> 0
ORDER BY kode_barang;
```

**3. Filter satu golongan exact**

```sql
SELECT
    TRIM(g.cGRPdesc) AS golongan,
    TRIM(sd.cSTDcode) AS kode_barang,
    s.cSTKdesc AS nama,
    SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok
FROM invoicedetail d
JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
LEFT JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
WHERE s.nSTKsuspend = 0
  AND TRIM(g.cGRPdesc) = '390/OHL (CHATRA)-SGLT'
GROUP BY TRIM(g.cGRPdesc), TRIM(sd.cSTDcode), s.cSTKdesc
ORDER BY net_stok DESC, kode_barang;
```

> Tip: cari nama golongan exact dulu:
>
> ```sql
> SELECT TRIM(cGRPdesc) AS golongan, COUNT(*) AS jumlah_barang
> FROM stock s
> JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
> WHERE s.nSTKsuspend = 0
>   AND TRIM(cGRPdesc) LIKE '%390%'
> GROUP BY TRIM(cGRPdesc);
> ```

**4. Satu kode barang + golongannya**

```sql
SELECT
    TRIM(g.cGRPdesc) AS golongan,
    TRIM(sd.cSTDcode) AS kode_barang,
    s.cSTKdesc AS nama,
    SUM(COALESCE(d.nIVDqtyin, 0) - COALESCE(d.nIVDqtyout, 0)) AS net_stok,
    SUM(COALESCE(d.nIVDqtyin, 0)) AS total_masuk,
    SUM(COALESCE(d.nIVDqtyout, 0)) AS total_keluar
FROM invoicedetail d
JOIN stock s ON d.cIVDfkSTK = s.cSTKpk
JOIN stockdetail sd ON sd.cSTDfkSTK = s.cSTKpk
LEFT JOIN stockgroup g ON g.cGRPpk = s.cSTKfkGRP
WHERE TRIM(sd.cSTDcode) = '390-119'
  AND s.nSTKsuspend = 0
GROUP BY TRIM(g.cGRPdesc), TRIM(sd.cSTDcode), s.cSTKdesc;
```

Contoh hasil `390-119` di `evacer_sragen`: golongan **390/OHL (CHATRA)-SGLT**, **net_stok = 20**.

---

## Query verifikasi (PostgreSQL / web)

```sql
SELECT
    b.kode_barang,
    b.nama,
    mg.name AS gudang,
    sa.stok_akhir,
    sa.stok_pajak
FROM barang b
LEFT JOIN stok_akhir sa ON sa.barang_id = b.id AND sa.deleted_at IS NULL
LEFT JOIN master_gudang mg ON mg.id = sa.gudang_id
WHERE b.kode_barang = 'KODE-BARANG'
  AND b.deleted_at IS NULL
ORDER BY mg.name;
```

---

## Perintah perbaikan / refresh

| Kebutuhan | Perintah |
|-----------|----------|
| Cek selisih source vs target | `./run.sh inspect` |
| Stok Sragen (saat migrasi barang baru) | `./run.sh run --only=barang` |
| Stok Ngawi & Caruban | `./run.sh run --only=stok_cabang` |
| Katalog cabang belum lengkap | `./run.sh run --only=barang_union` lalu `stok_cabang` |
| Stok pajak saja | `./run.sh run --only=stok_pajak` |
| Simulasi tanpa write | tambahkan `--dry-run` |

**Refresh setelah backup baru di-import:**

```bash
# 1. Pastikan .env mengarah ke DB backup terbaru
# 2. Cabang
./run.sh run --only=stok_cabang

# 3. Pajak (opsional)
./run.sh run --only=stok_pajak
```

> **Catatan:** Saat ini belum ada `--update-stok` untuk refresh Gudang Pusat tanpa migrasi barang ulang. Untuk refresh Sragen penuh, perlu re-run `barang` atau implementasi update stok terpisah.

---

## Output CSV terkait stok

| File | Isi |
|------|-----|
| `output/stok_cabang_ngawi_applied.csv` | Stok Ngawi yang di-apply |
| `output/stok_cabang_ngawi_unmapped.csv` | Kode Ngawi tidak ketemu di web |
| `output/stok_cabang_caruban_applied.csv` | Stok Caruban yang di-apply |
| `output/stok_cabang_caruban_unmapped.csv` | Kode Caruban tidak ketemu di web |

---

## Diagram alur stok

```
MySQL Desktop (per cabang)
│
├── invoicedetail.nIVDqtyin / nIVDqtyout   ← SUMUTAMA (stok fisik)
│       │
│       ├── Sragen DB  ──► stok_akhir @ Gudang Pusat   (migrate barang)
│       ├── Ngawi DB   ──► stok_akhir @ Gudang Ngawi   (stok_cabang)
│       └── Caruban DB ──► stok_akhir @ Gudang Caruban (stok_cabang)
│
├── stockdetail.outlet01–20  ← fallback (biasanya 0)
├── stock.nSTKopen           ← fallback (biasanya 0)
│
└── evacer_pajak_backup      ──► stok_akhir.stok_pajak  (terpisah, bukan fisik cabang)
```

---

## Checklist troubleshooting selisih stok

- [ ] Pastikan backup MySQL sudah di-import ulang (tanggal file = tanggal data)
- [ ] Cek `.env`: `MYSQL_TRX_DATABASE` untuk Sragen sudah benar
- [ ] Bandingkan net ledger MySQL vs `stok_akhir` PostgreSQL per gudang
- [ ] Untuk Ngawi/Caruban: sudah jalankan `stok_cabang`?
- [ ] Cek transaksi terakhir — apakah desktop menampilkan snapshot sebelum transaksi itu?
- [ ] Review CSV `stok_cabang_*_unmapped.csv` untuk kode yang tidak ketemu di web
- [ ] Jangan bandingkan `stok_pajak` dengan stok fisik cabang — itu kolom berbeda

---

## Referensi kode

| File | Fungsi |
|------|--------|
| `lib/migrators.py` | `fetch_net_stok_from_trx()`, migrasi stok Sragen |
| `lib/cabang_migrators.py` | `NET_STOK_BY_KODE_SQL`, `migrate_stok_cabang()` |
| `lib/config.py` | `resolve_stok_qty()` — fallback outlet / nSTKopen |
| `config/mapping.py` | `STOCK_SOURCES` — dokumentasi mapping kolom |

---

*Terakhir diperbarui: Juni 2026 — berdasarkan investigasi selisih stok (contoh kode `390-119`) dan struktur backup Sragen / Ngawi / Caruban tanggal 18 Juni.*
