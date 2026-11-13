#!/usr/bin/env bash
# Pruebas del componente: solo biblioteca estándar, datos inventados, sin red y sin tocar ningún servidor.
set -euo pipefail
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || { echo "falta Python" >&2; exit 2; }
cd "$RAIZ"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RAIZ:$RAIZ/tests" "$PY" -B -m unittest discover -s tests -p 'test_*.py'
