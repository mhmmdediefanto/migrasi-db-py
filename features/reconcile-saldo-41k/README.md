# Reconcile Saldo Stock 41k

Fitur khusus rekonsiliasi stok berdasarkan laporan Saldo Stock desktop (~41k barang).

## Langkah 1 — Cek transaksi

```bash
bash features/reconcile-saldo-41k/run.sh \
  --pg-database matahari_final \
  --from-file "/path/ke/SaldostockSragen per 180626.xlsx" \
  --out features/reconcile-saldo-41k/output/trx_flags_41k_matahari_final.xlsx
```

Opsi `--since-date 2026-06-18` untuk filter transaksi setelah tanggal laporan.

## Langkah 2 — Transaksi LUAR 41k

Barang yang **tidak ada** di Saldo Stock Excel, tapi punya transaksi web:

```bash
bash features/reconcile-saldo-41k/run_luar.sh \
  --pg-database matahari_final \
  --out features/reconcile-saldo-41k/output/trx_luar_41k_matahari_final.xlsx
```

## Langkah 3 — Stok hitung (baseline Excel/0 ± transaksi)

Rumus:
- **Dalam 41k:** `stok_hitung = stok_excel + transaksi web >= 18/06/2026`
- **Luar 41k:** `stok_hitung = 0 + semua transaksi web`
- Stok sistem **hanya untuk selisih**, bukan sumber hitung

```bash
bash features/reconcile-saldo-41k/run_hitung.sh --pg-database matahari_final
```

Output: `output/stok_hitung_41k_matahari_final.xlsx`


| Sheet | Isi |
|-------|-----|
| Ringkasan | Metrik total + statistik |
| Detail Trx 41k | Semua barang + baris TOTAL |
| Tanpa Trx | Barang tanpa transaksi web |
| Ada Trx | Barang yang punya minimal 1 transaksi |
