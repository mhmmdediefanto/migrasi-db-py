#!/usr/bin/env bash
set -euo pipefail

HOST="${PG_TUNNEL_HOST:-127.0.0.1}"
PORT="${PG_TUNNEL_PORT:-55432}"

echo "Cek tunnel PostgreSQL: ${HOST}:${PORT}"

if command -v nc >/dev/null 2>&1; then
  if nc -vz "$HOST" "$PORT" 2>&1; then
    echo "✓ Tunnel OK — bisa pakai PG_HOST=${HOST} PG_PORT=${PORT} di .env"
    exit 0
  fi
  echo "✗ Tunnel tidak merespons. Jalankan: ~/bin/matahari-production-tunnel.sh"
  exit 1
fi

echo "nc tidak ada; coba: pg_isready -h $HOST -p $PORT"
if command -v pg_isready >/dev/null 2>&1; then
  pg_isready -h "$HOST" -p "$PORT" && exit 0
fi
exit 1
