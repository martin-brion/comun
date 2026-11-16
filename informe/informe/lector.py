"""Lee una sola base de conocimiento según su forma declarada y devuelve los datos del atlas (niveles 1 a 3).

No adivina ni completa: lo que falta se anota como «falta», lo vacío como «vacío», y una carpeta declarada que no está
frena con un mensaje que dice todas las que faltan. Lee solo `.md` dentro de la base, sin seguir enlaces, y lo que el
recorte oculta no se abre.
"""
import os
import posixpath
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

from .datos import ErrorDatos
from .forma import oculto, recorte as elegir_recorte
from .markdown import bloques, sin_comentarios
from .resumenes import huella

TOPE_DOCUMENTO = 2 * 1024 * 1024
TOPE_ARCHIVOS = 5000
TOPE_ENTRADAS = 50000
TOPE_HONDURA = 24
_VINCULO = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"\n]*\")?\s*\)")
_VERSION = re.compile(r"^(.*?)[-_.]v(\d{1,9})$", re.IGNORECASE)


def _es_enlace(ruta):
    return ruta.is_symlink() or (hasattr(ruta, "is_junction") and ruta.is_junction())


def orden_natural(texto):
    """Clave de orden: los números se comparan como números, así v10 va después de v2."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", texto.casefold()) if t]


def nombre_de_carpeta(ruta):
    """El nombre que se muestra de un área, un procedimiento o una pieza: su carpeta, con espacios."""
    base = posixpath.basename(ruta.rstrip("/")) or ruta
    if base.lower().endswith(".md"):
        base = base[:-3]
    texto = re.sub(r"[-_]+", " ", base).strip() or base
    return texto[:1].upper() + texto[1:]


def plano(texto):
    """El texto sin las marcas del formato de línea: para un dibujo, donde no se interpreta el formato."""
    texto = re.sub(r"\[([^\]\n]+)\]\([^)\s]+\)", r"\1", texto)
    return re.sub(r"\s+", " ", texto.replace("**", "").replace("`", "").replace("*", "")).strip()


def _normal_titulo(texto):
    texto = unicodedata.normalize("NFKD", plano(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", texto).strip().rstrip(":.").strip()


class Base:
    """La base en disco: recorre, lee y responde qué hay, siempre dentro de la raíz y sin pasar por enlaces."""

    def __init__(self, raiz, patrones):
        raiz = Path(raiz)
        if not raiz.exists():
            raise ErrorDatos("no existe la base %s" % raiz)
        if _es_enlace(raiz) or not raiz.is_dir():
            raise ErrorDatos("la base tiene que ser una carpeta, no un enlace: %s" % raiz)
        self.raiz = raiz.resolve()
        self.patrones = patrones
        self.docs = {}
        self.entradas = 0

    def oculto(self, ruta):
        return oculto(self.patrones, ruta)

    def _ruta(self, relativa):
        """La ruta en disco; frena si algún tramo es un enlace."""
        if relativa == ".":
            return self.raiz
        tramo = self.raiz
        for parte in relativa.split("/"):
            tramo = tramo / parte
            if _es_enlace(tramo):
                raise ErrorDatos("la base tiene un enlace en %s: no se siguen enlaces (sacalo o ponelo en ocultar)"
                                 % relativa)
        return tramo

    def es_carpeta(self, relativa):
        return self._ruta(relativa).is_dir()

    def es_archivo(self, relativa):
        return self._ruta(relativa).is_file()

    def recorrer(self, relativa):
        """(carpetas, archivos .md) visibles debajo de una carpeta, como rutas relativas a la base, en orden."""
        carpetas, archivos = [], []
        self._recorrer(relativa, carpetas, archivos, 0)
        return carpetas, archivos

    def _recorrer(self, relativa, carpetas, archivos, hondura):
        if hondura > TOPE_HONDURA:
            raise ErrorDatos("la base tiene carpetas más hondas que %d niveles en %s" % (TOPE_HONDURA, relativa))
        ruta = self._ruta(relativa)
        with os.scandir(ruta) as entradas:
            lista = sorted(entradas, key=lambda e: e.name)
        for e in lista:
            self.entradas += 1
            if self.entradas > TOPE_ENTRADAS:
                raise ErrorDatos("la base pasa de %d archivos y carpetas: ocultá lo que no va" % TOPE_ENTRADAS)
            hija = e.name if relativa == "." else "%s/%s" % (relativa, e.name)
            if self.oculto(hija):
                continue
            if e.is_symlink() or (hasattr(e, "is_junction") and e.is_junction()):
                raise ErrorDatos("la base tiene un enlace en %s: no se siguen enlaces (sacalo o ponelo en ocultar)"
                                 % hija)
            if e.is_dir(follow_symlinks=False):
                carpetas.append(hija)
                self._recorrer(hija, carpetas, archivos, hondura + 1)
            elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".md"):
                archivos.append(hija)
                if len(archivos) > TOPE_ARCHIVOS:
                    raise ErrorDatos("la base pasa de %d documentos: ocultá lo que no va" % TOPE_ARCHIVOS)

    def doc(self, relativa):
        """Lee un documento una sola vez: texto, huella, título, primer párrafo y si está vacío."""
        if relativa in self.docs:
            return self.docs[relativa]
        ruta = self._ruta(relativa)
        if ruta.stat().st_size > TOPE_DOCUMENTO:
            raise ErrorDatos("%s pasa de %d bytes" % (relativa, TOPE_DOCUMENTO))
        contenido = ruta.read_bytes()
        try:
            texto = contenido.decode("utf-8")
        except UnicodeDecodeError:
            raise ErrorDatos("%s no es texto UTF-8" % relativa) from None
        if texto.startswith("﻿"):
            texto = texto[1:]
        texto = texto.replace("\r\n", "\n").replace("\r", "\n")
        limpio = sin_comentarios(texto)
        lista = bloques(limpio)
        titulo = next((b["texto"] for b in lista if b["t"] == "h"), None)
        primero = next((b["texto"] for b in lista if b["t"] == "p"), None)
        if primero is None:
            lista_items = next((b for b in lista if b["t"] == "lista"), None)
            if lista_items:
                primero = " ".join(lista_items["items"][0]["lineas"])
        d = {"ruta": relativa, "texto": texto, "huella": huella(contenido),
             "titulo": titulo or posixpath.basename(relativa), "primero": primero or "",
             "vacio": all(b["t"] in ("h", "hr") for b in lista), "bloques": lista, "limpio": limpio}
        self.docs[relativa] = d
        return d

    def referencia(self, relativa):
        """Un archivo declarado: None si el recorte lo oculta; si no, con su estado (ok, vacío o falta)."""
        if self.oculto(relativa):
            return None
        if not self.es_archivo(relativa):
            return {"ruta": relativa, "estado": "falta"}
        return {"ruta": relativa, "estado": "vacio" if self.doc(relativa)["vacio"] else "ok"}


def secciones(doc, titulos):
    """El markdown de la sección que lleva uno de esos títulos, hasta el próximo título de su nivel; None si no
    está o si no tiene más que títulos."""
    if doc is None:
        return None
    buscados = {_normal_titulo(t) for t in titulos}
    lista = doc["bloques"]
    lineas = doc["limpio"].split("\n")
    for i, b in enumerate(lista):
        if b["t"] != "h" or _normal_titulo(b["texto"]) not in buscados:
            continue
        fin = len(lineas)
        for otro in lista[i + 1:]:
            if otro["t"] == "h" and otro["nivel"] <= b["nivel"]:
                fin = otro["a"] - 1
                break
        dentro = [o for o in lista[i + 1:] if o["a"] <= fin]
        if all(o["t"] in ("h", "hr") for o in dentro):
            return None
        return "\n".join(lineas[b["a"]:fin]).strip("\n")
    return None


def items_de_primer_nivel(doc):
    """Los ítems de primer nivel de todas las listas de un documento, en orden."""
    salida = []
    for b in doc["bloques"]:
        if b["t"] != "lista":
            continue
        minimo = min(it["sangria"] for it in b["items"])
        salida.extend(" ".join(it["lineas"]) for it in b["items"] if it["sangria"] == minimo)
    return salida


def vinculos(doc):
    """Las rutas a otros `.md` que nombra un documento con un vínculo markdown, resueltas contra su carpeta.

    Se comparan con los archivos ya recorridos: nada de esto se abre. Fuera de los bloques de código."""
    codigo = set()

    def marcar(lista):
        for b in lista:
            if b["t"] == "pre":
                codigo.update(range(b["a"], b["b"] + 1))
            elif b["t"] == "cita":
                marcar(b["dentro"])

    marcar(doc["bloques"])
    carpeta = posixpath.dirname(doc["ruta"])
    salida = []
    for n, linea in enumerate(doc["limpio"].split("\n"), 1):
        if n in codigo:
            continue
        for m in _VINCULO.finditer(linea):
            destino = unquote(m.group(1).split("#", 1)[0])
            if not destino or "://" in destino or destino.lower().startswith("mailto:"):
                continue
            if not destino.lower().endswith(".md"):
                continue
            junto = posixpath.normpath(posixpath.join(carpeta, destino.replace("\\", "/")))
            salida.append(junto)
    return salida


def _hijas(carpetas, madre):
    """Las subcarpetas directas de una carpeta, en orden natural."""
    prefijo = "" if madre == "." else madre + "/"
    hijas = [c for c in carpetas if c.startswith(prefijo) and "/" not in c[len(prefijo):] and c != madre]
    return sorted(hijas, key=orden_natural)


def _debajo(ruta, carpeta):
    return carpeta == "." or ruta.startswith(carpeta + "/")


def _comprobar_carpetas(base, pedidas, nombre_recorte):
    """Frena si falta alguna carpeta declarada, o si el recorte la oculta, diciendo todas juntas."""
    faltan = []
    for clave, carpeta in pedidas:
        if carpeta != "." and base.oculto(carpeta):
            faltan.append('la salida "%s" oculta %s/ (%s)' % (nombre_recorte, carpeta, clave))
        elif not base.es_carpeta(carpeta):
            faltan.append("falta la carpeta %s/ (%s)" % (carpeta, clave))
    if faltan:
        raise ErrorDatos("la base no tiene la forma declarada: %s" % "; ".join(faltan))


def _leer_areas(base, forma, propios):
    a = forma["areas"]
    _comprobar_carpetas(base, [("areas.carpeta", a["carpeta"])], base.recorte)
    carpetas, archivos = base.recorrer(a["carpeta"])
    areas = []
    for area in _hijas(carpetas, a["carpeta"]):
        reglas = "%s/%s" % (area, a["reglas"])
        carpeta_procs = "%s/%s" % (area, a["procedimientos"])
        procedimientos = []
        hay_procs = carpeta_procs in carpetas
        if hay_procs:
            for proc in _hijas(carpetas, carpeta_procs):
                documento = "%s/%s" % (proc, a["documento"])
                ref = base.referencia(documento)
                doc = base.doc(documento) if ref and ref["estado"] == "ok" else None
                otros = [r for r in archivos if _debajo(r, proc) and r != documento]
                procedimientos.append({
                    "carpeta": proc, "area": area, "nombre": nombre_de_carpeta(proc), "documento": ref,
                    "pasos": secciones(doc, a["pasos"]), "lee": secciones(doc, a["lee"]), "deja": secciones(doc, a["deja"]),
                    "otros": [base.referencia(r) for r in otros]})
        otros = [r for r in archivos if _debajo(r, area) and r != reglas and not _debajo(r, carpeta_procs)
                 and r not in propios]
        areas.append({"carpeta": area, "nombre": nombre_de_carpeta(area), "reglas": base.referencia(reglas),
                      "procedimientos": procedimientos,
                      "procedimientos_carpeta": None if hay_procs or base.oculto(carpeta_procs) else carpeta_procs,
                      "otros": [base.referencia(r) for r in otros]})
    return areas


def _piezas(eslabon, archivos):
    """Agrupa los archivos de un eslabón en piezas, cada una con sus versiones en orden (la más nueva al final)."""
    carpeta, modo = eslabon["carpeta"], eslabon["versiones"]
    grupos = {}
    for r in archivos:
        resto = r[len(carpeta) + 1:]
        if modo == "carpeta" and "/" in resto:
            clave, numero = resto.split("/", 1)[0], None
        elif modo == "sufijo":
            directorio, nombre = posixpath.split(resto)
            m = _VERSION.match(nombre[:-3])
            clave = posixpath.join(directorio, m.group(1)) if m and m.group(1) else resto[:-3]
            numero = int(m.group(2)) if m and m.group(1) else None
        else:
            clave, numero = resto[:-3], None
        grupos.setdefault(clave, []).append((numero, r))
    piezas = []
    for clave in sorted(grupos, key=orden_natural):
        versiones = sorted(grupos[clave], key=lambda v: (v[0] if v[0] is not None else -1, orden_natural(v[1])))
        piezas.append({"clave": clave, "nombre": nombre_de_carpeta(clave), "versiones": [r for _, r in versiones]})
    return piezas


def _leer_cadena(base, forma, propios):
    eslabones = forma["eslabones"]
    _comprobar_carpetas(base, [("eslabones[%d].carpeta" % i, e["carpeta"]) for i, e in enumerate(eslabones)],
                        base.recorte)
    faltan = []
    for i, e in enumerate(eslabones):
        if e["indice"] is None:
            continue
        if base.oculto(e["indice"]):
            faltan.append('la salida "%s" oculta el índice %s (eslabones[%d].indice)' % (base.recorte, e["indice"], i))
        elif not base.es_archivo(e["indice"]):
            faltan.append("falta el índice %s (eslabones[%d].indice)" % (e["indice"], i))
    if faltan:
        raise ErrorDatos("la base no tiene la forma declarada: %s" % "; ".join(faltan))
    indices = {e["indice"] for e in eslabones if e["indice"]}
    salida = []
    for i, e in enumerate(eslabones):
        _, archivos = base.recorrer(e["carpeta"])
        archivos = [r for r in archivos if r not in indices and r not in propios]
        piezas = _piezas(e, archivos)
        if e["indice"]:
            del_eslabon = set(archivos)
            marcados = set()
            for destino in vinculos(base.doc(e["indice"])):
                if destino == ".." or destino.startswith("../") or destino.startswith("/"):
                    raise ErrorDatos("el índice %s marca como vigente un archivo fuera de la base: %s"
                                     % (e["indice"], destino))
                if base.oculto(destino):
                    continue
                if destino not in del_eslabon:
                    raise ErrorDatos("el índice %s marca como vigente %s, que no es un archivo de su eslabón (%s/)"
                                     % (e["indice"], destino, e["carpeta"]))
                marcados.add(destino)
            for p in piezas:
                vigentes = [r for r in p["versiones"] if r in marcados]
                if len(vigentes) > 1:
                    raise ErrorDatos("el índice %s marca dos versiones vigentes de la misma pieza: %s"
                                     % (e["indice"], ", ".join(vigentes)))
                p["vigente"] = vigentes[0] if vigentes else None
        else:
            for p in piezas:
                p["vigente"] = p["versiones"][-1]
        for p in piezas:
            p["antecedentes"] = [r for r in reversed(p["versiones"]) if r != p["vigente"]]
            p["refs"] = {r: base.referencia(r) for r in p["versiones"]}
        salida.append({"carpeta": e["carpeta"], "titulo": e["titulo"], "con_versiones": e["versiones"] is not None,
                       "indice": base.referencia(e["indice"]) if e["indice"] else None, "piezas": piezas})
    # Una flecha une dos piezas de eslabones vecinos cuando una enlaza a la otra.
    de_quien = {}
    for i, e in enumerate(salida):
        for j, p in enumerate(e["piezas"]):
            for r in p["versiones"]:
                de_quien[r] = (i, j)
    flechas = set()
    for i, e in enumerate(salida):
        for j, p in enumerate(e["piezas"]):
            mostrado = p["vigente"]
            if not mostrado or p["refs"][mostrado]["estado"] != "ok":
                continue
            for destino in vinculos(base.doc(mostrado)):
                otro = de_quien.get(destino)
                if otro and abs(otro[0] - i) == 1:
                    flechas.add(tuple(sorted([(i, j), otro])))
    return salida, sorted(flechas)


def leer(forma, raiz, nombre_recorte=None):
    """Los datos del atlas de una base, para un recorte."""
    nombre, patrones = elegir_recorte(forma, nombre_recorte)
    base = Base(raiz, patrones)
    base.recorte = nombre
    propios = {forma[c] for c in ("orienta", "rutea", "funciones") if forma[c]}
    atlas = {"titulo": forma["titulo"], "forma": forma["forma"], "recorte": nombre, "base": base,
             "orienta": base.referencia(forma["orienta"]),
             "rutea": base.referencia(forma["rutea"]) if forma["rutea"] else None,
             "funciones": base.referencia(forma["funciones"]) if forma["funciones"] else None,
             "areas": [], "eslabones": [], "flechas": []}
    if forma["forma"] == "areas":
        atlas["areas"] = _leer_areas(base, forma, propios)
    else:
        atlas["eslabones"], atlas["flechas"] = _leer_cadena(base, forma, propios)
    f = atlas["funciones"]
    atlas["funciones_items"] = items_de_primer_nivel(base.doc(f["ruta"])) if f and f["estado"] == "ok" else []
    return atlas


def documentos(atlas):
    """Las rutas de los documentos con contenido que muestra el atlas, en orden: cada uno lleva su página."""
    rutas = set()

    def sumar(ref):
        if ref and ref["estado"] == "ok":
            rutas.add(ref["ruta"])

    for clave in ("orienta", "rutea", "funciones"):
        sumar(atlas[clave])
    for a in atlas["areas"]:
        sumar(a["reglas"])
        for o in a["otros"]:
            sumar(o)
        for p in a["procedimientos"]:
            sumar(p["documento"])
            for o in p["otros"]:
                sumar(o)
    for e in atlas["eslabones"]:
        sumar(e["indice"])
        for p in e["piezas"]:
            for ref in p["refs"].values():
                sumar(ref)
    return sorted(rutas)
