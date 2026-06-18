# Migrasi Cabang (Ngawi + Caruban)

Katalog barang **satu** untuk semua gudang. Stok fisik **per cabang** dari DB backup masing-masing.

## Prasyarat `.env`

```env
MYSQL_TRX_DATABASE=evacer_fullbackup          # Sragen / Pusat (sudah dimigrasi)
MYSQL_TRX_DATABASE_NGAWI=evacer_ngawi_fullbackup
MYSQL_TRX_DATABASE_CARUBAN=evacer_caruban_fullbackup
```

Web harus punya 3 gudang: **Gudang Pusat**, **Gudang Ngawi**, **Gudang Caruban**.

## Urutan

```bash
# 1. Samakan katalog — insert kode dari Ngawi/Caruban yang belum ada di web
./run.sh run --only=barang_union --dry-run
./run.sh run --only=barang_union

# 2. Isi stok fisik per gudang cabang (hanya net != 0)
./run.sh run --only=stok_cabang --dry-run
./run.sh run --only=stok_cabang

# 3. Stok pajak (pusat, opsional)
./run.sh run --only=stok_pajak,barang_pajak
```

Gabungan:

```bash
./run.sh run --only=barang_union,stok_cabang --dry-run
./run.sh run --only=barang_union,stok_cabang
```

## Apa yang dilakukan

| Step | Fungsi |
|------|--------|
| `barang_union` | UNION kode Ngawi + Caruban → insert ke `barang` jika belum ada (stok awal 0) |
| `stok_cabang` | `stok_akhir` @ Gudang Ngawi / Caruban dari ledger cabang masing-masing |

**Tidak** mengubah stok Gudang Pusat (Sragen sudah dari migrasi `barang`).

## Output CSV

| File | Isi |
|------|-----|
| `output/barang_union_inserted.csv` | Barang baru dari cabang |
| `output/stok_cabang_ngawi_applied.csv` | Stok terisi Ngawi |
| `output/stok_cabang_ngawi_unmapped.csv` | Kode ledger Ngawi tidak ada di web |
| `output/stok_cabang_caruban_applied.csv` | Stok terisi Caruban |
| `output/stok_cabang_caruban_unmapped.csv` | Kode ledger Caruban tidak ada di web |

## Refresh ulang

Setelah DB cabang di-update:

```bash
./run.sh run --only=stok_cabang
```

`barang_union` aman dijalankan ulang (skip barang existing).
