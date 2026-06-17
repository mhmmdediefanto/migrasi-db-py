# Dokumentasi Migrasi Data — Desktop (MySQL) ke Web (PostgreSQL)

> **Audience:** Project Manager, stakeholder teknis  
> **Project:** Department Store — migrasi dari aplikasi desktop ISX/evacer ke aplikasi web Laravel  
> **Toolkit:** Python (`migrate.py`) di folder `departement-store/`  
> **Terakhir diperbarui:** Juni 2026

---

## 1. Ringkasan Eksekutif

Proyek ini memindahkan **master data** dari database desktop lama (MySQL) ke database aplikasi web baru (PostgreSQL). Fokus saat ini:

| Entitas | Status |
|---------|--------|
| Supplier | Siap dimigrasi |
| Golongan (brand) | Siap dimigrasi |
| Barang (+ harga & stok) | Siap dimigrasi |
| Gudang / Store | Opsional (biasanya pakai data yang sudah ada di web) |
| Transaksi (invoice, penjualan, dll.) | **Belum** — fase berikutnya |
| Group Barang (footwear) | **Tidak ada** di database desktop — setup manual di web |

**Estimasi volume data master:**
- ~334 supplier
- ~869 golongan
- ~725.693 barang aktif
- ~77.863 barang punya stok net ≠ 0 (dari transaksi)

**Catatan penting untuk client:** Stok boleh **minus** — ini sesuai ketentuan bisnis client dan dihitung dari net mutasi transaksi di database lama.

---

## 2. Konteks Masalah

### Sistem lama (Desktop)
- Database: **MySQL** (`evacer` / `evacer_fullbackup`)
- Aplikasi desktop ISX dengan skema tabel dan nama kolom yang **sangat berbeda** dari web
- Client menyediakan **dua jenis dump**:
  1. **Master only** (`evacer`) — hanya tabel master, tanpa transaksi
  2. **Full backup** (`evacer_fullbackup`) — master + transaksi (invoice, invoicedetail, dll.)

### Sistem baru (Web)
- Database: **PostgreSQL** (`department-local`)
- Framework: Laravel
- Tabel target: `supplier`, `brands`, `barang`, `stok_akhir`, `stok`, dll.

### Tantangan utama
1. Beda DB engine (MySQL → PostgreSQL)
2. Nama tabel/kolom tidak sama (mis. `stock` → `barang`, `stockgroup` → `brands`)
3. Stok **tidak tersimpan** di kolom outlet (`stockdetail.outlet01–20` semuanya 0)
4. Stok aktual harus **dihitung ulang** dari tabel transaksi `invoicedetail`

---

## 3. Arsitektur Koneksi Database

Toolkit migrasi membaca **3 database sekaligus**:

```
┌─────────────────────┐     ┌──────────────────────────┐
│  MySQL MASTER       │     │  MySQL TRX (full backup) │
│  evacer             │     │  evacer_fullbackup       │
│                     │     │                          │
│  • entity           │     │  • invoicedetail         │
│  • stockgroup       │     │  • invoice               │
│  • stock            │     │  • (3,3 juta baris trx)  │
│  • stockdetail      │     │                          │
└─────────┬───────────┘     └────────────┬─────────────┘
          │                              │
          │    Python migrate.py         │
          └──────────────┬───────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  PostgreSQL TARGET  │
              │  department-local   │
              │                     │
              │  • supplier         │
              │  • brands           │
              │  • barang           │
              │  • stok_akhir       │
              │  • stok             │
              │  • migration_id_map │
              └─────────────────────┘
```

### Konfigurasi `.env`

| Variabel | Fungsi |
|----------|--------|
| `MYSQL_MASTER_DATABASE` | DB master (supplier, golongan, barang) |
| `MYSQL_TRX_DATABASE` | DB full backup (untuk hitung stok dari transaksi) |
| `PG_DATABASE` | DB target web (PostgreSQL) |
| `MIGRATION_DEFAULT_USER_ID` | User ID Laravel untuk `created_by` / `updated_by` |
| `MIGRATION_BATCH_SIZE` | Jumlah barang per batch (default: 500) |

---

## 4. Mapping Tabel & Kolom

### 4.1 Supplier

| MySQL (desktop) | PostgreSQL (web) | Keterangan |
|-----------------|------------------|------------|
| `entity` (filter `nENTsupp = 1`) | `supplier` | Hanya entitas yang ditandai supplier |
| `cENTpk` | `migration_id_map` | Primary key lama disimpan untuk relasi |
| `cENTcode` | `code` | |
| `cENTdesc` | `name` | |
| `cENTadd1/2/3` | `address` | Digabung |
| `nENTsuspend` | `status` | 0 = aktif |

**Volume:** ~334 baris

---

### 4.2 Golongan (Brand)

| MySQL (desktop) | PostgreSQL (web) | Keterangan |
|-----------------|------------------|------------|
| `stockgroup` | `brands` | **Bukan** tabel `brand` (kosong di desktop) |
| `cGRPpk` | `migration_id_map` | |
| `cGRPdesc` | `name` | |
| `serino` | `code` | Fallback: slug dari nama |
| `markpersen1` | `margin_percent` | |
| Nama mengandung "PPN" | `is_ppn = true` | Deteksi otomatis |

**Volume:** ~869 baris

---

### 4.3 Barang

| MySQL (desktop) | PostgreSQL (web) | Keterangan |
|-----------------|------------------|------------|
| `stock` + `stockdetail` | `barang` | JOIN untuk kode & harga |
| `cSTKpk` | `migration_id_map` | |
| `stockdetail.cSTDcode` (MIN) | `kode_barang` | Satu kode per barang |
| `cSTKdesc` | `nama` | |
| `cSTKfkGRP` | `brand_id` | Lookup via `migration_id_map` |
| `cSTKfkENT` | `supplier_id` | Lookup via `migration_id_map` |
| `nSTKbuy` → `nSTDprice` | `harga_beli` | Prioritas: `nSTKbuy` dulu |
| `nHrgQty01` → `nSTDretail` → `nSTDprice` | `harga_jual` | 3 level fallback |
| `nSTKcogs` → `nSTKbuy` | `hpp` | |
| `nstkukuran` / `cSTKsize` | `ukuran` | |
| `cSTKcolor` | `warna` | |
| `nstkkonsi` | `is_consignment`, `type` | |
| `nstktdiscp` | `consignment_percentage` | |

**Volume:** ~725.693 barang aktif (`nSTKsuspend = 0`)

---

### 4.4 Stok

Stok **tidak** diambil dari kolom outlet (`stockdetail.outlet01–20`) karena di dump production semua nilainya **0**.

**Sumber stok aktual:** tabel `invoicedetail` di database full backup.

```
Stok net per barang = SUM(nIVDqtyin - nIVDqtyout)
                      GROUP BY cIVDfkSTK
```

| Prioritas | Sumber | Kapan dipakai |
|-----------|--------|---------------|
| 1 (utama) | `invoicedetail` (DB trx) | Full backup tersedia |
| 2 | `stockdetail.outlet01–20` | Jika suatu saat terisi |
| 3 | `stock.nSTKopen` | Stok pembukaan |

**Distribusi stok (hasil analisis full backup):**

| Kategori | Jumlah barang |
|----------|---------------|
| Stok net > 0 | ~39.847 |
| Stok net < 0 | ~38.016 |
| Stok net = 0 | ~515.971 |
| **Total ≠ 0** | **~77.863** |

**Kebijakan bisnis:** Stok **boleh minus** — sesuai ketentuan client. Nilai minus ikut dimigrasikan ke:
- `barang.stok_awal` / `barang.stok_akhir`
- `stok_akhir` (per gudang default)
- `stok` (jejak ledger: "Stok Awal - Migrasi Desktop")

#### Catatan penting: stok per gudang / per store (3 cabang)

Di aplikasi web, stok dibaca per **gudang** (`stok_akhir` berdasarkan `gudang_id`). Saat ini web memiliki 3 store, masing-masing terhubung ke gudang berbeda:
- Store Sragen → Gudang Pusat
- Store Caruban → Gudang Caruban
- Store Ngawi → Gudang Ngawi

Namun, backup desktop yang sedang dipakai berasal dari **Sragen saja** (server lokal Sragen). Bukti di data source:
- Tabel `warehouse` hanya 1 (SRAGEN)
- Tabel `outlet` hanya 1 ("N/A")
- `invoice.cINVfkWHS` hampir semuanya menunjuk SRAGEN
- `stockdetail.outlet01–20` semuanya 0 (tidak ada stok per outlet di dump ini)

**Implikasi:** stok yang dimigrasikan saat ini adalah stok global dari DB Sragen dan **belum bisa dipisah per gudang/store** Caruban/Ngawi tanpa backup database masing-masing cabang.

**Action yang dibutuhkan dari client agar stok per gudang benar:**
- Minta dump MySQL dari **Caruban** dan **Ngawi** (server lokal masing-masing), atau
- Berikan sumber data stok per outlet (jika ada) yang memetakan ke 3 gudang di web.

---

### 4.5 Yang Belum Ada Padanan

| Fitur Web | Status di Desktop |
|-----------|-------------------|
| Group Barang (footwear) | Tidak ada tabel padanan — **setup manual** |
| Master Gudang | Ada (`warehouse`, 1 row) — opsional migrasi |
| Master Store | Ada (`outlet`, sering "N/A") — opsional, pakai store web |
| Transaksi penjualan | Ada di full backup — **belum dimigrasi** (fase 2) |

---

## 5. Urutan Migrasi

Migrasi **wajib berurutan** karena ada foreign key:

```
1. supplier
2. brand (golongan)
3. barang  ← butuh supplier_id & brand_id dari langkah 1–2
```

Opsional (biasanya dilewati):
```
gudang → store
```

### Tabel penghubung: `migration_id_map`

Menyimpan mapping `legacy_pk` (MySQL) → `new_id` (PostgreSQL) per entitas. Dipakai untuk:
- Relasi barang → supplier / golongan
- Resume migrasi jika terputus
- Audit trail

---

## 6. Cara Menjalankan

### Prasyarat
- MySQL running (port 3307) dengan DB `evacer` dan `evacer_fullbackup`
- PostgreSQL running (port 5432) dengan DB `department-local`
- File `.env` sudah dikonfigurasi

### Perintah

```bash
# Lihat mapping tabel
./run.sh mapping

# Bandingkan jumlah data source vs target (+ cek stok transaksi)
./run.sh inspect

# Simulasi tanpa insert
./run.sh run --dry-run --only=supplier,brand,barang

# Migrasi penuh (hapus data lama dulu)
./run.sh run --fresh --only=supplier,brand,barang

# Migrasi partial (untuk testing)
./run.sh run --fresh --only=barang --limit=1000
```

### Flag penting

| Flag | Fungsi |
|------|--------|
| `--fresh` | Hapus data master PostgreSQL + ID map sebelum migrasi |
| `--dry-run` | Simulasi, tidak ada insert |
| `--only=...` | Pilih entitas (default: `supplier,brand,barang`) |
| `--limit=N` | Batas jumlah barang (0 = semua) |
| `--offset=N` | Mulai dari baris ke-N |

### Estimasi waktu
- Supplier + golongan: **< 1 menit**
- Barang full (~725k): **~2–4 jam** (tergantung spesifikasi mesin & batch size)

---

## 7. Status Saat Ini

| Item | Status |
|------|--------|
| Mapping supplier | ✅ Selesai & tervalidasi |
| Mapping golongan | ✅ Selesai & tervalidasi |
| Mapping barang (nama, harga, relasi) | ✅ Selesai & tervalidasi |
| Mapping stok (dari invoicedetail) | ✅ Logic selesai, belum full run |
| Support stok minus | ✅ Sesuai ketentuan client |
| Dual MySQL (master + trx) | ✅ |
| Migrasi transaksi (invoice, dll.) | ❌ Belum — fase berikutnya |
| Group Barang | ❌ Manual di web |

**Data di PostgreSQL (per inspect terakhir):**
- Supplier: 334 / 334 ✅
- Golongan: 869 / 869 ✅
- Barang: ~30.500 / 725.693 ⚠️ (partial, perlu `--fresh` full run)

---

## 8. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|--------|--------|----------|
| Migrasi barang lama (~2–4 jam) | Proses bisa terputus | Batch processing + `migration_id_map` untuk resume |
| Stok minus (~38k barang) | Tampilan stok negatif di web | **Diterima client** — bukan bug migrasi |
| Lock database saat TRUNCATE | Proses hang | Tutup DBeaver/pgAdmin sebelum `--fresh` |
| Group Barang tidak ada di desktop | Data footwear kosong | Setup manual di web setelah migrasi |
| Harga jual tidak 100% terisi di source | Beberapa barang tanpa harga jual | Fallback 3 level (`nHrgQty01` → `nSTDretail` → `nSTDprice`) |

---

## 9. Rencana Fase Berikutnya

### Fase 1 — Master Data (saat ini)
- [x] Supplier, golongan, barang
- [x] Stok awal dari net transaksi
- [ ] Full run migrasi (~725k barang)
- [ ] Validasi sample dengan client

### Fase 2 — Transaksi (belum dimulai)
- Migrasi invoice / penjualan
- Migrasi pembelian / penerimaan barang
- Sinkronisasi stok mutasi lanjutan

### Fase 3 — Setup Manual
- Group Barang (footwear) di web
- Review gudang / store jika perlu penyesuaian

---

## 10. Struktur Folder Toolkit

```
departement-store/
├── docs/
│   └── DOKUMENTASI-MIGRASI.md    ← file ini
├── .env                          ← konfigurasi koneksi DB
├── migrate.py                    ← CLI utama
├── run.sh                        ← shortcut jalankan
├── config/
│   └── mapping.py                ← definisi mapping kolom
└── lib/
    ├── config.py                 ← load env, helper harga/stok
    ├── db.py                     ← koneksi, ID map, truncate
    ├── migrators.py              ← logic migrasi per entitas
    └── progress.py               ← progress bar & spinner
```

---

## 11. Glosarium

| Istilah Desktop | Istilah Web | Penjelasan |
|-----------------|-------------|------------|
| entity (supp) | supplier | Data pemasok |
| stockgroup | brands / golongan | Kategori barang |
| stock | barang | Master produk |
| stockdetail | (bagian dari barang) | Detail kode & harga per SKU |
| invoicedetail | (belum dimigrasi) | Detail transaksi — sumber stok |
| cSTKpk / cENTpk / cGRPpk | migration_id_map.legacy_pk | Primary key lama |
| nIVDqtyin / nIVDqtyout | stok masuk / keluar | Mutasi quantity transaksi |

---

## Kontak Teknis

Untuk pertanyaan teknis migrasi, merujuk ke developer yang menangani toolkit Python di folder `departement-store/`.
