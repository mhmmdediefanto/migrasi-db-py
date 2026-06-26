# Prompt: Alur Check Penjualan Post-Snapshot Opname

Salin blok **PROMPT** di bawah ke Cursor / agent saat mau implement fitur ini.

---

## Konteks bisnis (jangan di-skip)

1. **Stok opname** Sragen memakai cutoff saldo Excel **18/06/2026** sebagai baseline awal.
2. Saat scanning, `stok_opname_detail.stok_sistem_snapshot` merekam **stok sistem saat discan** (bisa = Excel 18/6 atau sudah sedikit berubah).
3. **Setelah barang discan, barang masih bisa dijual** — trx penjualan setelah scan **belum** masuk perbandingan opname.
4. **Jangan** auto-update `stok_sistem_snapshot` ke stok live / koreksi — snapshot opname adalah titik acuan compare.
5. Untuk menyelesaikan selisih opname, perlu **alur check penjualan**: dari **tanggal snapshot/scan** sampai **tanggal compare**, per barang.

---

## PROMPT (copy-paste)

```
Implementasi fitur "check penjualan post-snapshot opname" di repo migrasi-db-py (PostgreSQL Matahari).

## Tujuan

Untuk barang yang masih selisih di stok opname (atau daftar kode dari file), hitung dan audit transaksi penjualan (dan retur jual jika ada) **sejak tanggal snapshot/scan** sampai tanggal compare, lalu bandingkan dengan snapshot opname — BUKAN memakai cutoff Excel 18/6 global untuk trx window ini.

## Prinsip

- Snapshot opname = titik awal window trx untuk barang itu.
- Penjualan setelah scan menjelaskan selisih: `stok_sistem_sekarang ≈ stok_sistem_snapshot − net_jual + retur_jual` (± pembelian masuk Sragen jika relevan).
- Read-only default; tidak UPDATE stok atau snapshot tanpa flag eksplisit.
- Ikuti pola existing: `lib/stok_trx_check.py`, `lib/stok_trx_detail_export.py`, CLI di `migrate.py` via `./run.sh`.

## Sumber data DB (production / clone)

### Opname
- `stok_opname`: `id`, `no_opname`, `gudang_id`, `status`, `snapshot_at`, `compared_at`, `tanggal_mulai`, `tanggal_selesai`
- `stok_opname_detail`: `id`, `stok_opname_id`, `barang_id`, `kode_barang_snapshot`, `nama_barang_snapshot`, `stok_sistem_snapshot`, `stok_fisik`, `selisih`, `last_scanned_at`, `status_selisih`
- Filter sesi: `stok_opname.status = 'scanning'`, `gudang_id = 1` (Sragen), `deleted_at IS NULL`

### Tanggal snapshot per baris (prioritas)
1. `stok_opname_detail.last_scanned_at` (tanggal scan baris itu) — **utama**
2. Fallback: `stok_opname.snapshot_at`
3. Fallback: `stok_opname.tanggal_mulai`
- Window trx: `tanggal >= tanggal_snapshot` AND `tanggal <= tanggal_compare`
- Default `tanggal_compare`: `stok_opname.compared_at` atau `NOW()` / argumen CLI `--until`

### Penjualan
- `penjualan` + `penjualan_detail` (filter `deleted_at IS NULL`)
- `penjualan_return` + `penjualan_return_detail` (`status = 1`, parent `deleted_at IS NULL`)
- Agregat per `kode_barang`: qty jual, qty retur jual, list faktur (no_faktur, tanggal, qty)

### Stok sistem sekarang (opsional banding)
- `stok_akhir` gudang Sragen + `barang.stok_akhir` fallback

## Rumus compare (per baris opname detail)

```
tanggal_snapshot = last_scanned_at atau snapshot_at (date)
net_jual_window    = SUM penjualan_detail.jumlah WHERE tanggal in [snapshot, until]
net_retur_j_window = SUM penjualan_return_detail.qty_return (sama window)
trx_delta_window   = -net_jual_window + net_retur_j_window

stok_diharapkan    = stok_sistem_snapshot + trx_delta_window
                     (+ net_beli_pusat window jika mau lengkap)
selisih_explained  = stok_sistem_sekarang - stok_diharapkan
opname_selisih     = stok_fisik - stok_sistem_snapshot  (selisih saat scan)
sisa_setelah_jual  = stok_fisik - stok_sistem_sekarang  (atau fisik vs diharapkan)
```

Kategori output:
- `COCOK` — sistem sekarang = snapshot ± trx window
- `JUAL_SETELAH_SCAN` — selisih opname bisa dijelaskan penjualan setelah scan
- `TIDAK_COCOK` — masih ada gap setelah trx window
- `BELUM_DISCAN` — tidak ada last_scanned_at

## CLI (usulan)

Subcommand: `check-opname-trx` atau `check-stok-trx-post-snapshot`

```
./run.sh check-opname-trx --dry-run \
  --gudang pusat \
  --status scanning \
  [--no-opname OPN-20260619-0001] \
  [--from-selisih output/...xlsx]   # optional: hanya kode tertentu
  [--only-selisih]                  # hanya baris opname dengan selisih != 0
  [--until 2026-06-22]              # tanggal compare (default: compared_at atau hari ini)
  [--out output/opname_post_snapshot_trx.xlsx]
```

## Output Excel (minimal 3 sheet)

1. **Ringkasan** — jumlah per kategori, per no_opname
2. **Per barang** — kode, no_opname, tanggal_snapshot, stok_sistem_snapshot, stok_fisik, selisih_opname, net_jual_window, net_retur_j, stok_diharapkan, stok_sistem_sekarang, gap, kategori, daftar faktur
3. **Detail faktur** — satu baris per penjualan_detail dalam window (kode, no_faktur, tanggal, qty)

## Edge cases

- Satu kode di beberapa sesi opname → tanggal snapshot beda per baris; hitung trx window **per baris detail**, bukan per kode global.
- Penjualan bisa kurangi stok gudang lain (Ngawi); opsi flag `--ledger-sragen-only` untuk bandingkan juga `stok` keluar Sragen dalam window.
- Barang dengan stok minus setelah koreksi global — tetap tampilkan; jangan clamp.
- Retur jual masuk window mengurangi efek jual.

## Testing

1. Dry-run di `matahari_prod_clone` untuk 1 no_opname (mis. OPN-20260619-0001).
2. Validasi manual 3 kode: snapshot + faktur setelah scan = stok sekarang.
3. Production: read-only sampai user review Excel.

## File referensi di repo

- `lib/stok_trx_check.py` — query penjualan since date
- `lib/stok_trx_detail_export.py` — export Excel format
- `lib/stok_opname_fix.py` — join opname detail (JANGAN dipakai untuk update snapshot di fitur ini)
- `docs/KOREKSI-STOK-PUSAT.md` — konteks koreksi Excel 18/6 vs opname

## Deliverables

- `lib/stok_opname_trx_check.py` (atau nama serupa)
- Subcommand di `migrate.py`
- Contoh run di README atau docs singkat
- Tidak commit .env / credential
```

---

## Contoh interpretasi (untuk reviewer)

| Snapshot scan | Fisik | Jual setelah scan | Sistem sekarang | Status |
|---------------|-------|-------------------|-----------------|--------|
| 10 | 9 | 1 | 9 | Cocok — jual setelah scan |
| 10 | 9 | 0 | 10 | Belum ada jual; selisih fisik = 1 |
| 4 | 3 | 2 | 2 | Cocok rumus snapshot−jual |

---

## Catatan: beda dengan check trx cutoff 18/6

| Fitur | Window trx | Baseline |
|-------|------------|----------|
| `check-stok-trx` / koreksi stok | ≥ **18/06/2026** (global) | Excel cutoff 18/6 |
| **check post-snapshot** (ini) | ≥ **tanggal scan per baris** | `stok_sistem_snapshot` opname |

Keduanya dibutuhkan: koreksi ledger pakai Excel 18/6; penyelesaian selisih opname pakai snapshot + jual setelah scan.
