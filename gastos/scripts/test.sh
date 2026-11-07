#!/usr/bin/env bash
# Copia fijada del motor, cargador real y datos temporales por suite.
set -euo pipefail
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
MOTOR=${MOTOR_REPO:?indicar MOTOR_REPO}
REF=${MOTOR_REF:?indicar MOTOR_REF exacta}
PY=${PYTHON:-python3}
PY=$(command -v "$PY")
PYBIN=$(dirname "$PY")
RUN=$(mktemp -d)
trap 'rm -rf "$RUN"' EXIT
mkdir -p "$RUN/motor" "$RUN/plugin"
git -C "$MOTOR" archive "$REF" | tar -x -C "$RUN/motor"
cp -R "$RAIZ/plugin/gastos" "$RUN/plugin/"
cp -R "$RAIZ/ejemplo_capa" "$RUN/capa"
cp "$RAIZ"/tests/test_*plugin.py "$RUN/motor/tests/"
cp -R "$RAIZ/tests/datos" "$RUN/datos"
"$PY" -I -B "$RUN/motor/scripts/verificar_identificadores.py" --tree "$RAIZ" --manifest "$RUN/motor/scripts/identificadores.hashes.json"
echo "motor: $(git -C "$MOTOR" rev-parse "$REF^{commit}")"
for ARCHIVO in "$RAIZ"/tests/test_*plugin.py; do
  SUITE=$(basename "$ARCHIVO" .py)
  mkdir -p "$RUN/$SUITE/data" "$RUN/$SUITE/ws" "$RUN/$SUITE/base" "$RUN/$SUITE/home"
  (
    cd "$RUN/motor"
    env -i PATH="$PYBIN:$PATH" LANG=C.UTF-8 HOME="$RUN/$SUITE/home" TMPDIR="$RUN/$SUITE" \
      PYTHONPATH="$RUN/motor:$RUN/motor/tests" \
      DATA_DIR="$RUN/$SUITE/data" WORKSPACES_DIR="$RUN/$SUITE/ws" BASELINE_DIR="$RUN/$SUITE/base" \
      MODO_IDENTIDAD=pin GASTOS_PLUGIN_DIR="$RUN/plugin" GASTOS_CAPA_DIR="$RUN/capa" GASTOS_DATOS_DIR="$RUN/datos" PYTHONDONTWRITEBYTECODE=1 \
      timeout 300 "$PY" -m unittest -v "tests.$SUITE"
  )
done
(
  cd "$RAIZ/odoo"
  env -i PATH="$PYBIN:$PATH" LANG=C.UTF-8 HOME="$RUN/home-odoo" PYTHONDONTWRITEBYTECODE=1 \
    timeout 300 "$PY" -B -m unittest discover -s tests -p 'test_receipt*.py' -v
)
