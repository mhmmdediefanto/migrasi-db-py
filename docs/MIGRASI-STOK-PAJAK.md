# Migrasi Stok Pajak — Panduan Menjalankan

Panduan singkat untuk mengisi kolom `stok_pajak` di PostgreSQL (web) dari database pajak desktop yang terpisah.

Dokumentasi migrasi master umum: [`DOKUMENTASI-MIGRASI.md`](DOKUMENTASI-MIGRASI.md)

---

## Ringkasan

| Sumber MySQL | Target PostgreSQL |
|--------------|-------------------|
| `evacer` | Master barang, supplier, brand |
| `evacer_fullbackup` | `stok_akhir` (stok fisik) |
| `evacer_pajak_backup` | `stok_akhir.stok_pajak` |

Di web, satu barang punya **dua kolom stok** di tabel `stok_akhir`:

- `stok_akhir` — stok fisik / operasional
- `stok_pajak` — stok untuk keperluan pajak

---

## Prasyarat

1. `./setup.sh` sudah dijalankan
2. File `.env` sudah diisi (minimal):

```env
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=department-local
PG_USERNAME=postgres
PG_PASSWORD=...

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3307
MYSQL_MASTER_DATABASE=evacer
MYSQL_TRX_DATABASE=evacer_fullbackup
MYSQL_TRX_PAJAK_DATABASE=evacer_pajak_backup
MYSQL_USERNAME=root
MYSQL_PASSWORD=...
```

3. Database MySQL sudah di-restore:
   - `evacer`
   - `evacer_fullbackup`
   - `evacer_pajak_backup`

4. Tes koneksi:

```bash
./run.sh inspect
```

---

## Urutan migrasi (lengkap)

Jalankan **berurutan** dari atas ke bawah.

### Langkah 1 — Master + stok fisik

Migrasi supplier, golongan (brand), dan barang dari DB master. Stok fisik diisi dari `evacer_fullbackup`.

```bash
./run.sh run --only=supplier,brand,barang
```

> Skip langkah ini jika master sudah pernah dimigrasi dan tidak perlu di-refresh.

Estimasi barang full (~725k): **2–4 jam**.

---

### Langkah 2 — Stok pajak (barang yang sudah ada)

Isi `stok_pajak` untuk barang PPN yang **sudah ada** di web.

```bash
# Simulasi dulu (tidak menulis ke PostgreSQL)
./run.sh run --only=stok_pajak --dry-run

# Apply sungguhan
./run.sh run --only=stok_pajak
```

**Mapping kode** (otomatis):

| Tier | Contoh | Keterangan |
|------|--------|------------|
| 1 — exact | `312-010A` → `312-010A` | Kode sama di web |
| 2 — strip A | `323-932A` → `323-932` | Varian pajak, barang tanpa `A` sudah ada |
| 3 — skip | `22-1354A` | Tidak ketemu di web → masuk CSV unmapped |

Hanya barang **PPN** (`brands.is_ppn = true`) yang di-update. Stok fisik (`stok_akhir`) **tidak diubah**.

Estimasi: **~1–2 menit**.

---

### Langkah 3 — Barang pajak-only (opsional)

Insert barang baru dari DB pajak yang **belum ada** di web (~112 kode, mis. `22-1354A`).

```bash
# Simulasi
./run.sh run --only=barang_pajak --dry-run

# Apply sungguhan
./run.sh run --only=barang_pajak
```

Barang baru:

- `stok_akhir` = **0** (tidak ada di DB fisik)
- `stok_pajak` = net dari `evacer_pajak_backup`

Estimasi: **< 1 menit**.

---

## Satu baris (gabungan)

Setelah master sudah ada:

```bash
./run.sh run --only=stok_pajak,barang_pajak
```

Atau dari awal (master + pajak):

```bash
./run.sh run --only=supplier,brand,barang,stok_pajak,barang_pajak
```

---

## Apa itu `--dry-run`?

| Mode | Efek |
|------|------|
| **Dengan `--dry-run`** | Simulasi: hitung & laporkan saja, **tidak** INSERT/UPDATE ke PostgreSQL |
| **Tanpa `--dry-run`** | Migrasi sungguhan: data ditulis ke PostgreSQL |

Contoh output dry-run `stok_pajak`:

```
pajak_codes: 95717
updated: 92052          ← akan di-update jika dijalankan tanpa --dry-run
unmapped: 112            ← kode pajak tidak ketemu barang web
skipped_non_ppn: 3553   ← bukan barang PPN, di-skip
```

---

## File laporan (folder `output/`)

Setelah menjalankan migrasi pajak:

| File | Isi |
|------|-----|
| `output/kode_pajak_tidak_ada_di_master.csv` | **209** kode: ada di DB pajak, tidak di master `evacer` (kolom `aksi`) |
| `output/kode_pajak_mapped_ke_web.csv` | Sudah ketemu di web (mapping `A` → tanpa `A`) → langkah `stok_pajak` |
| `output/kode_pajak_perlu_insert_baru.csv` | **112** kode: perlu barang baru → langkah `barang_pajak` |
| `output/kode_pajak_tanpa_transaksi.csv` | **24** kode: ada di DB pajak, tidak di master, tidak ada transaksi ledger |
| `output/stok_pajak_applied.csv` | Kode pajak → kode web, tier mapping, qty |
| `output/stok_pajak_unmapped.csv` | Sama isinya dengan `kode_pajak_perlu_insert_baru` (saat stok_pajak) |
| `output/barang_pajak_inserted.csv` | Barang baru hasil `barang_pajak` |
| `output/kode_barang_hanya_di_pajak.csv` | Alias lama dari `kode_pajak_tidak_ada_di_master` (kompatibilitas) |

Generate laporan CSV (tanpa migrasi):

```bash
./run.sh run --only=pajak_report
```

Otomatis juga dijalankan setelah `./run.sh run --only=stok_pajak`.

### Memahami angka 209 vs 112

```
209  kode di DB pajak, tidak ada di master evacer
 ├── 73   sudah ada di web (mapping)     → kode_pajak_mapped_ke_web.csv
 ├── 112  perlu barang baru              → kode_pajak_perlu_insert_baru.csv
 └── 24   belum di web, tanpa transaksi  → kode_pajak_tanpa_transaksi.csv
```

File gabungan `kode_pajak_tidak_ada_di_master.csv` punya kolom **`aksi`** yang menjelaskan tiap baris.

---

## Verifikasi setelah migrasi

### 1. Cek di PostgreSQL

```sql
-- Berapa baris sudah punya stok_pajak != 0
SELECT COUNT(*) FROM stok_akhir WHERE deleted_at IS NULL AND stok_pajak <> 0;

-- Sample barang PPN
SELECT b.kode_barang, b.nama, sa.stok_akhir, sa.stok_pajak
FROM barang b
JOIN stok_akhir sa ON sa.barang_id = b.id AND sa.deleted_at IS NULL
JOIN brands br ON br.id = b.brand_id
WHERE br.is_ppn = true AND sa.stok_pajak <> 0
LIMIT 20;
```

### 2. Cek di web

Buka menu Barang, cari kode contoh:

| Kode | Harapan |
|------|---------|
| `323-932` | Ada, `stok_pajak` terisi (dari `323-932A`) |
| `22-1354A` | Ada setelah langkah 3, `stok_akhir=0`, `stok_pajak>0` |

### 3. Inspect ulang

```bash
./run.sh inspect
```

---

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| `Unknown database 'evacer_pajak_...'` | Pastikan DB pajak sudah di-restore; cek `MYSQL_TRX_PAJAK_DATABASE` di `.env` |
| `updated: 0` | Jalankan langkah 1 dulu (`barang`); pastikan barang sudah ada di PostgreSQL |
| Banyak `unmapped` | Normal untuk kode pajak-only; jalankan langkah 3 `barang_pajak` |
| `stok_pajak` masih 0 di web | Pastikan tidak pakai `--dry-run` saat apply |
| Koneksi PostgreSQL gagal | Cek `PG_*` di `.env`; pastikan service PostgreSQL jalan |

---

## Checklist cepat

```
[ ] .env: MYSQL_TRX_PAJAK_DATABASE=evacer_pajak_backup
[ ] ./run.sh inspect sukses
[ ] ./run.sh run --only=supplier,brand,barang  (jika belum)
[ ] ./run.sh run --only=stok_pajak --dry-run   → cek angka
[ ] ./run.sh run --only=stok_pajak             → apply
[ ] ./run.sh run --only=barang_pajak --dry-run → cek (opsional)
[ ] ./run.sh run --only=barang_pajak           → apply (opsional)
[ ] Review output/stok_pajak_unmapped.csv
[ ] Spot-check di web / SQL
```
