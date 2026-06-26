# Source file: source scripts/pg_aliases.sh
# Password lewat ~/.pgpass (lihat docs/TUNNEL-DB.md)

export PG_TUNNEL_HOST="${PG_TUNNEL_HOST:-127.0.0.1}"
export PG_TUNNEL_PORT="${PG_TUNNEL_PORT:-55432}"
export PG_TUNNEL_USER="${PG_TUNNEL_USER:-doadmin}"

alias pgprod="psql \"host=${PG_TUNNEL_HOST} port=${PG_TUNNEL_PORT} dbname=matahari-production user=${PG_TUNNEL_USER} sslmode=require\""
alias pgdev="psql \"host=${PG_TUNNEL_HOST} port=${PG_TUNNEL_PORT} dbname=department-dev user=${PG_TUNNEL_USER} sslmode=require\""

echo "Alias: pgprod | pgdev (tunnel ${PG_TUNNEL_HOST}:${PG_TUNNEL_PORT})"
