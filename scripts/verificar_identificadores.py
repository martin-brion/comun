#!/usr/bin/env python3
"""Compuerta de identificadores: usa hashes versionados, nunca imprime el texto hallado.

Contrato del manifiesto y límites: scripts/IDENTIFICADORES.md. Solo biblioteca estándar.
"""
import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata

NORMALIZACION = "NFKC.casefold"
MANIFIESTO = Path(__file__).with_name("identificadores.hashes.json")
MAX_BYTES = 8 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CODIGO = re.compile(r"(?<!\w)[a-z]-[a-z]{3}-[0-9]{2}(?!\w)")
NUMERACION = re.compile(r"(?<!\w)[a-z]+[0-9]+(?:-[0-9]+)+(?!\w)")
TIPOS = {"substring", "word", "code-template", "number-template"}


class GateError(Exception):
    pass


def normalizar(texto):
    return unicodedata.normalize("NFKC", texto).casefold()


@lru_cache(maxsize=65536)
def huella(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _objeto(pares):
    obj = {}
    for clave, valor in pares:
        if clave in obj:
            raise GateError("clave repetida en el manifiesto")
        obj[clave] = valor
    return obj


def cargar(ruta, raiz):
    try:
        ruta = ruta.resolve(strict=True)
        if (ruta == raiz or raiz in ruta.parents) and ruta != raiz / "scripts" / "identificadores.hashes.json":
            raise GateError("manifiesto interno fuera de la ruta canónica")
        data = json.loads(ruta.read_text(encoding="utf-8-sig"), object_pairs_hook=_objeto)
    except (OSError, UnicodeError, ValueError):
        raise GateError("manifiesto ausente o ilegible") from None
    claves = {"schema_version", "normalization", "source_sha256", "rules", "exceptions"}
    if not isinstance(data, dict) or set(data) != claves:
        raise GateError("esquema de manifiesto inválido")
    if (type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["normalization"] != NORMALIZACION
            or not isinstance(data["source_sha256"], str)
            or not HEX64.fullmatch(data["source_sha256"])):
        raise GateError("versión, normalización o procedencia inválida")
    if not isinstance(data["rules"], list) or not data["rules"]:
        raise GateError("manifiesto sin reglas")
    reglas = set()
    for regla in data["rules"]:
        if not isinstance(regla, dict) or set(regla) != {"kind", "length", "sha256"}:
            raise GateError("regla inválida")
        tipo, largo, sha = regla["kind"], regla["length"], regla["sha256"]
        if (not isinstance(tipo, str) or tipo not in TIPOS
                or type(largo) is not int or not 1 <= largo <= 1024
                or not isinstance(sha, str) or not HEX64.fullmatch(sha)
                or (tipo == "code-template" and largo != 8)
                or (tipo == "number-template" and not 3 <= largo <= 32)):
            raise GateError("regla inválida")
        item = (tipo, largo, sha)
        if item in reglas:
            raise GateError("regla repetida")
        reglas.add(item)
    if not isinstance(data["exceptions"], list):
        raise GateError("excepciones inválidas")
    excepciones = set()
    for item in data["exceptions"]:
        if (not isinstance(item, dict) or set(item) != {"path", "kind", "length", "sha256"}
                or item["path"] != "PROCEDENCIA.md"):
            raise GateError("solo PROCEDENCIA.md admite una excepción")
        clave = (item["kind"], item["length"], item["sha256"])
        if clave not in reglas or clave[0] != "substring" or clave in excepciones:
            raise GateError("excepción inválida")
        excepciones.add(clave)
    grupos = {}
    for tipo, largo, sha in reglas:
        grupos.setdefault((tipo, largo), set()).add(sha)
    return grupos, excepciones


def plantilla_de(token):
    """El formato de un código de numeración: letras -> @, dígitos -> #, guiones tal cual (dos letras, cuatro cifras, guion, cuatro cifras -> @@####-####)."""
    return "".join("@" if c.isalpha() else "#" if c.isdigit() else c for c in token)


def es_palabra(caracter):
    return caracter.isalnum() or caracter == "_"


def coincidencias(texto, grupos):
    texto = normalizar(texto)
    resultados = []
    for (tipo, largo), hashes in grupos.items():
        if tipo == "code-template":
            for m in CODIGO.finditer(texto):
                sha = huella(m.group()[0] + "-@@@-##")
                if sha in hashes:
                    resultados.append((m.start(), m.end(), (tipo, largo, sha)))
            continue
        if tipo == "number-template":
            for m in NUMERACION.finditer(texto):
                plantilla = plantilla_de(m.group())
                if len(plantilla) != largo:
                    continue
                sha = huella(plantilla)
                if sha in hashes:
                    resultados.append((m.start(), m.end(), (tipo, largo, sha)))
            continue
        for inicio in range(len(texto) - largo + 1):
            fin = inicio + largo
            if tipo == "word" and ((inicio and es_palabra(texto[inicio - 1]))
                                    or (fin < len(texto) and es_palabra(texto[fin]))):
                continue
            sha = huella(texto[inicio:fin])
            if sha in hashes:
                resultados.append((inicio, fin, (tipo, largo, sha)))
    return resultados


def ruta_segura(rel, grupos):
    intervalos = [(inicio, fin) for inicio, fin, _ in coincidencias(rel, grupos)]
    if not intervalos:
        return rel.encode("unicode_escape").decode("ascii")
    unidos = []
    for inicio, fin in sorted(intervalos):
        if unidos and inicio <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], fin))
        else:
            unidos.append((inicio, fin))
    texto = normalizar(rel)
    # En el nombre puede estar el propio identificador; tampoco lo mostramos.
    for inicio, fin in reversed(unidos):
        texto = texto[:inicio] + "[identificador]" + texto[fin:]
    return texto.encode("unicode_escape").decode("ascii")


def revisar(raiz, grupos, excepciones):
    hallazgos = []
    archivos = 0

    def error_walk(_error):
        raise GateError("no se pudo recorrer todo el árbol")

    for carpeta, dirs, files in os.walk(raiz, followlinks=False, onerror=error_walk):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for nombre in sorted(dirs + [f for f in files if f != ".git"]):
            ruta = Path(carpeta) / nombre
            rel = ruta.relative_to(raiz).as_posix()
            segura = ruta_segura(rel, grupos)
            if coincidencias(rel, grupos):
                hallazgos.append(f"{segura}:0: identificador en nombre")
            try:
                info = ruta.lstat()
                if ruta.is_symlink() or (hasattr(ruta, "is_junction") and ruta.is_junction()):
                    raise GateError(f"{segura}:0: enlace no permitido")
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    raise GateError(f"{segura}:0: archivo no verificable")
                texto = ruta.read_text(encoding="utf-8-sig")
                if "\x00" in texto:
                    raise GateError(f"{segura}:0: contenido binario")
            except (OSError, UnicodeError):
                raise GateError(f"{segura}:0: archivo ilegible o no UTF-8") from None
            archivos += 1
            for linea, contenido in enumerate(texto.split("\n"), 1):
                matches = coincidencias(contenido, grupos)
                if rel == "PROCEDENCIA.md":
                    matches = [m for m in matches if m[2] not in excepciones]
                if matches:
                    hallazgos.append(f"{segura}:{linea}: identificador en contenido")
    if not archivos:
        raise GateError("árbol sin archivos verificables")
    return archivos, sorted(set(hallazgos))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--manifest", default=str(MANIFIESTO))
    args = parser.parse_args(argv)
    try:
        if not args.manifest:
            raise GateError("falta el manifiesto por hashes")
        raiz = Path(args.tree).resolve(strict=True)
        if not raiz.is_dir():
            raise GateError("árbol inválido")
        grupos, excepciones = cargar(Path(args.manifest), raiz)
        archivos, hallazgos = revisar(raiz, grupos, excepciones)
    except GateError as error:
        print(f"IDENTIFICADORES ERROR: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print("IDENTIFICADORES ERROR: entrada no verificable", file=sys.stderr)
        return 2
    for hallazgo in hallazgos:
        print(hallazgo)
    print(f"IDENTIFICADORES {'FALLA' if hallazgos else 'OK'}: {len(hallazgos)} hallazgos; {archivos} archivos")
    return 1 if hallazgos else 0


if __name__ == "__main__":
    sys.exit(main())
