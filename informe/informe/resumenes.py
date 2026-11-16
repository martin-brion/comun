"""La caché de resúmenes: un resumen por documento, guardado con la huella del contenido del archivo.

El componente no escribe resúmenes ni llama a ningún modelo. Los lista pendientes y los incorpora cuando alguien de
afuera los escribió. Un resumen vale solo mientras la huella sea la del archivo de hoy.
"""
import hashlib
import json
import os
import re
from pathlib import Path

from .datos import ErrorDatos
from .forma import ruta_relativa

FORMATO = "resumenes-1"
TOPE = 1200
_HUELLA = re.compile(r"^sha256:[0-9a-f]{64}$")
TOPE_ARCHIVO = 16 * 1024 * 1024


def huella(contenido):
    return "sha256:" + hashlib.sha256(contenido).hexdigest()


def _validar(d, donde, vacios_valen=False):
    """{ruta: {huella, resumen}} de un archivo de resúmenes; los vacíos, si valen, quedan con resumen vacío."""
    if not isinstance(d, dict) or sorted(d) != ["formato", "resumenes"] or d.get("formato") != FORMATO:
        raise ErrorDatos('%s: tiene que ser {"formato": "%s", "resumenes": {...}}' % (donde, FORMATO))
    if not isinstance(d["resumenes"], dict):
        raise ErrorDatos('%s: "resumenes" tiene que ser un objeto' % donde)
    salida = {}
    for ruta, entrada in d["resumenes"].items():
        de = "%s: %s" % (donde, ruta)
        ruta_relativa(ruta, de)
        if not ruta.lower().endswith(".md"):
            raise ErrorDatos("%s: solo hay resúmenes de archivos .md" % de)
        if not isinstance(entrada, dict) or sorted(entrada) != ["huella", "resumen"]:
            raise ErrorDatos('%s: cada entrada es {"huella", "resumen"}' % de)
        if not isinstance(entrada["huella"], str) or not _HUELLA.match(entrada["huella"]):
            raise ErrorDatos("%s: la huella va como sha256:<64 letras hexadecimales>" % de)
        resumen = entrada["resumen"]
        if not isinstance(resumen, str) or len(resumen) > TOPE or (not resumen.strip() and not vacios_valen):
            raise ErrorDatos("%s: el resumen es un texto de 1 a %d caracteres" % (de, TOPE))
        salida[ruta] = {"huella": entrada["huella"], "resumen": resumen.strip()}
    return salida


def _leer_json(ruta, que):
    ruta = Path(ruta)
    try:
        if ruta.stat().st_size > TOPE_ARCHIVO:
            raise ErrorDatos("%s pasa de %d bytes" % (ruta, TOPE_ARCHIVO))
        return json.loads(ruta.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ErrorDatos("no se pudo leer %s %s: %s" % (que, ruta, error)) from None


def cargar(ruta, debe_existir=True):
    """La caché guardada. Si no existe y no hace falta que exista, está vacía."""
    if ruta is None:
        return {}
    if not Path(ruta).exists() and not debe_existir:
        return {}
    if not Path(ruta).is_file():
        raise ErrorDatos("no existe el archivo de resúmenes %s" % ruta)
    return _validar(_leer_json(ruta, "el archivo de resúmenes"), str(ruta))


def vigente(cache, ruta, huella_hoy):
    """El resumen guardado, si es de esta versión del archivo; si no, None."""
    entrada = cache.get(ruta)
    if entrada and entrada["huella"] == huella_hoy:
        return entrada["resumen"]
    return None


def como_texto(cache):
    datos = {"formato": FORMATO, "resumenes": {r: cache[r] for r in sorted(cache)}}
    return json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def guardar(ruta, cache):
    """Escribe la caché ordenada y la reemplaza de una vez: nunca queda a medias."""
    ruta = Path(ruta)
    if ruta.is_symlink():
        raise ErrorDatos("el archivo de resúmenes no puede ser un enlace: %s" % ruta)
    temporal = ruta.with_name(ruta.name + ".nuevo")
    temporal.write_bytes(como_texto(cache).encode("utf-8"))
    os.replace(temporal, ruta)


def _huella_en_base(base, ruta):
    """La huella de hoy de un archivo de la base, sin pasar por enlaces ni salir de ella. None si no es un archivo."""
    tramo = base
    for parte in ruta.split("/"):
        tramo = tramo / parte
        if tramo.is_symlink() or (hasattr(tramo, "is_junction") and tramo.is_junction()):
            raise ErrorDatos("%s: no se siguen enlaces" % ruta)
    if not tramo.is_file() or tramo.stat().st_size > TOPE_ARCHIVO:
        return None
    return huella(tramo.read_bytes())


def incorporar(nuevos, base, ruta_cache):
    """Suma a la caché los resúmenes de `nuevos`. Devuelve (sumados, salteados por vacíos). Frena sin escribir nada
    si una ruta no es un archivo de la base o si la huella no es la de hoy."""
    base = Path(base)
    if not base.is_dir() or base.is_symlink():
        raise ErrorDatos("la base no es una carpeta: %s" % base)
    entradas = _validar(_leer_json(nuevos, "el archivo de resúmenes nuevos"), str(nuevos), vacios_valen=True)
    cache = cargar(ruta_cache, debe_existir=False)
    errores, sumados, salteados = [], 0, 0
    for ruta in sorted(entradas):
        entrada = entradas[ruta]
        if not entrada["resumen"]:
            salteados += 1
            continue
        hoy = _huella_en_base(base, ruta)
        if hoy is None:
            errores.append("%s no es un archivo de la base" % ruta)
        elif hoy != entrada["huella"]:
            errores.append("%s cambió después de escribir su resumen (la huella no es la de hoy)" % ruta)
        else:
            cache[ruta] = entrada
            sumados += 1
    if errores:
        extra = " y %d más" % (len(errores) - 5) if len(errores) > 5 else ""
        raise ErrorDatos("no se incorporó nada: %s%s" % ("; ".join(errores[:5]), extra))
    guardar(ruta_cache, cache)
    return sumados, salteados
