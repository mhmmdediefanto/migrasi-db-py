# Urutan Migrasi — Cheat Sheet

Panduan singkat urutan perintah dari kondisi **master Sragen sudah dimigrasi** (`supplier`, `brand`, `barang` + stok fisik Gudang Pusat), tetapi **belum** cabang (Ngawi/Caruban) dan **belum** pajak.

Detail per area:

- Master & mapping bisnis: [`DOKUMENTASI-MIGRASI.md`](DOKUMENTASI-MIGRASI.md)
- Cabang: [`MIGRASI-CABANG.md`](MIGRASI-CABANG.md)
- Pajak: [`MIGRASI-STOK-PAJAK.md`](MIGRASI-STOK-PAJAK.md)

---

## Kondisi awal (asumsi)

| Sudah | Belum |
|-------|-------|
| `supplier`, `brand`, `barang` dari `evacer` | Katalog union cabang |
| Stok fisik **Gudang Pusat** dari `evacer_fullbackup` | Stok **Gudang Ngawi** & **Caruban** |
| | `stok_pajak` & barang pajak-only |

---

## Prasyarat `.env`

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
MYSQL_TRX_DATABASE_NGAWI=evacer_ngawi_fullbackup
MYSQL_TRX_DATABASE_CARUBAN=evacer_caruban_fullbackup
MYSQL_TRX_PAJAK_DATABASE=evacer_pajak_backup
MYSQL_USERNAME=root
MYSQL_PASSWORD=...
```

Web harus punya 3 gudang: **Gudang Pusat**, **Gudang Ngawi**, **Gudang Caruban**.

Tes koneksi:

```bash
./run.sh inspect
```

---

## Urutan lengkap

### Langkah 0 — Master Sragen (skip jika sudah selesai)

```bash
# Hanya jika belum pernah migrasi master / perlu refresh dari nol
./run.sh run --only=supplier,brand,barang
```

Estimasi full barang (~725k): **2–4 jam**.

---

### Langkah 1 — Katalog cabang (`barang_union`)

Union kode Ngawi + Caruban → insert ke `barang` jika belum ada di web.

```bash
./run.sh run --only=barang_union --dry-run
./run.sh run --only=barang_union
```

| | |
|--|--|
| Target | Tabel `barang` (katalog global) |
| Estimasi dry-run | ~3 menit |
| Estimasi apply | ~15–30 menit (~53 rb barang) |
| Output | `output/barang_union_inserted.csv` |

---

### Langkah 2 — Stok fisik cabang (`stok_cabang`)

**Wajib setelah** `barang_union` — isi `stok_akhir` per gudang Ngawi & Caruban (hanya net ≠ 0).

```bash
./run.sh run --only=stok_cabang --dry-run
./run.sh run --only=stok_cabang
```

| | |
|--|--|
| Target | `stok_akhir` @ Gudang Ngawi & Caruban |
| Tidak mengubah | Stok Gudang Pusat |
| Estimasi | ~2–5 menit |
| Output | `output/stok_cabang_ngawi_*.csv`, `output/stok_cabang_caruban_*.csv` |

Gabungan langkah 1 + 2:

```bash
./run.sh run --only=barang_union,stok_cabang --dry-run
./run.sh run --only=barang_union,stok_cabang
```

---

### Langkah 3 — Stok pajak barang existing (`stok_pajak`)

Update kolom `stok_pajak` di `stok_akhir` untuk barang PPN yang sudah ada di web.

```bash
./run.sh run --only=stok_pajak --dry-run
./run.sh run --only=stok_pajak
```

| | |
|--|--|
| Sumber | `evacer_pajak_backup` |
| Tidak mengubah | `stok_akhir` (stok fisik) |
| Estimasi | ~1–2 menit |

---

### Langkah 4 — Barang pajak-only (`barang_pajak`)

Insert ~112 barang yang hanya ada di DB pajak.

```bash
./run.sh run --only=barang_pajak --dry-run
./run.sh run --only=barang_pajak
```

| | |
|--|--|
| Barang baru | `stok_akhir` fisik = 0, `stok_pajak` dari ledger pajak |
| Estimasi | < 1 menit |
| Output | `output/barang_pajak_inserted.csv` |

Gabungan langkah 3 + 4:

```bash
./run.sh run --only=stok_pajak,barang_pajak --dry-run
./run.sh run --only=stok_pajak,barang_pajak
```

---

### Langkah 5 — Laporan & verifikasi (opsional)

```bash
./run.sh run --only=pajak_report
./run.sh inspect
```

CSV pajak: `output/kode_pajak_*.csv`

---

## Satu blok (copy-paste)

Dari kondisi master Sragen sudah ada:

```bash
./run.sh inspect

./run.sh run --only=barang_union,stok_cabang --dry-run
./run.sh run --only=barang_union,stok_cabang

./run.sh run --only=stok_pajak,barang_pajak --dry-run
./run.sh run --only=stok_pajak,barang_pajak

./run.sh run --only=pajak_report
./run.sh inspect
```

---

## Ringkasan step vs tabel

| Step | Tabel / kolom | Gudang |
|------|----------------|--------|
| `barang` (sudah) | `barang`, `stok_akhir` fisik | Pusat |
| `barang_union` | `barang` | — |
| `stok_cabang` | `stok_akhir` fisik | Ngawi, Caruban |
| `stok_pajak` | `stok_akhir.stok_pajak` | Baris existing (umumnya Pusat) |
| `barang_pajak` | `barang` + `stok_pajak` | Barang baru |

---

## Refresh ulang (data backup di-update)

| Kebutuhan | Perintah |
|-----------|----------|
| Stok cabang saja | `./run.sh run --only=stok_cabang` |
| Stok pajak saja | `./run.sh run --only=stok_pajak` |
| Katalog cabang baru | `./run.sh run --only=barang_union` lalu `stok_cabang` |

`barang_union` aman dijalankan ulang (skip barang yang sudah ada).

---

## Setelah migrasi

1. UAT di web — cek stok per gudang (Pusat, Ngawi, Caruban) dan stok pajak.
2. Review edge case di `output/*_unmapped.csv`.
3. Jika DB ini lokal, ulangi urutan yang sama di environment staging/production dengan `.env` yang sesuai.
