# Migrasi Data — Desktop (MySQL) → Web (PostgreSQL)

Toolkit Python untuk migrasi master data dari aplikasi desktop ISX/evacer ke aplikasi web Laravel (PostgreSQL).

Dokumentasi lengkap mapping & bisnis: [`docs/DOKUMENTASI-MIGRASI.md`](docs/DOKUMENTASI-MIGRASI.md)

---

## Hal pertama setelah clone

Urutan yang benar:

```bash
git clone <url-repo> departement-store
cd departement-store

# 1. Setup otomatis (cek Python, buat .env, install dependencies)
chmod +x setup.sh run.sh
./setup.sh

# 2. Edit kredensial database
nano .env   # atau editor lain

# 3. Tes koneksi
./run.sh inspect
```

**Jadi:** clone → `./setup.sh` → edit `.env` → `./run.sh inspect` → baru migrasi.

---

## Prasyarat

| Komponen | Keterangan |
|----------|------------|
| **Python 3.10+** | Wajib |
| **MySQL** | Source data desktop (port default dev: `3307`) |
| **PostgreSQL** | Target web Laravel (port default: `5432`) |
| **Git** | Untuk clone repo |
| **Bash** | Linux / macOS / WSL (Windows) |

Database MySQL harus sudah di-restore dulu (mis. `evacer` + `evacer_fullbackup`).

---

## Install Python (jika belum ada)

### Linux (Ubuntu / Debian)

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git
python3 --version
```

### macOS

```bash
# Homebrew
brew install python3 git
python3 --version
```

### Windows

**Opsi A — WSL (disarankan):** install [WSL2](https://learn.microsoft.com/windows/wsl/install), lalu di dalam Ubuntu jalankan perintah Linux di atas.

**Opsi B — Native:** download dari [python.org](https://www.python.org/downloads/) — centang **"Add Python to PATH"** saat install.

```powershell
python --version
pip --version
```

Di Windows native, jalankan via Git Bash / WSL:

```bash
./setup.sh
./run.sh inspect
```

---

## Setup manual (alternatif dari `./setup.sh`)

### 1. Copy environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# PostgreSQL (target web)
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=department-local
PG_USERNAME=postgres
PG_PASSWORD=...

# MySQL master (supplier, golongan, barang)
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3307
MYSQL_MASTER_DATABASE=evacer

# MySQL full backup (stok dari invoicedetail)
MYSQL_TRX_DATABASE=evacer_fullbackup
MYSQL_USERNAME=root
MYSQL_PASSWORD=...

MIGRATION_DEFAULT_USER_ID=1
MIGRATION_BATCH_SIZE=2000
```

### 2. Install dependencies

Project memakai folder `vendor/` (tanpa install global):

```bash
python3 -m pip install -r requirements.txt -t vendor/
```

Jika folder `vendor/` sudah ikut di repo, langkah ini bisa dilewati.

---

## Perintah utama

Semua perintah lewat wrapper:

```bash
./run.sh <command>
```

| Perintah | Fungsi |
|----------|--------|
| `./run.sh mapping` | Lihat mapping tabel MySQL → PostgreSQL |
| `./run.sh inspect` | Bandingkan jumlah data + breakdown stok/konsinyasi |
| `./run.sh run --dry-run --only=supplier,brand,barang` | Simulasi migrasi |
| `./run.sh run --fresh --only=supplier,brand,barang` | Migrasi penuh (hapus data lama dulu) |
| `./run.sh run --only=barang --limit=1000` | Migrasi partial (testing) |

### Urutan migrasi

```
supplier → brand (golongan) → barang
```

Stok dihitung dari `invoicedetail` (DB full backup). Stok **boleh minus** sesuai ketentuan client.

### Estimasi waktu

- Supplier + golongan: < 1 menit
- Barang full (~725k): ~2–4 jam (tergantung spek laptop & `MIGRATION_BATCH_SIZE`)

---

## Restore database MySQL (jika belum ada)

Contoh import dump ke Docker MySQL:

```bash
# Buat database
mysql -h 127.0.0.1 -P 3307 -u root -p -e "CREATE DATABASE IF NOT EXISTS evacer;"
mysql -h 127.0.0.1 -P 3307 -u root -p -e "CREATE DATABASE IF NOT EXISTS evacer_fullbackup;"

# Import dump
mysql -h 127.0.0.1 -P 3307 -u root -p evacer < dump_master.sql
mysql -h 127.0.0.1 -P 3307 -u root -p evacer_fullbackup < dump_fullbackup.sql
```

Sesuaikan host/port dengan environment masing-masing.

---

## Struktur folder

```
departement-store/
├── README.md              ← panduan ini
├── setup.sh               ← setup pertama kali
├── run.sh                 ← jalankan migrasi
├── migrate.py             ← CLI utama
├── .env.example           ← template config (copy ke .env)
├── requirements.txt       ← dependencies Python
├── config/mapping.py      ← dokumentasi mapping kolom
├── docs/DOKUMENTASI-MIGRASI.md
├── lib/                   ← logic migrasi
└── vendor/                ← dependencies (auto-install via setup.sh)
```

---

## Troubleshooting

### `python3: command not found`

Install Python (lihat bagian di atas), lalu ulangi `./setup.sh`.

### `Connection refused` ke MySQL/PostgreSQL

- Pastikan service database sudah jalan
- Cek `host` / `port` di `.env`
- Tes manual: `mysql -h 127.0.0.1 -P 3307 -u root -p`

### `./run.sh: Permission denied`

```bash
chmod +x run.sh setup.sh
```

### Migrasi `--fresh` hang / lama

- Tutup DBeaver / pgAdmin yang membuka koneksi ke PostgreSQL
- Proses barang memang lama (~725k row) — lihat progress bar & spinner di terminal

### `ModuleNotFoundError: pymysql` / `psycopg2`

```bash
python3 -m pip install -r requirements.txt -t vendor/
```

### File `.env` tidak ikut di Git

Normal — `.env` berisi password dan di-ignore. Setiap developer copy dari `.env.example` sendiri.

---

## Checklist teman baru

- [ ] Python 3.10+ terinstall
- [ ] Git clone repo
- [ ] `./setup.sh` sukses
- [ ] `.env` sudah diisi kredensial DB yang benar
- [ ] MySQL: `evacer` + `evacer_fullbackup` sudah di-restore
- [ ] PostgreSQL: database target sudah ada (mis. `department-local`)
- [ ] `./run.sh inspect` → selisih barang = 0 (setelah migrasi)
- [ ] `./run.sh run --fresh --only=supplier,brand,barang` (jika perlu migrasi ulang)

---

## Catatan keamanan

- **Jangan commit** file `.env` ke GitHub
- Password database hanya di `.env` lokal masing-masing developer
