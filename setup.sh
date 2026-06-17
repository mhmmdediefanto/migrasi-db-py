#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=== Setup migrasi departement-store ==="

# 1. Cek Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 tidak ditemukan."
  echo "Install Python 3.10+ dulu, lalu jalankan ulang: ./setup.sh"
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $(python3 --version)"

# 2. File .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ .env dibuat dari .env.example — EDIT kredensial database sebelum migrasi!"
else
  echo "✓ .env sudah ada"
fi

# 3. Dependencies (folder vendor/)
if [ -d vendor/pymysql ] && [ -d vendor/psycopg2 ]; then
  echo "✓ Dependencies sudah ada di vendor/"
else
  echo "> Menginstall dependencies ke vendor/ ..."
  python3 -m pip install --upgrade pip
  python3 -m pip install -r requirements.txt -t vendor/
  echo "✓ Dependencies terinstall"
fi

chmod +x run.sh

echo ""
echo "Setup selesai. Langkah berikutnya:"
echo "  1. Edit .env (host, port, user, password database)"
echo "  2. Pastikan MySQL & PostgreSQL sudah jalan + database sudah di-restore"
echo "  3. ./run.sh inspect"
echo "  4. ./run.sh run --fresh --only=supplier,brand,barang"
