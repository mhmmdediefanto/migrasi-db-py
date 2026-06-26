# Koneksi DB Production via Tunnel (Biznet)

DigitalOcean PostgreSQL sering **tidak bisa diakses langsung** dari jaringan Biznet.  
Pakai **SSH tunnel** ke `127.0.0.1:55432`, lalu semua tool (psql, DataGrip, script Python) mengarah ke localhost.

---

## 1. Nyalakan tunnel

```bash
~/bin/matahari-production-tunnel.sh
```

Mode auto-reconnect (watcher). Log:

```bash
tail -f ~/.hermes/tunnel-logs/matahari-production-tunnel.log
```

Cek proses:

```bash
pgrep -af 'matahari-production-tunnel-watch.sh|55432:db-postgresql-sgp1'
```

Restart jika putus:

```bash
pkill -f matahari-production-tunnel-watch.sh || true
~/bin/matahari-production-tunnel.sh
```

---

## 2. Cek tunnel aktif

```bash
./scripts/check_tunnel.sh
# atau
nc -vz 127.0.0.1 55432
```

Sukses: `succeeded!`

---

## 3. Konfigurasi project ini (`.env`)

Saat pakai tunnel, ubah bagian PostgreSQL di `.env`:

```env
PG_HOST=127.0.0.1
PG_PORT=55432
PG_DATABASE=matahari-production
PG_USERNAME=doadmin
PG_PASSWORD=<password-dari-team>
PG_SSLMODE=require
```

Database lain lewat tunnel yang sama:

| Database | `PG_DATABASE` |
|----------|----------------|
| Production | `matahari-production` |
| Dev | `department-dev` |
| Clone lokal | tetap `127.0.0.1:5432` tanpa tunnel |

**Jangan commit** `.env` ke Git.

---

## 4. Perintah Python (tunnel)

Setelah `.env` diisi + tunnel jalan:

```bash
./run.sh inspect
./run.sh check-stok --from-file "..." --gudang pusat
./run.sh check-stok-trx --from-selisih output/stok_selisih_sragen_180626.xlsx --gudang pusat
python3 scripts/export_stok_trx_detail_prod.py
```

Script prod membaca **`.env`** — tidak perlu hardcode host/port di script.

---

## 5. psql manual

```bash
psql "host=127.0.0.1 port=55432 dbname=matahari-production user=doadmin sslmode=require"
```

Atau setelah `source scripts/pg_aliases.sh`:

```bash
pgprod          # matahari-production
pgdev           # department-dev
```

Password dari `.pgpass` (lihat bawah) atau ketik manual.

---

## 6. DataGrip

| Field | Nilai |
|-------|--------|
| Host | `127.0.0.1` |
| Port | `55432` |
| Database | `matahari-production` atau `department-dev` |
| User | `doadmin` |
| Password | (dari team) |
| SSL | `require` (jika gagal, coba `disable`) |

Tunnel harus **sudah jalan** sebelum Test Connection.

---

## 7. `.pgpass` (opsional, tanpa ketik password)

File: `~/.pgpass` (chmod 600)

```
127.0.0.1:55432:matahari-production:doadmin:PASSWORD
127.0.0.1:55432:department-dev:doadmin:PASSWORD
```

Format: `host:port:database:user:password`

---

## Ringkas

1. `~/bin/matahari-production-tunnel.sh`
2. `./scripts/check_tunnel.sh`
3. Edit `.env` → `127.0.0.1:55432`
4. `./run.sh ...` atau DataGrip ke localhost
