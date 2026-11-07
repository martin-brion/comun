#!/usr/bin/env bash
# Compuerta sobre todo el repo y sobre los mensajes de commit, más las pruebas de la compuerta.
# Las pruebas de cada componente son suyas: `<componente>/scripts/test.sh`.
set -euo pipefail
RAIZ=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || { echo "falta Python" >&2; exit 2; }
cd "$RAIZ"
PYTHONDONTWRITEBYTECODE=1 "$PY" -B scripts/verificar_identificadores.py --tree .
MENSAJES=$(mktemp -d)
trap 'rm -rf "$MENSAJES"' EXIT
git log --format=%B > "$MENSAJES/mensajes.txt"
PYTHONDONTWRITEBYTECODE=1 "$PY" -B scripts/verificar_identificadores.py --tree "$MENSAJES" --manifest scripts/identificadores.hashes.json
PYTHONDONTWRITEBYTECODE=1 "$PY" -B -m unittest discover -s tests -p 'test_*.py'
