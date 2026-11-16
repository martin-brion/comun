"""Arma el atlas de una base: el mapa de la casa, una página por área o eslabón, una por procedimiento y el documento.

Todo sale de los datos que devuelve el lector y se compone en memoria antes de escribir, como el informe.
"""
import argparse
import html
import json
import posixpath
import re
import sys
import unicodedata
from pathlib import Path

from . import forma as forma_mod
from . import lector, resumenes
from .armado import _bloque, _pagina, comprobar_enlaces, escribir, recursos
from .datos import BLOQUES, ErrorDatos
from .mapa import enlaces
from .markdown import a_html, en_linea

CAJAS_POR_COLUMNA = 15
FICHAS_POR_AREA = 8


def _e(texto):
    return html.escape(str(texto), quote=True)


def _corto(texto, tope):
    """Un texto de hasta `tope` letras, cortado por palabras y con «…» si se cortó."""
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if len(texto) <= tope:
        return texto
    corte = texto[:tope - 1].rsplit(" ", 1)[0] if " " in texto[:tope - 1] else texto[:tope - 1]
    return corte.rstrip(" ,;:.") + "…"


def _slug(texto):
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80].strip("-")
    return t or "x"


class _Paginas:
    """Reparte nombres de página únicos y siempre iguales para los mismos datos."""

    def __init__(self):
        self.usadas = {"index.html"}
        self.de = {}

    def nombre(self, prefijo, clave, texto):
        if (prefijo, clave) not in self.de:
            base = "%s/%s" % (prefijo, _slug(texto))
            candidata, n = base + ".html", 2
            while candidata in self.usadas:
                candidata, n = "%s-%d.html" % (base, n), n + 1
            self.usadas.add(candidata)
            self.de[(prefijo, clave)] = candidata
        return self.de[(prefijo, clave)]


class _Armador:
    def __init__(self, atlas, cache, mermaid):
        self.a = atlas
        self.base = atlas["base"]
        self.cache = cache
        self.archivos, self.recursos, self.origen = recursos(mermaid)
        self.paginas = _Paginas()
        self.camino_doc = {}       # ruta del documento → camino hasta su página madre
        self.version = {}          # ruta de una versión → (pieza, eslabón)
        self.sin_resumen = set()
        self.vinculos = []
        self.vecinas = {}

    # ---- piezas de página ----

    def pag_area(self, area):
        return self.paginas.nombre("area", area["carpeta"], posixpath.basename(area["carpeta"]))

    def pag_proc(self, p):
        nombre = "%s-%s" % (posixpath.basename(p["area"]), posixpath.basename(p["carpeta"]))
        return self.paginas.nombre("proc", p["carpeta"], nombre)

    def resumen(self, ruta):
        doc = self.base.doc(ruta)
        guardado = resumenes.vigente(self.cache, ruta, doc["huella"])
        if guardado is not None:
            return guardado
        self.sin_resumen.add(ruta)
        return doc["primero"]

    def titulo_doc(self, ruta):
        return self.base.doc(ruta)["titulo"]

    def pagina_doc(self, ruta, camino):
        self.camino_doc.setdefault(ruta, camino)
        return self.paginas.nombre("doc", ruta, ruta[:-3])

    def camino(self, pasos, base):
        partes = []
        for texto, pagina in pasos[:-1]:
            partes.append('<a href="%s%s">%s</a>' % (base, _e(pagina), _e(texto)))
        partes.append('<span aria-current="page">%s</span>' % _e(pasos[-1][0]))
        return '<nav class="camino" aria-label="Camino">%s</nav>' % '<span class="sep" aria-hidden="true">›</span>'.join(
            partes)

    @staticmethod
    def sin_definir(ref):
        motivo = "falta el archivo" if ref["estado"] == "falta" else "el archivo está vacío"
        return ('<p class="sin-definir"><span class="pildora sin">Sin definir</span> <code>%s</code>'
                '<span class="suave"> · %s</span></p>' % (_e(ref["ruta"]), motivo))

    def ficha_doc(self, ref, camino, base, titulo=None):
        """Un documento como tarjeta: título con vínculo y resumen; o «sin definir» con su ruta."""
        if ref["estado"] != "ok":
            return '<div class="tarjeta">%s%s</div>' % ("<h3>%s</h3>" % _e(titulo) if titulo else "",
                                                       self.sin_definir(ref))
        pagina = self.pagina_doc(ref["ruta"], camino)
        return ('<div class="tarjeta"><h3><a href="%s%s">%s</a></h3><p>%s</p><p class="suave"><code>%s</code></p></div>'
                % (base, pagina, _e(titulo or self.titulo_doc(ref["ruta"])), en_linea(self.resumen(ref["ruta"])),
                   _e(ref["ruta"])))

    def lista_docs(self, refs, camino, base):
        filas = []
        for ref in refs:
            if ref["estado"] != "ok":
                filas.append("<li>%s</li>" % self.sin_definir(ref))
                continue
            pagina = self.pagina_doc(ref["ruta"], camino)
            resumen = self.resumen(ref["ruta"])
            filas.append('<li><a href="%s%s">%s</a> <span class="suave"><code>%s</code></span>%s</li>'
                         % (base, pagina, _e(self.titulo_doc(ref["ruta"])), _e(ref["ruta"]),
                            "<p>%s</p>" % en_linea(resumen) if resumen else ""))
        return '<ul class="docs">%s</ul>' % "".join(filas)

    def pagina(self, ruta, titulo, cuerpo, base):
        self.archivos[ruta] = _pagina(titulo, cuerpo, base, self.recursos, "", self.origen).encode("utf-8")

    def figura_mapa(self, m, donde, base):
        BLOQUES["mapa"](m, donde)
        self.vinculos.extend((donde, v) for v in enlaces(m))
        return _bloque(m, None, donde, [0, 0], base)

    # ---- el mapa de la casa ----

    def caja_doc(self, ident, ref, titulo, tope=220):
        if ref["estado"] != "ok":
            return {"id": ident, "fila": 1, "titulo": titulo, "estado": "apagado",
                    "texto": _corto("Sin definir · %s" % ref["ruta"], 300)}
        caja = {"id": ident, "fila": 1, "titulo": titulo,
                "enlace": self.pagina_doc(ref["ruta"], [(self.a["titulo"], "index.html"),
                                                         (self.titulo_doc(ref["ruta"]), None)])}
        texto = _corto(lector.plano(self.resumen(ref["ruta"])), tope)
        if texto:
            caja["texto"] = texto
        return caja

    def cajas_funciones(self):
        f = self.a["funciones"]
        if f is None:
            return []
        if f["estado"] != "ok" or not self.a["funciones_items"]:
            return [self.caja_doc("funciones", f, "Lo que sabe hacer")]
        enlace = self.pagina_doc(f["ruta"], [(self.a["titulo"], "index.html"), (self.titulo_doc(f["ruta"]), None)])
        cajas = []
        items = self.a["funciones_items"]
        for i, item in enumerate(items[:CAJAS_POR_COLUMNA - 1 if len(items) > CAJAS_POR_COLUMNA else None]):
            texto = lector.plano(item)
            m = re.match(r"^(.{2,80}?)(?::\s|\.\s|\s—\s|\s-\s)(.+)$", texto)
            caja = {"id": "f%d" % (i + 1), "fila": 1, "enlace": enlace,
                    "titulo": _corto(m.group(1) if m else texto, 80)}
            if m:
                caja["texto"] = _corto(m.group(2), 160)
            cajas.append(caja)
        if len(items) > len(cajas):
            cajas.append({"nota": "y %d más en el documento" % (len(items) - len(cajas)), "fila": 1})
        return cajas

    def mapa_areas(self):
        casa = [c for c in (self.caja_doc("orienta", self.a["orienta"], "Reglas de base") if self.a["orienta"] else None,
                            self.caja_doc("rutea", self.a["rutea"], "Ruteo") if self.a["rutea"] else None) if c]
        areas, flechas = [], []
        lista = self.a["areas"]
        tope = CAJAS_POR_COLUMNA - 1 if len(lista) > CAJAS_POR_COLUMNA else len(lista)
        for i, area in enumerate(lista[:tope]):
            reglas = area["reglas"]
            caja = {"id": "a%d" % (i + 1), "fila": 1, "titulo": _corto(area["nombre"], 80),
                    "enlace": self.pag_area(area)}
            if reglas and reglas["estado"] == "ok":
                texto = _corto(lector.plano(self.resumen(reglas["ruta"])), 160)
                if texto:
                    caja["texto"] = texto
            elif reglas:
                caja["texto"] = "Reglas: sin definir"
            fichas = []
            procs = area["procedimientos"]
            for p in procs[:FICHAS_POR_AREA - 1 if len(procs) > FICHAS_POR_AREA else None]:
                ficha = {"titulo": _corto(p["nombre"], 40),
                         "enlace": self.pag_proc(p)}
                if not p["documento"] or p["documento"]["estado"] != "ok":
                    ficha["estado"] = "apagado"
                fichas.append(ficha)
            if len(procs) > len(fichas):
                fichas.append({"titulo": "y %d más" % (len(procs) - len(fichas)), "estado": "apagado"})
            if not procs:
                fichas.append({"titulo": "Sin procedimientos", "estado": "apagado"})
            caja["dentro"] = fichas
            definido = (reglas and reglas["estado"] == "ok") or any(
                p["documento"] and p["documento"]["estado"] == "ok" for p in procs)
            if not definido:
                caja["estado"] = "apagado"
            areas.append(caja)
            if self.a["rutea"] and self.a["rutea"]["estado"] == "ok":
                flechas.append({"de": "rutea", "a": caja["id"]})
        if len(lista) > tope:
            areas.append({"nota": "y %d áreas más: ver la lista de abajo" % (len(lista) - tope), "fila": 1})
        if not lista:
            areas.append({"id": "sin-areas", "fila": 1, "titulo": "Sin áreas", "estado": "apagado",
                          "texto": "La carpeta de áreas no tiene ninguna."})
        columnas = []
        if casa:
            columnas.append({"titulo": "La casa", "items": casa})
        columnas.append({"titulo": "Áreas", "items": areas, "fondo": True})
        funciones = self.cajas_funciones()
        if funciones:
            columnas.append({"titulo": "El asistente", "items": funciones})
        m = {"tipo": "mapa", "columnas": columnas,
             "pie": "Cada caja lleva a su página. Las punteadas están sin definir: es lo que falta cargar."}
        if flechas:
            m["flechas"] = flechas
        return m

    def mapa_cadena(self):
        columnas, ids = [], {}
        for i, e in enumerate(self.a["eslabones"]):
            pagina = self.paginas.nombre("eslabon", e["carpeta"], e["carpeta"])
            items = []
            piezas = e["piezas"]
            tope = CAJAS_POR_COLUMNA - 1 if len(piezas) > CAJAS_POR_COLUMNA else len(piezas)
            for j, p in enumerate(piezas[:tope]):
                ident = "e%d-%d" % (i + 1, j + 1)
                ids[(i, j)] = ident
                caja = {"id": ident, "fila": 1, "titulo": _corto(p["nombre"], 80),
                        "enlace": "%s#pieza-%d" % (pagina, j + 1)}
                vigente = p["vigente"]
                if vigente is None:
                    caja["estado"] = "aviso"
                    caja["texto"] = "Ninguna versión figura como vigente en el índice."
                elif p["refs"][vigente]["estado"] != "ok":
                    caja["estado"] = "apagado"
                    caja["texto"] = "Sin definir"
                else:
                    texto = _corto(lector.plano(self.resumen(vigente)), 140)
                    if texto:
                        caja["texto"] = texto
                if len(p["versiones"]) > 1:
                    caja["dentro"] = [{"titulo": "%d versiones" % len(p["versiones"]), "estado": "apagado"}]
                items.append(caja)
            if len(piezas) > tope:
                items.append({"nota": "y %d más en la página del eslabón" % (len(piezas) - tope), "fila": 1})
            if not piezas:
                items.append({"id": "e%d-vacio" % (i + 1), "fila": 1, "titulo": "Sin definir", "estado": "apagado",
                              "texto": "La carpeta %s/ no tiene documentos." % e["carpeta"], "enlace": pagina})
            columnas.append({"titulo": e["titulo"], "items": items, "fondo": True})
        flechas = [{"de": ids[a], "a": ids[b]} for a, b in self.a["flechas"] if a in ids and b in ids]
        m = {"tipo": "mapa", "columnas": columnas,
             "pie": "Una columna por eslabón, en el orden de la cadena. Una flecha dice que una pieza enlaza a la otra."}
        if flechas:
            m["flechas"] = flechas
        return m

    def tarjetas_casa(self):
        casa = [("orienta", "Reglas de base"), ("rutea", "Ruteo"), ("funciones", "Lo que sabe hacer el asistente")]
        partes = []
        for clave, titulo in casa:
            ref = self.a[clave]
            if ref:
                partes.append(self.ficha_doc(ref, [(self.a["titulo"], "index.html"), (titulo, None)], "", titulo))
        return '<div class="tarjetas">%s</div>' % "".join(partes) if partes else ""

    def indice(self):
        a = self.a
        if a["forma"] == "areas":
            mapa = self.figura_mapa(self.mapa_areas(), "atlas.mapa", "")
            filas = []
            for area in a["areas"]:
                procs = "".join('<li><a href="%s">%s</a></li>'
                                % (self.pag_proc(p), _e(p["nombre"]))
                                for p in area["procedimientos"])
                filas.append('<li><a href="%s">%s</a>%s</li>'
                             % (self.pag_area(area), _e(area["nombre"]),
                                "<ul>%s</ul>" % procs if procs else ""))
            extra = ""
            titulo_lista = "Áreas y procedimientos"
        else:
            mapa = self.figura_mapa(self.mapa_cadena(), "atlas.mapa", "")
            filas = ['<li><a href="%s">%s</a> <span class="suave">· %d pieza(s)</span></li>'
                     % (self.paginas.nombre("eslabon", e["carpeta"], e["carpeta"]), _e(e["titulo"]), len(e["piezas"]))
                     for e in a["eslabones"]]
            extra = '<section id="casa"><h2>Reglas de base y asistente</h2>%s</section>' % self.tarjetas_casa()
            titulo_lista = "Eslabones"
        bajada = ""
        if a["orienta"] and a["orienta"]["estado"] == "ok":
            resumen = self.resumen(a["orienta"]["ruta"])
            if resumen:
                bajada = '<p class="bajada">%s</p>' % en_linea(resumen)
        cuerpo = ('<div class="hoja"><header class="portada"><span class="antetitulo">Atlas de la base</span>'
                  '<h1>%s</h1>%s</header><section id="mapa"><h2>El mapa de la casa</h2>%s</section>%s'
                  '<section id="lista"><h2>%s</h2><nav class="indice-atlas" aria-label="%s"><ul>%s</ul></nav></section>'
                  '</div>' % (_e(a["titulo"]), bajada, mapa, extra, titulo_lista, titulo_lista, "".join(filas)))
        self.pagina("index.html", a["titulo"], cuerpo, "")

    # ---- niveles 2 y 3 ----

    def areas(self):
        casa = (self.a["titulo"], "index.html")
        for area in self.a["areas"]:
            pagina = self.pag_area(area)
            camino = [casa, (area["nombre"], pagina)]
            partes = [self.camino(camino, "../"),
                      '<header class="portada"><span class="antetitulo">Área</span><h1>%s</h1>'
                      '<p class="suave"><code>%s/</code></p></header>' % (_e(area["nombre"]), _e(area["carpeta"]))]
            reglas = area["reglas"]
            if reglas:
                if reglas["estado"] == "ok":
                    doc = self.pagina_doc(reglas["ruta"], camino + [(self.titulo_doc(reglas["ruta"]), None)])
                    resumen = self.resumen(reglas["ruta"])
                    cuerpo_reglas = '%s<p><a class="ver" href="../%s">Ver las reglas completas</a></p>' % (
                        "<p>%s</p>" % en_linea(resumen) if resumen else "", doc)
                else:
                    cuerpo_reglas = self.sin_definir(reglas)
                partes.append('<section id="reglas"><h2>Reglas</h2>%s</section>' % cuerpo_reglas)
            tarjetas = []
            for p in area["procedimientos"]:
                pp = self.pag_proc(p)
                ref = p["documento"]
                if ref and ref["estado"] == "ok":
                    texto = "<p>%s</p>" % en_linea(self.resumen(ref["ruta"]))
                elif ref:
                    texto = self.sin_definir(ref)
                else:
                    texto = ""
                tarjetas.append('<div class="tarjeta"><h3><a href="../%s">%s</a></h3>%s</div>' % (pp, _e(p["nombre"]), texto))
            if tarjetas:
                procs = '<div class="tarjetas">%s</div>' % "".join(tarjetas)
            elif area["procedimientos_carpeta"]:
                procs = self.sin_definir({"ruta": area["procedimientos_carpeta"] + "/", "estado": "falta"})
            else:
                procs = '<p class="suave">Sin procedimientos.</p>'
            partes.append('<section id="procedimientos"><h2>Procedimientos</h2>%s</section>' % procs)
            if area["otros"]:
                partes.append('<section id="otros"><h2>Otros documentos</h2>%s</section>'
                              % self.lista_docs(area["otros"], camino, "../"))
            self.pagina(pagina, "%s · %s" % (area["nombre"], self.a["titulo"]),
                        '<div class="hoja nivel">%s</div>' % "".join(partes), "../")
            for p in area["procedimientos"]:
                self.procedimiento(area, p, camino)

    def procedimiento(self, area, p, camino_area):
        pagina = self.pag_proc(p)
        camino = camino_area + [(p["nombre"], pagina)]
        partes = [self.camino(camino, "../"),
                  '<header class="portada"><span class="antetitulo">Procedimiento · %s</span><h1>%s</h1>'
                  '<p class="suave"><code>%s/</code></p></header>' % (_e(area["nombre"]), _e(p["nombre"]), _e(p["carpeta"]))]
        ref = p["documento"]
        if ref and ref["estado"] == "vacio":
            partes.append(self.sin_definir(ref))
        if ref and ref["estado"] != "falta":
            if ref["estado"] == "ok":
                resumen = self.resumen(ref["ruta"])
                if resumen:
                    partes.append('<p class="intro">%s</p>' % en_linea(resumen))
            for clave, titulo in (("pasos", "Pasos"), ("lee", "De dónde lee"), ("deja", "Qué deja")):
                texto = p[clave]
                dentro = ('<div class="documento seccion-doc">%s</div>' % a_html(texto, base_titulos=2)
                          if texto else '<p class="sin-definir"><span class="pildora sin">Sin definir</span>'
                                        '<span class="suave"> · la sección está vacía o el documento no la tiene</span></p>')
                partes.append('<section id="%s"><h2>%s</h2>%s</section>' % (clave, titulo, dentro))
            if ref["estado"] == "ok":
                doc = self.pagina_doc(ref["ruta"], camino + [(self.titulo_doc(ref["ruta"]), None)])
                partes.append('<p><a class="ver" href="../%s">Ver el documento completo: <code>%s</code></a></p>'
                              % (doc, _e(ref["ruta"])))
        elif ref:
            partes.append('<section id="pasos"><h2>Pasos, de dónde lee y qué deja</h2>%s</section>'
                          % self.sin_definir(ref))
        if p["otros"]:
            partes.append('<section id="otros"><h2>Otros documentos</h2>%s</section>'
                          % self.lista_docs(p["otros"], camino, "../"))
        self.pagina(pagina, "%s · %s" % (p["nombre"], self.a["titulo"]),
                    '<div class="hoja nivel">%s</div>' % "".join(partes), "../")

    def eslabones(self):
        casa = (self.a["titulo"], "index.html")
        total = len(self.a["eslabones"])
        paginas = [self.paginas.nombre("eslabon", e["carpeta"], e["carpeta"]) for e in self.a["eslabones"]]
        vecinas = {}
        for a, b in self.a["flechas"]:
            vecinas.setdefault(a, []).append(b)
            vecinas.setdefault(b, []).append(a)
        self.vecinas = vecinas
        for i, e in enumerate(self.a["eslabones"]):
            camino = [casa, (e["titulo"], paginas[i])]
            partes = [self.camino(camino, "../"),
                      '<header class="portada"><span class="antetitulo">Eslabón %d de %d</span><h1>%s</h1>'
                      '<p class="suave"><code>%s/</code></p></header>' % (i + 1, total, _e(e["titulo"]), _e(e["carpeta"]))]
            if e["indice"]:
                if e["indice"]["estado"] == "ok":
                    doc = self.pagina_doc(e["indice"]["ruta"], camino + [(self.titulo_doc(e["indice"]["ruta"]), None)])
                    partes.append('<p>El índice que marca la versión vigente: <a href="../%s"><code>%s</code></a></p>'
                                  % (doc, _e(e["indice"]["ruta"])))
                else:
                    partes.append(self.sin_definir(e["indice"]))
            piezas = []
            for j, p in enumerate(e["piezas"]):
                piezas.append(self.pieza(i, j, p, camino, paginas))
            if not piezas:
                piezas.append(self.sin_definir({"ruta": e["carpeta"] + "/", "estado": "vacio"}))
            partes.append('<section id="piezas"><h2>Piezas</h2><div class="piezas">%s</div></section>' % "".join(piezas))
            self.pagina(paginas[i], "%s · %s" % (e["titulo"], self.a["titulo"]),
                        '<div class="hoja nivel">%s</div>' % "".join(partes), "../")

    def pieza(self, i, j, p, camino, paginas):
        partes = ['<h3>%s</h3>' % _e(p["nombre"])]
        vigente = p["vigente"]
        if vigente is None:
            partes.append('<p class="aviso-version">Ninguna versión figura como vigente en el índice.</p>')
        elif p["refs"][vigente]["estado"] != "ok":
            partes.append(self.sin_definir(p["refs"][vigente]))
        else:
            doc = self.pagina_doc(vigente, camino + [(self.titulo_doc(vigente), None)])
            self.version[vigente] = p
            resumen = self.resumen(vigente)
            partes.append('<p class="vigente"><span class="pildora vig">Vigente</span> <a href="../%s">%s</a> '
                          '<span class="suave"><code>%s</code></span></p>%s'
                          % (doc, _e(self.titulo_doc(vigente)), _e(vigente), "<p>%s</p>" % en_linea(resumen) if resumen else ""))
        relaciones = self.relaciones(i, j)
        if relaciones:
            partes.append('<p class="suave">Enlaza con: %s</p>' % ", ".join(relaciones))
        if p["antecedentes"]:
            filas = []
            for r in p["antecedentes"]:
                ref = p["refs"][r]
                self.version[r] = p
                if ref["estado"] != "ok":
                    filas.append("<li>%s</li>" % self.sin_definir(ref))
                    continue
                doc = self.pagina_doc(r, camino + [(self.titulo_doc(r), None)])
                filas.append('<li><a href="../%s">%s</a> <span class="suave"><code>%s</code></span></li>'
                             % (doc, _e(self.titulo_doc(r)), _e(r)))
            partes.append('<details class="antecedentes"><summary>Antecedentes (%d)</summary><ol>%s</ol></details>'
                          % (len(p["antecedentes"]), "".join(filas)))
        return '<article class="pieza" id="pieza-%d">%s</article>' % (j + 1, "".join(partes))

    def relaciones(self, i, j):
        """Las piezas de eslabones vecinos que enlazan con esta, para nombrarlas en su página."""
        salida = []
        for (k, l) in sorted(self.vecinas.get((i, j), [])):
            e = self.a["eslabones"][k]
            pagina = self.paginas.nombre("eslabon", e["carpeta"], e["carpeta"])
            salida.append('<a href="../%s#pieza-%d">%s · %s</a>' % (pagina, l + 1, _e(e["titulo"]),
                                                                     _e(e["piezas"][l]["nombre"])))
        return salida

    # ---- nivel 4 ----

    def documentos(self):
        hechos = set()
        while len(hechos) < len(self.camino_doc):
            for ruta in sorted(set(self.camino_doc) - hechos):
                hechos.add(ruta)
                self.documento(ruta)

    def documento(self, ruta):
        camino = self.camino_doc[ruta]
        pagina = self.pagina_doc(ruta, camino)
        doc = self.base.doc(ruta)
        aviso = ""
        p = self.version.get(ruta)
        if p is not None:
            if ruta != p["vigente"]:
                if p["vigente"] and p["refs"][p["vigente"]]["estado"] == "ok":
                    vig = self.pagina_doc(p["vigente"], camino)
                    aviso = ('<p class="aviso-version">Versión reemplazada. La vigente es <a href="../%s">%s</a> '
                             '(<code>%s</code>).</p>' % (vig, _e(self.titulo_doc(p["vigente"])), _e(p["vigente"])))
                else:
                    aviso = '<p class="aviso-version">Versión reemplazada. Ninguna versión figura como vigente en el índice.</p>'
            elif p["antecedentes"]:
                filas = []
                for r in p["antecedentes"]:
                    if p["refs"][r]["estado"] == "ok":
                        filas.append('<li><a href="../%s">%s</a> <span class="suave"><code>%s</code></span></li>'
                                     % (self.pagina_doc(r, camino), _e(self.titulo_doc(r)), _e(r)))
                    else:
                        filas.append("<li>%s</li>" % self.sin_definir(p["refs"][r]))
                aviso = ('<details class="antecedentes"><summary>Antecedentes (%d)</summary><ol>%s</ol></details>'
                         % (len(p["antecedentes"]), "".join(filas)))
        cuerpo = ('<div class="hoja documento-hoja">%s<header class="portada"><span class="antetitulo">Documento · %s'
                  '</span><h1>%s</h1>%s</header><article class="documento">%s</article></div>'
                  % (self.camino(camino, "../"), _e(self.a["titulo"]), _e(ruta), aviso, a_html(doc["texto"])))
        self.pagina(pagina, "%s · %s" % (lector.plano(doc["titulo"]), self.a["titulo"]), cuerpo, "../")

    def armar(self):
        # Primero los niveles 2 y 3: reparten los nombres de página y anotan qué documento cuelga de dónde.
        if self.a["forma"] == "areas":
            self.areas()
        else:
            self.eslabones()
        self.indice()
        self.documentos()
        comprobar_enlaces(self.vinculos, self.archivos)
        return self.archivos


def componer(atlas, cache=None, mermaid=None):
    """({ruta relativa: bytes} del sitio, rutas de los documentos sin resumen guardado). No escribe nada."""
    armador = _Armador(atlas, cache or {}, mermaid)
    archivos = armador.armar()
    return archivos, sorted(armador.sin_resumen)


def leer_forma(ruta):
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorDatos("falta la declaración de la forma: %s. Es un archivo JSON que dice qué carpeta es cada nivel, "
                         "cuál es el índice de vigentes y qué no se muestra (CONTRATO.md, «El atlas»)" % ruta)
    try:
        datos = json.loads(ruta.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ErrorDatos("no se pudo leer la forma %s: %s" % (ruta, error)) from None
    return forma_mod.validar(datos)


def pendientes(atlas, cache):
    """[(huella, ruta)] de los documentos cuyo resumen muestra el atlas y que no tienen uno vigente guardado."""
    _, sin_resumen = componer(atlas, cache)
    return [(atlas["base"].doc(ruta)["huella"], ruta) for ruta in sin_resumen]


def principal(argumentos):
    p = argparse.ArgumentParser(prog="informe atlas",
                                description="Arma el atlas de una base de conocimiento según su forma declarada.")
    p.add_argument("accion", choices=("validar", "armar", "pendientes", "incorporar"))
    p.add_argument("archivo", help="la forma (forma.json); con incorporar, el archivo de resúmenes nuevos")
    p.add_argument("--base", required=True, help="la carpeta de la base de conocimiento")
    p.add_argument("--salida", help="carpeta donde se escribe el atlas (solo armar)")
    p.add_argument("--recorte", help="qué salida de las declaradas en la forma")
    p.add_argument("--resumenes", help="el archivo de la caché de resúmenes")
    p.add_argument("--mermaid", help="copia local de la librería de diagramas, o una dirección https")
    p.add_argument("--json", action="store_true", help="pendientes: da la caché a llenar en lugar de la lista")
    a = p.parse_args(argumentos)
    prefijo = {"armar": "NO SE ARMÓ", "validar": "NO ES VÁLIDA"}.get(a.accion, "NO SE HIZO")
    try:
        if a.accion == "incorporar":
            if not a.resumenes:
                raise ErrorDatos("incorporar pide --resumenes: la caché donde se suman")
            sumados, salteados = resumenes.incorporar(a.archivo, a.base, a.resumenes)
            print("INCORPORADOS: %d resumen(es) en %s%s" % (sumados, a.resumenes,
                                                            "; %d vacío(s) salteado(s)" % salteados if salteados else ""))
            return 0
        forma = leer_forma(a.archivo)
        atlas = lector.leer(forma, a.base, a.recorte)
        if a.accion == "pendientes":
            if not a.resumenes:
                raise ErrorDatos("pendientes pide --resumenes: la caché contra la que se compara")
            lista = pendientes(atlas, resumenes.cargar(a.resumenes, debe_existir=False))
            if a.json:
                sys.stdout.write(resumenes.como_texto({r: {"huella": h, "resumen": ""} for h, r in lista}))
            else:
                for h, r in lista:
                    print("%s %s" % (h, r))
            return 0
        cache = resumenes.cargar(a.resumenes) if a.resumenes else {}
        archivos, sin_resumen = componer(atlas, cache, a.mermaid)
        paginas = sum(1 for r in archivos if r.endswith(".html"))
        if a.accion == "validar":
            print("VÁLIDA: forma %s, recorte %s, %d página(s), %d documento(s) sin resumen guardado"
                  % (forma["forma"], atlas["recorte"], paginas, len(sin_resumen)))
            return 0
        if not a.salida:
            raise ErrorDatos("armar pide --salida")
        escribir(archivos, a.salida)
        print("ARMADO: %d página(s) en %s, recorte %s" % (paginas, a.salida, atlas["recorte"]))
        if sin_resumen:
            print("Sin resumen guardado: %d documento(s); muestran su primer párrafo." % len(sin_resumen))
        print("Abrir: %s" % (Path(a.salida) / "index.html"))
        return 0
    except ErrorDatos as error:
        print("%s: %s" % (prefijo, error), file=sys.stderr)
        return 1
