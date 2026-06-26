#!/usr/bin/env bash
# ============================================================
# clone_prod_to_local.sh
# Dump DB production via tunnel dan restore ke local clone.
#
# Usage:
#   bash scripts/clone_prod_to_local.sh [nama_db_lokal]
#
# Default nama_db_lokal: matahari_prod_clone
# Tunnel harus SUDAH jalan sebelum dijalankan.
# ============================================================
set -euo pipefail

TUNNEL_HOST="${PG_TUNNEL_HOST:-127.0.0.1}"
TUNNEL_PORT="${PG_TUNNEL_PORT:-55432}"
TUNNEL_USER="${PG_TUNNEL_USER:-doadmin}"
PROD_DB="${PG_PROD_DB:-matahari-production}"
PROD_PASS="${PG_PROD_PASS:-}"

LOCAL_HOST="${LOCAL_PG_HOST:-127.0.0.1}"
LOCAL_PORT="${LOCAL_PG_PORT:-5432}"
LOCAL_USER="${LOCAL_PG_USER:-$(whoami)}"
LOCAL_DB="${1:-matahari_prod_clone}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${ROOT}/backup"
DUMP_FILE="${BACKUP_DIR}/matahari-production_${TIMESTAMP}.dump"

mkdir -p "${BACKUP_DIR}"

echo "=== Clone Production → Local ==="
echo "  Tunnel : ${TUNNEL_HOST}:${TUNNEL_PORT} / ${PROD_DB}"
echo "  Local  : ${LOCAL_HOST}:${LOCAL_PORT} / ${LOCAL_DB}"
echo "  Dump   : ${DUMP_FILE}"
echo ""

# Cek tunnel
if ! nc -vz "${TUNNEL_HOST}" "${TUNNEL_PORT}" 2>/dev/null; then
  echo "ERROR: Tunnel tidak terbuka di ${TUNNEL_HOST}:${TUNNEL_PORT}"
  echo "  Jalankan dulu: ~/bin/matahari-production-tunnel.sh"
  exit 1
fi
echo "✓ Tunnel OK"

# Dump
echo "Dump ${PROD_DB} ..."
PGPASSWORD="${PROD_PASS}" pg_dump \
  "host=${TUNNEL_HOST} port=${TUNNEL_PORT} dbname=${PROD_DB} user=${TUNNEL_USER} sslmode=require" \
  --format=custom \
  --no-owner \
  --no-acl \
  -f "${DUMP_FILE}"
echo "✓ Dump selesai: ${DUMP_FILE}"

# Buat DB lokal (skip kalau sudah ada)
if PGPASSWORD="" psql -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" -U "${LOCAL_USER}" \
    -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "${LOCAL_DB}"; then
  echo "DB lokal '${LOCAL_DB}' sudah ada — drop dulu? (y/N)"
  read -r REPLY
  if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
    PGPASSWORD="" dropdb -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" -U "${LOCAL_USER}" "${LOCAL_DB}"
    echo "  Dropped: ${LOCAL_DB}"
  else
    echo "  Lewati drop — restore akan append ke DB lama."
  fi
fi

if ! PGPASSWORD="" psql -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" -U "${LOCAL_USER}" \
    -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "${LOCAL_DB}"; then
  PGPASSWORD="" createdb -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" -U "${LOCAL_USER}" "${LOCAL_DB}"
  echo "✓ DB lokal dibuat: ${LOCAL_DB}"
fi

# Restore
echo "Restore ke ${LOCAL_DB} ..."
PGPASSWORD="" pg_restore \
  -h "${LOCAL_HOST}" \
  -p "${LOCAL_PORT}" \
  -U "${LOCAL_USER}" \
  -d "${LOCAL_DB}" \
  --no-owner \
  --no-acl \
  --exit-on-error \
  "${DUMP_FILE}" \
  && echo "✓ Restore selesai!" \
  || echo "WARN: Ada error saat restore (lihat di atas), tapi mungkin tidak fatal"

echo ""
echo "=== Selesai ==="
echo "Sekarang update .env untuk koneksi ke clone lokal:"
echo "  PG_HOST=${LOCAL_HOST}"
echo "  PG_PORT=${LOCAL_PORT}"
echo "  PG_DATABASE=${LOCAL_DB}"
echo "  PG_USERNAME=${LOCAL_USER}"
echo "  PG_PASSWORD="
echo "  # hapus/kosongkan PG_SSLMODE"
echo ""
echo "Lalu dry-run koreksi stok:"
echo "  ./run.sh fix-stok-pusat --dry-run --from-selisih output/stok_selisih_sragen_180626.xlsx"
