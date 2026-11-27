#!/usr/bin/env bash
# Pruebas del componente videos. Solo biblioteca estándar, sin red y con datos inventados.
set -euo pipefail
AQUI=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || { echo "falta Python" >&2; exit 2; }
cd "$AQUI"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$AQUI" "$PY" -B -m unittest discover -s tests -p 'test_*.py'
