"""python -m informe armar|validar <datos.json> [--salida carpeta] [--raiz nombre=carpeta] [--mermaid archivo|url]"""
import argparse
import json
import re
import sys
from pathlib import Path

from .armado import armar, componer
from .datos import ErrorDatos


def _raices(pares):
    raices = {}
    for par in pares or []:
        nombre, igual, ruta = par.partition("=")
        if not igual or not re.match(r"^[a-z][a-z0-9_-]{0,39}$", nombre) or not Path(ruta).is_dir():
            raise ErrorDatos("--raiz va como nombre=carpeta, con una carpeta que exista: %s" % par)
        raices[nombre] = ruta
    return raices


def main(argumentos=None):
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="informe", description="Arma un informe navegable desde un archivo de datos.")
    p.add_argument("accion", choices=("armar", "validar"))
    p.add_argument("datos", help="archivo JSON con el informe")
    p.add_argument("--salida", help="carpeta donde se escribe el sitio (solo armar)")
    p.add_argument("--raiz", action="append", metavar="nombre=carpeta",
                   help="de dónde se leen los documentos citados; se repite por cada raíz")
    p.add_argument("--mermaid", help="copia local de la librería de diagramas, o una dirección https")
    a = p.parse_args(argumentos)
    try:
        try:
            datos = json.loads(Path(a.datos).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ErrorDatos("no se pudo leer %s: %s" % (a.datos, error)) from None
        raices = _raices(a.raiz)
        if a.accion == "validar":
            archivos = sorted(componer(datos, raices, a.mermaid))
            print("VÁLIDO: %d archivo(s) por escribir, %d documento(s) citado(s)"
                  % (len(archivos), sum(1 for r in archivos if r.startswith("doc/"))))
            return 0
        if not a.salida:
            raise ErrorDatos("armar pide --salida")
        archivos = armar(datos, raices, a.salida, a.mermaid)
        print("ARMADO: %d archivo(s) en %s" % (len(archivos), a.salida))
        print("Abrir: %s" % (Path(a.salida) / "index.html"))
        return 0
    except ErrorDatos as error:
        print("NO SE ARMÓ: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
