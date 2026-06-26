#!/usr/bin/env bash
# Jalankan setelah restart terminal jika "fork failed"
cd "$(dirname "$0")/.."
export PYTHONPATH="vendor:."
exec /usr/bin/python3 -u scripts/check_stok_trx_prod.py
