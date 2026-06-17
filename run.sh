#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="${ROOT}/vendor:${ROOT}"
exec python3 -u "${ROOT}/migrate.py" "$@"
