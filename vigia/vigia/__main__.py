"""python -m vigia correr --config vigia.json [--sin-fuentes]   una corrida; imprime el resumen
python -m vigia resumen --config vigia.json                  el resumen de la última corrida
python -m vigia foto                                         la foto de este servidor, en JSON
"""
import argparse
import json
import os
import sys

from . import config, corrida, foto


def main(argv=None):
    p = argparse.ArgumentParser(prog="vigia", description="Vigía de parches: observa y deja registro. No aplica nada.")
    sub = p.add_subparsers(dest="orden", required=True)
    c = sub.add_parser("correr")
    c.add_argument("--config", required=True)
    c.add_argument("--sin-fuentes", action="store_true", help="no consulta internet: solo la foto")
    r = sub.add_parser("resumen")
    r.add_argument("--config", required=True)
    sub.add_parser("foto")
    args = p.parse_args(argv)
    salida = sys.stdout
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    if args.orden == "foto":
        json.dump(foto.tomar(), salida)
        salida.write("\n")
        return 0
    try:
        cfg = config.cargar(args.config)
    except config.ConfigError as e:
        print(f"vigia: {e}", file=sys.stderr)
        return 2
    if args.orden == "resumen":
        try:
            with open(os.path.join(cfg["estado_dir"], "resumen.txt"), encoding="utf-8") as f:
                salida.write(f.read())
            return 0
        except OSError:
            print("vigia: todavía no hay una corrida", file=sys.stderr)
            return 1
    try:
        resultado = corrida.correr(cfg, con_fuentes=not args.sin_fuentes)
    except corrida.Ocupado as e:
        print(f"vigia: {e}", file=sys.stderr)
        return 3
    salida.write(resultado["resumen"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
