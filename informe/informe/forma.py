"""Valida la declaración de la forma de una base de conocimiento. El contrato está en CONTRATO.md, «El atlas».

La forma se declara, no se adivina: una clave que no figura en el contrato es un error, y nada se completa en silencio.
"""
import re

from .datos import ErrorDatos

FORMAS = ("areas", "cadena")
VERSIONES = ("carpeta", "sufijo")
TITULOS_POR_DEFECTO = {"pasos": ["Pasos"], "lee": ["Lee", "De dónde lee"], "deja": ["Deja", "Qué deja"]}
_SALIDA = re.compile(r"^[a-z][a-z0-9-]{0,29}$")
_PROHIBIDO = re.compile(r'[\x00-\x1f:*?"<>|\\]')
COMPLETA = "completa"


def _fallo(donde, que):
    raise ErrorDatos("%s: %s" % (donde, que))


def _solo(d, permitidas, donde):
    if not isinstance(d, dict):
        _fallo(donde, "tiene que ser un objeto")
    sobran = sorted(set(d) - set(permitidas))
    if sobran:
        _fallo(donde, "clave desconocida: %s" % ", ".join(sobran))


def _texto(d, clave, donde, tope, opcional=False):
    v = d.get(clave)
    if v is None and opcional:
        return None
    if not isinstance(v, str) or not v.strip():
        _fallo(donde, '"%s" tiene que ser un texto no vacío' % clave)
    if len(v) > tope:
        _fallo(donde, '"%s" pasa de %d caracteres' % (clave, tope))
    return v.strip()


def ruta_relativa(valor, donde, punto=False):
    """Una ruta relativa a la base, partida por `/`: sin `..`, sin `.`, sin unidad ni barra inicial."""
    if not isinstance(valor, str) or not valor or len(valor) > 300:
        _fallo(donde, "tiene que ser una ruta relativa a la base, como carpeta/archivo.md")
    if punto and valor == ".":
        return "."
    partes = valor.split("/")
    for p in partes:
        if p in ("", ".", "..") or _PROHIBIDO.search(p) or p != p.strip():
            _fallo(donde, "la ruta %r tiene que ser relativa a la base y no puede salir de ella" % valor)
    return "/".join(partes)


def _patron(valor, donde):
    """Una ruta de `ocultar`: tramos con nombre, `*` (un nombre cualquiera) o `**` (cualquier cantidad de carpetas)."""
    if not isinstance(valor, str) or not valor or len(valor) > 300:
        _fallo(donde, "tiene que ser una ruta relativa a la base")
    tramos = valor.split("/")
    for t in tramos:
        if t in ("*", "**"):
            continue
        if t in ("", ".", "..") or _PROHIBIDO.search(t) or t != t.strip():
            _fallo(donde, "la ruta %r no vale: tramos con nombre, * o **, sin .. ni barra inicial" % valor)
    return [t if t in ("*", "**") else t.casefold() for t in tramos]


def _patrones(d, donde):
    lista = d.get("ocultar", [])
    if not isinstance(lista, list):
        _fallo(donde, '"ocultar" tiene que ser una lista de rutas')
    return [_patron(v, "%s.ocultar[%d]" % (donde, i)) for i, v in enumerate(lista)]


def _titulos(d, clave, donde):
    if clave not in d:
        return list(TITULOS_POR_DEFECTO[clave])
    lista = d[clave]
    if not isinstance(lista, list) or not lista or any(not isinstance(t, str) or not t.strip() or len(t) > 80
                                                         for t in lista):
        _fallo(donde, '"%s" tiene que ser una lista de títulos' % clave)
    return [t.strip() for t in lista]


def _dentro(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def validar(d):
    """Devuelve la forma normalizada; si no cumple el contrato, levanta ErrorDatos diciendo dónde."""
    donde = "forma"
    _solo(d, ("titulo", "forma", "orienta", "rutea", "funciones", "areas", "eslabones", "ocultar", "salidas"), donde)
    f = {"titulo": _texto(d, "titulo", donde, 120)}
    if d.get("forma") not in FORMAS:
        _fallo(donde, '"forma" tiene que ser una de: %s' % ", ".join(FORMAS))
    f["forma"] = d["forma"]
    if "orienta" not in d:
        _fallo(donde, 'falta "orienta": el archivo que orienta, con las reglas de base')
    for clave in ("orienta", "rutea", "funciones"):
        f[clave] = ruta_relativa(d[clave], "%s.%s" % (donde, clave)) if clave in d else None
    f["areas"] = f["eslabones"] = None
    if f["forma"] == "areas":
        if "eslabones" in d:
            _fallo(donde, '"eslabones" va solo con la forma cadena')
        if "areas" not in d:
            _fallo(donde, 'la forma areas pide "areas"')
        da = "%s.areas" % donde
        a = d["areas"]
        _solo(a, ("carpeta", "reglas", "procedimientos", "documento", "pasos", "lee", "deja"), da)
        for clave in ("carpeta", "reglas", "procedimientos", "documento"):
            if clave not in a:
                _fallo(da, 'falta "%s"' % clave)
        f["areas"] = {"carpeta": ruta_relativa(a["carpeta"], da + ".carpeta", punto=True),
                      "reglas": ruta_relativa(a["reglas"], da + ".reglas"),
                      "procedimientos": ruta_relativa(a["procedimientos"], da + ".procedimientos"),
                      "documento": ruta_relativa(a["documento"], da + ".documento")}
        for clave in ("pasos", "lee", "deja"):
            f["areas"][clave] = _titulos(a, clave, da)
    else:
        if "areas" in d:
            _fallo(donde, '"areas" va solo con la forma areas')
        lista = d.get("eslabones")
        if not isinstance(lista, list) or not 1 <= len(lista) <= 5:
            _fallo(donde, '"eslabones" tiene que ser una lista de uno a cinco eslabones')
        f["eslabones"] = []
        for i, e in enumerate(lista):
            de = "%s.eslabones[%d]" % (donde, i)
            _solo(e, ("carpeta", "titulo", "versiones", "indice"), de)
            if "carpeta" not in e:
                _fallo(de, 'falta "carpeta"')
            eslabon = {"carpeta": ruta_relativa(e["carpeta"], de + ".carpeta"), "titulo": _texto(e, "titulo", de, 40),
                       "versiones": e.get("versiones"), "indice": None}
            if eslabon["versiones"] is not None and eslabon["versiones"] not in VERSIONES:
                _fallo(de, '"versiones" tiene que ser una de: %s' % ", ".join(VERSIONES))
            if ("versiones" in e) != ("indice" in e):
                _fallo(de, '"versiones" e "indice" van juntos: sin índice no se sabe cuál es la vigente')
            if "indice" in e:
                eslabon["indice"] = ruta_relativa(e["indice"], de + ".indice")
                if not eslabon["indice"].lower().endswith(".md"):
                    _fallo(de, "el índice es un archivo .md")
            for otro in f["eslabones"]:
                if _dentro(eslabon["carpeta"], otro["carpeta"]):
                    _fallo(de, "las carpetas de los eslabones no pueden repetirse ni estar una dentro de otra")
            f["eslabones"].append(eslabon)
    f["ocultar"] = _patrones(d, donde)
    if "salidas" in d:
        salidas = d["salidas"]
        if not isinstance(salidas, dict) or not salidas:
            _fallo(donde, '"salidas" tiene que ser un objeto con al menos una salida')
        f["salidas"] = {}
        for nombre in sorted(salidas):
            ds = "%s.salidas.%s" % (donde, nombre)
            if not _SALIDA.match(nombre):
                _fallo(ds, "el nombre de una salida va en minúsculas, números y guiones")
            _solo(salidas[nombre], ("ocultar",), ds)
            f["salidas"][nombre] = _patrones(salidas[nombre], ds)
    else:
        f["salidas"] = {COMPLETA: []}
    return f


def recorte(forma, nombre=None):
    """(nombre, patrones que oculta) del recorte pedido. Con varias salidas declaradas, hay que elegir una."""
    salidas = forma["salidas"]
    if nombre is None:
        if len(salidas) > 1:
            raise ErrorDatos("la forma declara varias salidas (%s): elegí una con --recorte" % ", ".join(salidas))
        nombre = next(iter(salidas))
    if nombre not in salidas:
        raise ErrorDatos('la forma no declara la salida "%s". Las que hay: %s' % (nombre, ", ".join(salidas)))
    return nombre, forma["ocultar"] + salidas[nombre]


def calza(patron, tramos):
    """Verdadero si el patrón nombra la ruta o una carpeta que la contiene."""
    if not patron:
        return True
    if patron[0] == "**":
        return any(calza(patron[1:], tramos[i:]) for i in range(len(tramos) + 1))
    if not tramos:
        return False
    return (patron[0] == "*" or patron[0] == tramos[0]) and calza(patron[1:], tramos[1:])


def oculto(patrones, ruta):
    """Verdadero si la ruta, relativa a la base, queda fuera de la salida."""
    if ruta in ("", "."):
        return False
    tramos = [t.casefold() for t in ruta.split("/")]
    if any(t.startswith(".") for t in tramos):
        return True
    return any(calza(p, tramos) for p in patrones)
