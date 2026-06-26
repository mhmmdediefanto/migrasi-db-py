# Koreksi Stok Gudang Pusat (Sragen)

Panduan lengkap untuk merekonsiliasi stok 2.248 barang selisih antara laporan Excel desktop (18/06/2026) dan stok sistem web (PostgreSQL production).

---

## Latar Belakang

Setelah migrasi, ditemukan **2.248 barang selisih** antara:
- Excel laporan Saldo Stock Sragen 18/06/2026 (`stok_excel`)
- `stok_akhir.stok_akhir` @ Gudang Pusat (id=1) dan `barang.stok_akhir` di web

Cek sebelumnya ([`output/stok_selisih_sragen_180626.xlsx`](../output/stok_selisih_sragen_180626.xlsx)) menunjukkan:
- ~39.016 barang OK (94.2%)
- 2.389 selisih total → difilter menjadi 2.248 aktif di sistem

---

## Strategi Rekonsiliasi (Strategi B)

Stok target **bukan** snap mentah ke Excel, melainkan:

```
stok_target = stok_excel
            + net_pembelian_pusat (p.tanggal >= 18/06/2026, gudang_id=1)
            - net_penjualan       (p.tanggal >= 18/06/2026, semua gudang)
            + retur_pembelian_pusat - retur_penjualan
```

Artinya: baseline adalah Excel 18/6, lalu disesuaikan dengan transaksi web yang sudah berjalan sejak laporan itu.

**Tabel yang diubah (urutan wajib):**

| Urutan | Tabel | Kolom yang diubah |
|--------|-------|-------------------|
| 1 | `stok` | INSERT baris koreksi (jejak audit) |
| 2 | `stok_akhir` | UPDATE stok_akhir; INSERT jika belum ada |
| 3 | `barang` | UPDATE stok_akhir saja |

**Tidak diubah:** `pembelian`, `penjualan`, stok Ngawi/Caruban, `stok_pajak`, `barang.stok_awal`.

---

## Alur Keseluruhan

```
production (tunnel) ─── pg_dump ──► backup/
                                       │
                                   pg_restore
                                       │
                                  clone lokal (matahari_prod_clone)
                                       │
                          fix-stok-pusat --dry-run   ← preview CSV
                                       │
                          fix-stok-pusat --apply     ← write ke clone
                                       │
                          fix-stok-pusat --verify-only
                                       │
                           mismatch = 0? ──► production apply
```

---

## Fase 1 — Clone Production ke Lokal

### Prasyarat

- Tunnel aktif: `~/bin/matahari-production-tunnel.sh`
- `pg_dump` dan `pg_restore` tersedia: `which pg_dump pg_restore`
- PostgreSQL lokal jalan: `pg_isready`

### Jalankan script clone

```bash
# Pastikan tunnel aktif
./scripts/check_tunnel.sh

# Set password prod (atau isi langsung di prompt)
export PG_PROD_PASS='...'

# Clone (nama DB lokal default: matahari_prod_clone)
bash scripts/clone_prod_to_local.sh matahari_prod_clone
```

Script akan:
1. Cek tunnel
2. Dump production → `backup/matahari-production_YYYYMMDD_HHMMSS.dump`
3. Buat DB lokal `matahari_prod_clone` (atau drop+rebuild jika sudah ada)
4. Restore dump

### Update `.env` untuk clone lokal

```env
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DATABASE=matahari_prod_clone
PG_USERNAME=<user_lokal_anda>
PG_PASSWORD=
# PG_SSLMODE=        ← kosongkan atau hapus baris ini
```

---

## Fase 2 — Dry-run di Clone

```bash
./run.sh fix-stok-pusat --dry-run \
  --from-selisih output/stok_selisih_sragen_180626.xlsx \
  --since-date 2026-06-18 \
  --gudang pusat
```

Output:
- Print stats di terminal (berapa UPDATE, INSERT, skip)
- Export `output/stok_koreksi_preview.csv` — wajib direview

### Review CSV preview

Kolom di `stok_koreksi_preview.csv`:

| Kolom | Keterangan |
|-------|-----------|
| `kode_barang` | Kode barang |
| `nama_barang` | Nama barang |
| `stok_excel` | Stok dari laporan 18/6 |
| `net_beli` | Net pembelian Pusat ≥ 18/6 |
| `net_jual` | Net penjualan ≥ 18/6 |
| `net_retur_beli` | Retur pembelian Pusat |
| `net_retur_jual` | Retur penjualan |
| `stok_target` | Stok setelah rekonsiliasi (ini yang akan diset) |
| `stok_sebelum` | Stok sistem saat ini |
| `delta` | stok_target - stok_sebelum |
| `aksi` | `update`, `insert`, `skip` |

Cek baris yang kritis: barang dengan transaksi (mis. `508-806`, `508-807`).

---

## Fase 3 — Apply di Clone

Setelah preview OK:

```bash
./run.sh fix-stok-pusat --apply \
  --from-selisih output/stok_selisih_sragen_180626.xlsx \
  --since-date 2026-06-18 \
  --gudang pusat
```

Proses: batch 200 barang, commit per batch, progress bar di terminal.

---

## Fase 4 — Verifikasi Clone

```bash
./run.sh fix-stok-pusat --verify-only \
  --from-selisih output/stok_selisih_sragen_180626.xlsx \
  --gudang pusat
```

**Gate lulus:** `mismatch_stok_akhir = 0` dan `mismatch_barang = 0`.

Opsional — bandingkan lagi vs Excel mentah (akan ada sisa selisih untuk barang yang sudah jual 19–22 Jun, itu expected):

```bash
./run.sh check-stok \
  --from-file "/path/ke/SaldostockSragen per 180626.xlsx" \
  --gudang pusat
```

---

## Fase 5 — Production (setelah clone OK)

### 5.1 Backup production sebelum write

```bash
export PG_PROD_PASS='...'
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
PGPASSWORD="${PG_PROD_PASS}" pg_dump \
  "host=127.0.0.1 port=55432 dbname=matahari-production user=doadmin sslmode=require" \
  --format=custom --no-owner --no-acl \
  -f "backup/pre-koreksi-stok_${TIMESTAMP}.dump"
```

### 5.2 Update `.env` ke production

```env
PG_HOST=127.0.0.1
PG_PORT=55432
PG_DATABASE=matahari-production
PG_USERNAME=doadmin
PG_PASSWORD=...
PG_SSLMODE=require
```

### 5.3 Dry-run production

```bash
./run.sh fix-stok-pusat --dry-run --allow-production \
  --from-selisih output/stok_selisih_sragen_180626.xlsx
```

### 5.4 Apply production (maintenance window)

```bash
./run.sh fix-stok-pusat --apply --allow-production \
  --from-selisih output/stok_selisih_sragen_180626.xlsx

./run.sh fix-stok-pusat --verify-only --allow-production \
  --from-selisih output/stok_selisih_sragen_180626.xlsx
```

Simpan output: `output/stok_koreksi_preview_prod_YYYYMMDD.csv` sebagai bukti audit.

---

## Fase 6 — Sinkron `stok_opname_detail.stok_sistem_snapshot`

Setelah koreksi stok, sesi opname `scanning` masih menyimpan snapshot sistem lama di `stok_sistem_snapshot`. Perintah ini memperbarui snapshot ke stok gudang saat ini (match `kode_barang_snapshot` dari file selisih koreksi).

**Tidak mengubah:** stok real (`stok_akhir`, `barang`), `stok_fisik`, `selisih`, `snapshot_at`.

```bash
# Clone lokal
./run.sh fix-opname-snapshot --dry-run \
  --from-selisih output/stok_selisih_sragen_180626.xlsx

./run.sh fix-opname-snapshot --apply \
  --from-selisih output/stok_selisih_sragen_180626.xlsx

./run.sh fix-opname-snapshot --verify-only \
  --from-selisih output/stok_selisih_sragen_180626.xlsx

# Production
./run.sh fix-opname-snapshot --apply --allow-production \
  --from-selisih output/stok_selisih_sragen_180626.xlsx
```

Gate lulus: `verify-only` → **mismatch = 0**.

---

## Checklist

- [ ] Tunnel aktif (`./scripts/check_tunnel.sh`)
- [ ] Clone dump berhasil (`backup/*.dump`)
- [ ] `.env` mengarah ke clone lokal
- [ ] Dry-run OK — preview CSV direview
- [ ] Apply clone OK — 0 error
- [ ] Verify clone OK — **mismatch = 0**
- [ ] Backup production dibuat sebelum apply
- [ ] `.env` dikembalikan ke production (tunnel)
- [ ] Dry-run production OK
- [ ] Apply production OK
- [ ] Verify production OK — **mismatch = 0**

---

## Rollback

Jika ada masalah setelah apply production, restore dari dump pre-koreksi:

```bash
# HATI-HATI: ini menimpa seluruh DB
pg_restore \
  -d matahari-production \
  --no-owner --no-acl \
  backup/pre-koreksi-stok_TIMESTAMP.dump
```

Atau: identifikasi baris yang berubah dari tabel `stok` (keterangan = `'Koreksi Stok - Saldo Excel 18/06/2026 Sragen'`) dan balikkan secara manual.
