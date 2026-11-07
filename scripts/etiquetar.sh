#!/usr/bin/env bash
# Etiqueta una versión de un componente: bash scripts/etiquetar.sh <componente> <versión>
# Pide árbol limpio en main, VERSION igual, tag nuevo, compuerta y pruebas en verde, antes y sin cambios después.
# Las pruebas de un componente que las tiene necesitan su entorno: MOTOR_REPO y MOTOR_REF en el de gastos.
# No sube nada. Publicar el tag y entregarlo son pasos aparte.
set -euo pipefail
RAIZ=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
rechazo() { echo "etiquetar.sh: RECHAZADO: $*" >&2; exit 1; }
[ $# -eq 2 ] || { echo "uso: etiquetar.sh <componente> <versión>" >&2; exit 2; }
COMPONENTE=$1
VERSION=$2
TAG="$COMPONENTE-$VERSION"
[[ $COMPONENTE =~ ^[a-z0-9][a-z0-9_-]*$ ]] || rechazo "componente inválido"
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]] || rechazo "versión inválida"
cd "$RAIZ"
[ -d "$COMPONENTE" ] || rechazo "no existe el componente"
[ -z "$(git status --porcelain)" ] || rechazo "árbol con cambios"
[ "$(git symbolic-ref --short HEAD)" = main ] || rechazo "no estás en main"
[ "$(tr -d '[:space:]' < "$COMPONENTE/VERSION")" = "$VERSION" ] || rechazo "VERSION no coincide"
! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || rechazo "el tag ya existe"
ANTES=$(git rev-parse HEAD)
bash scripts/test.sh
if [ -f "$COMPONENTE/scripts/test.sh" ]; then bash "$COMPONENTE/scripts/test.sh"; fi
[ -z "$(git status --porcelain)" ] && [ "$(git rev-parse HEAD)" = "$ANTES" ] || rechazo "el árbol cambió durante las pruebas"
git tag -a "$TAG" -m "$TAG"
echo "$TAG"
