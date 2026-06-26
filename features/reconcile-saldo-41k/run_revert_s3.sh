#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="${ROOT}/../../vendor:${ROOT}/../../:${ROOT}"
exec python3 -u "${ROOT}/revert_stok_s3.py" "$@"
