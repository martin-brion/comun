"""Arma el sitio: una página de informe y una página por documento citado, con el pasaje señalado.

Todo se valida y se arma en memoria antes de escribir. Si una cita no está en su archivo, no se escribe nada.
"""
import hashlib
import html
import json
import re
from pathlib import Path

from . import VERSION
from .citas import CitaInvalida, ubicar
from .datos import ErrorDatos, validar
from .markdown import a_html, en_linea

MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
MANIFIESTO = ".informe.json"
TOPE_DOCUMENTO = 2 * 1024 * 1024
SIMBOLO = {"ok": "✓", "exc": "~", "dif": "≠", "na": "—"}
LEYENDA = (("ok", "sigue la regla"), ("exc", "distinto, pero válido en su etapa"),
           ("dif", "distinto y conviene corregir"), ("na", "no le corresponde todavía"))
ESTATICO = Path(__file__).resolve().parent / "estatico"


def _e(texto):
    return html.escape(str(texto), quote=True)


def _nombre_con_huella(nombre, contenido):
    base, _, ext = nombre.rpartition(".")
    return "%s.%s.%s" % (base, hashlib.sha256(contenido).hexdigest()[:10], ext)


class Documentos:
    """Los archivos citados: los lee una vez, ubica cada cita y reparte las anclas."""

    def __init__(self, raices):
        self.raices = {n: Path(r).resolve() for n, r in raices.items()}
        self.por_clave = {}
        self.orden = []
        self.paginas = set()

    def _abrir(self, clave, donde):
        if clave in self.por_clave:
            return self.por_clave[clave]
        raiz, _, relativa = clave.partition(":")
        if raiz not in self.raices:
            raise ErrorDatos('%s: la raíz "%s" no fue declarada con --raiz' % (donde, raiz))
        partes = Path(relativa.replace("\\", "/")).parts
        if not partes or Path(relativa).is_absolute() or any(p in ("..", "") or ":" in p for p in partes):
            raise ErrorDatos("%s: la ruta tiene que ser relativa a su raíz y no puede salir de ella" % donde)
        base = self.raices[raiz]
        ruta = base.joinpath(*partes)
        tramo = base
        for p in partes:
            tramo = tramo / p
            if tramo.is_symlink():
                raise ErrorDatos("%s: no se siguen enlaces" % donde)
        if not ruta.is_file() or not ruta.resolve().is_relative_to(base):
            raise ErrorDatos("%s: no existe el archivo %s" % (donde, clave))
        if ruta.stat().st_size > TOPE_DOCUMENTO:
            raise ErrorDatos("%s: el archivo pasa de %d bytes" % (donde, TOPE_DOCUMENTO))
        try:
            texto = ruta.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ErrorDatos("%s: el archivo no es texto UTF-8" % donde) from None
        if texto.startswith("﻿"):
            texto = texto[1:]
        pagina = re.sub(r"[^a-z0-9]+", "-", ("%s-%s" % (raiz, "/".join(partes))).lower()).strip("-") or "doc"
        candidata, n = pagina, 2
        while candidata in self.paginas:
            candidata, n = "%s-%d" % (pagina, n), n + 1
        self.paginas.add(candidata)
        doc = {"clave": clave, "raiz": raiz, "ruta": "/".join(partes), "texto": texto,
               "pagina": "doc/%s.html" % candidata, "citas": []}
        self.por_clave[clave] = doc
        self.orden.append(doc)
        return doc

    def citar(self, clave, cita, donde, hallazgo, parafrasis):
        doc = self._abrir(clave, donde)
        try:
            lugar = ubicar(doc["texto"], cita)
        except CitaInvalida as error:
            raise ErrorDatos("%s: %s" % (donde, error)) from None
        if lugar is None:
            raise ErrorDatos("%s: la cita no está en %s. Empieza con: «%s»" % (donde, clave, cita.strip()[:60]))
        ancla = "cita-%d" % (len(doc["citas"]) + 1)
        doc["citas"].append({"a": lugar[0], "b": lugar[1], "ancla": ancla, "hallazgo": hallazgo,
                             "parafrasis": parafrasis})
        return doc, lugar[0], lugar[1], ancla


def _lista(items):
    return "<ul>%s</ul>" % "".join("<li>%s</li>" % en_linea(i) for i in items)


def _diagrama(texto, titulo=None):
    cabeza = '<p class="diagrama-titulo">%s</p>' % en_linea(titulo) if titulo else ""
    return '%s<pre class="mermaid">%s</pre>' % (cabeza, html.escape(texto, quote=False))


def _figura(cuerpo, pie):
    return "<figure>%s%s</figure>" % (cuerpo, "<figcaption>%s</figcaption>" % en_linea(pie) if pie else "")


def _estado(estado):
    return '<span class="est %s">%s</span>' % (estado, SIMBOLO[estado])


def _matriz(b):
    cols = b["columnas"]
    cab = "<tr><th>Pieza</th>%s</tr>" % "".join('<th class="c">%s</th>' % _e(c["nombre"]) for c in cols)
    filas = []
    for f in b["filas"]:
        celdas = "".join('<td class="c" title="%s">%s</td>' % (_e(f["celdas"][c["id"]][1]), _estado(f["celdas"][c["id"]][0]))
                         for c in cols)
        filas.append("<tr><td>%s</td>%s</tr>" % (en_linea(f["pieza"]), celdas))
    leyenda = "".join("<span>%s %s</span>" % (_estado(e), _e(t)) for e, t in LEYENDA)
    fichas = []
    con_regla = any(f.get("regla") for f in b["filas"])
    for c in cols:
        renglones = []
        for f in b["filas"]:
            estado, texto = f["celdas"][c["id"]]
            regla = "<td>%s</td>" % en_linea(f.get("regla") or "") if con_regla else ""
            renglones.append("<tr><td>%s</td>%s<td>%s</td><td>%s</td></tr>"
                             % (en_linea(f["pieza"]), regla, en_linea(texto), _estado(estado)))
        nota = "<p class=\"suave\">%s</p>" % en_linea(c["nota"]) if c.get("nota") else ""
        fichas.append('<details class="ficha"><summary>Ficha de %s</summary>%s<div class="tabla"><table><thead><tr>'
                      "<th>Pieza</th>%s<th>Hoy</th><th></th></tr></thead><tbody>%s</tbody></table></div></details>"
                      % (_e(c["nombre"]), nota, "<th>Regla</th>" if con_regla else "", "".join(renglones)))
    return ('<div class="tabla"><table class="matriz"><thead>%s</thead><tbody>%s</tbody></table></div>'
            '<div class="leyenda">%s</div>%s' % (cab, "".join(filas), leyenda, "".join(fichas)))


def _hallazgos(b, docs, donde, cuenta):
    temas, tarjetas = [], []
    for i, h in enumerate(b["items"]):
        cuenta[0] += 1
        numero = cuenta[0]
        if h["tema"] not in temas:
            temas.append(h["tema"])
        evidencias = []
        for j, ev in enumerate(h["evidencia"]):
            partes = ["<p>%s</p>" % en_linea(ev["parafrasis"])]
            if "cita" in ev:
                doc, a, z, ancla = docs.citar(ev["archivo"], ev["cita"], "%s.items[%d].evidencia[%d]" % (donde, i, j),
                                              "%d · %s" % (numero, h["titulo"]), ev["parafrasis"])
                lineas = "línea %d" % a if a == z else "líneas %d a %d" % (a, z)
                partes.append("<blockquote>%s</blockquote>" % _e(ev["cita"].strip()))
                partes.append('<a class="ver" href="%s#%s">Ver en el documento: %s · %s, %s</a>'
                              % (_e(doc["pagina"]), ancla, _e(doc["raiz"]), _e(doc["ruta"]), lineas))
            if ev.get("fuente"):
                partes.append('<p class="suave">Fuente: %s</p>' % en_linea(ev["fuente"]))
            evidencias.append("<li>%s</li>" % "".join(partes))
        tarjetas.append(
            '<article class="hallazgo" id="hallazgo-%d" data-tema="%s"><div class="meta"><span class="pildora %s">%s</span>'
            '<span class="pildora tema">%s</span></div><h3>%s</h3><p>%s</p><details class="evidencia">'
            "<summary>Evidencia (%d)</summary><ul>%s</ul></details></article>"
            % (numero, _e(h["tema"]), h["severidad"], h["severidad"], _e(h["tema"]), en_linea(h["titulo"]),
               en_linea(h["texto"]), len(h["evidencia"]), "".join(evidencias)))
    filtros = ""
    if len(temas) > 1:
        botones = ['<button type="button" data-tema="" aria-pressed="true">Todos · %d</button>' % len(b["items"])]
        for t in temas:
            botones.append('<button type="button" data-tema="%s" aria-pressed="false">%s · %d</button>'
                           % (_e(t), _e(t), sum(1 for h in b["items"] if h["tema"] == t)))
        filtros = '<div class="filtros" role="group" aria-label="Filtrar por tema"><span class="rotulo">Tema:</span>%s</div>' \
                  % "".join(botones)
    return '<div class="grupo-hallazgos">%s<div class="hallazgos">%s</div></div>' % (filtros, "".join(tarjetas))


def _bloque(b, docs, donde, cuenta):
    t = b["tipo"]
    if t == "texto":
        return "".join("<p>%s</p>" % en_linea(p) for p in b["parrafos"])
    if t == "nota":
        return '<p class="nota">%s</p>' % en_linea(b["texto"])
    if t == "subtitulo":
        return "<h3>%s</h3>" % en_linea(b["texto"])
    if t == "codigo":
        return '<pre class="codigo"><code>%s</code></pre>' % html.escape(b["texto"], quote=False)
    if t == "veredicto":
        return '<div class="veredicto">%s</div>' % "".join(
            "<div><h3>%s</h3>%s</div>" % (en_linea(c["titulo"]), _lista(c["items"])) for c in b["columnas"])
    if t == "cifras":
        return '<div class="cifras">%s</div>' % "".join(
            '<div class="cifra"><b>%s</b><span>%s</span></div>' % (_e(c["valor"]), en_linea(c["texto"])) for c in b["items"])
    if t == "diagrama":
        return _figura(_diagrama(b["mermaid"], b.get("titulo")), b.get("pie"))
    if t == "antes_despues":
        lados = "".join('<div class="lado">%s</div>' % _diagrama(b[lado]["mermaid"], b[lado].get("titulo") or rotulo)
                        for lado, rotulo in (("antes", "Antes"), ("despues", "Después")))
        return _figura('<div class="antes-despues">%s</div>' % lados, b.get("pie"))
    if t == "matriz":
        return _matriz(b)
    if t == "desplegable":
        dentro = "\n".join(_bloque(h, docs, "%s.bloques[%d]" % (donde, j), cuenta) for j, h in enumerate(b["bloques"]))
        return '<details class="mas"><summary>%s</summary><div class="mas-dentro">%s</div></details>' \
               % (en_linea(b["titulo"]), dentro)
    if t == "hallazgos":
        return _hallazgos(b, docs, donde, cuenta)
    if t == "tarjetas":
        return '<div class="tarjetas">%s</div>' % "".join(
            '<div class="tarjeta"><h3>%s</h3><p>%s</p></div>' % (en_linea(c["titulo"]), en_linea(c["texto"]))
            for c in b["items"])
    if t == "pasos":
        return '<ol class="pasos">%s</ol>' % "".join("<li>%s</li>" % en_linea(i) for i in b["items"])
    if t == "fases":
        partes = []
        for c in b["items"]:
            cuando = '<span class="cuando">%s</span>' % en_linea(c["cuando"]) if c.get("cuando") else ""
            texto = "<p>%s</p>" % en_linea(c["texto"]) if c.get("texto") else ""
            partes.append('<div class="fase"><h3>%s</h3>%s%s%s</div>'
                          % (en_linea(c["titulo"]), cuando, texto, _lista(c["items"]) if c.get("items") else ""))
        return '<div class="fases">%s</div>' % "".join(partes)
    if t == "decisiones":
        filas = "".join('<tr><td class="id">%s</td><td>%s</td><td>%s</td></tr>'
                        % (_e(c["id"]), en_linea(c["decision"]), en_linea(c["recomendacion"])) for c in b["items"])
        return ('<div class="tabla"><table class="decisiones"><thead><tr><th>#</th><th>Decisión</th>'
                "<th>Recomendación</th></tr></thead><tbody>%s</tbody></table></div>" % filas)
    raise ErrorDatos("%s: bloque sin armador" % donde)


def _tema(d):
    tema = d.get("tema") or {}
    reglas, oscuro, enlace = [], [], ""
    if "acento" in tema:
        reglas.append("--acento:%s" % tema["acento"])
    if "acento_oscuro" in tema:
        oscuro.append("--acento:%s" % tema["acento_oscuro"])
    if "fuente_titulos" in tema:
        reglas.append("--f-titulos:%s,system-ui,sans-serif" % tema["fuente_titulos"])
    if "fuente_texto" in tema:
        reglas.append("--f-texto:%s,system-ui,sans-serif" % tema["fuente_texto"])
        reglas.append("--f-diagrama:%s,system-ui,sans-serif" % tema["fuente_texto"])
    if "fuentes_url" in tema:
        enlace = '<link rel="stylesheet" href="%s">' % _e(tema["fuentes_url"])
    estilo = ""
    if reglas:
        estilo += ":root{%s}" % ";".join(reglas)
    if oscuro:
        estilo += "@media (prefers-color-scheme:dark){:root{%s}}" % ";".join(oscuro)
    return enlace + ("<style>%s</style>" % estilo if estilo else "")


def _pagina(titulo, cuerpo, base, recursos, tema, mermaid):
    return ("<!doctype html>\n<html lang=\"es\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<meta name=\"generator\" content=\"informe %s\">\n<title>%s</title>\n"
            "<link rel=\"stylesheet\" href=\"%s%s\">\n%s\n</head>\n<body>\n%s\n"
            "<script src=\"%s\"></script>\n<script src=\"%s%s\"></script>\n<script src=\"%s%s\"></script>\n</body>\n</html>\n"
            % (VERSION, _e(titulo), base, recursos["estilo.css"], tema, cuerpo,
               _e(mermaid if "://" in mermaid else base + mermaid), base, recursos["visor.js"], base, recursos["pagina.js"]))


def componer(d, raices, mermaid=None):
    """Devuelve {ruta relativa: bytes} con el sitio entero. No escribe nada."""
    validar(d)
    docs = Documentos(raices)
    archivos, recursos = {}, {}
    for nombre in ("estilo.css", "visor.js", "pagina.js"):
        contenido = (ESTATICO / nombre).read_bytes()
        recursos[nombre] = "estatico/" + _nombre_con_huella(nombre, contenido)
        archivos[recursos[nombre]] = contenido
    origen = mermaid or MERMAID
    if "://" not in origen:
        contenido = Path(origen).read_bytes()
        origen = "estatico/" + _nombre_con_huella("mermaid.js", contenido)
        archivos[origen] = contenido
    elif not origen.startswith("https://"):
        raise ErrorDatos("--mermaid: una dirección tiene que ser https")
    tema = _tema(d)

    cuenta, secciones, menu = [0], [], []
    for i, s in enumerate(d["secciones"]):
        cuerpo = [_bloque(b, docs, "secciones[%d].bloques[%d] (%s)" % (i, j, b["tipo"]), cuenta)
                  for j, b in enumerate(s["bloques"])]
        intro = '<p class="intro">%s</p>' % en_linea(s["intro"]) if s.get("intro") else ""
        secciones.append('<section id="%s"><h2>%s</h2>%s%s</section>'
                         % (s["id"], en_linea(s["titulo"]), intro, "\n".join(cuerpo)))
        menu.append('<a href="#%s">%s</a>' % (s["id"], _e(s.get("menu") or s["titulo"])))
    cabeza = ['<header class="portada">']
    if d.get("antetitulo"):
        cabeza.append('<span class="antetitulo">%s</span>' % _e(d["antetitulo"]))
    cabeza.append("<h1>%s</h1>" % _e(d["titulo"]))
    if d.get("bajada"):
        cabeza.append('<p class="bajada">%s</p>' % en_linea(d["bajada"]))
    if d.get("fuentes"):
        cabeza.append('<p class="suave">%s</p>' % en_linea(d["fuentes"]))
    cabeza.append("</header>")
    indice = '<div class="hoja">%s<nav class="menu" aria-label="Secciones">%s</nav>%s</div>' \
             % ("".join(cabeza), "".join(menu), "\n".join(secciones))
    archivos["index.html"] = _pagina(d["titulo"], indice, "", recursos, tema, origen).encode("utf-8")

    for doc in docs.orden:
        rangos = [(c["a"], c["b"], c["ancla"]) for c in doc["citas"]]
        pasajes = "".join('<li><a href="#%s">%s</a><span class="suave"> · hallazgo %s</span></li>'
                          % (c["ancla"], en_linea(c["parafrasis"]), _e(c["hallazgo"])) for c in doc["citas"])
        cuerpo = ('<div class="hoja documento-hoja"><header class="portada"><a class="volver" href="../index.html">'
                  '← Volver al informe</a><span class="antetitulo">Documento fuente · %s</span><h1>%s</h1></header>'
                  '<aside class="pasajes"><h2>Pasajes señalados</h2><ol>%s</ol></aside>'
                  '<article class="documento">%s</article></div>'
                  % (_e(doc["raiz"]), _e(doc["ruta"]), pasajes, a_html(doc["texto"], rangos)))
        archivos[doc["pagina"]] = _pagina("%s · %s" % (doc["ruta"], d["titulo"]), cuerpo, "../", recursos, tema,
                                          origen).encode("utf-8")
    return archivos


def armar(d, raices, salida, mermaid=None):
    """Escribe el sitio en `salida`. Solo pisa una carpeta vacía o una que ya era de un informe."""
    archivos = componer(d, raices, mermaid)
    salida = Path(salida)
    previos = []
    if salida.exists():
        if not salida.is_dir():
            raise ErrorDatos("la salida no es una carpeta: %s" % salida)
        manifiesto = salida / MANIFIESTO
        if manifiesto.is_file():
            previos = json.loads(manifiesto.read_text(encoding="utf-8")).get("archivos", [])
        elif any(salida.iterdir()):
            raise ErrorDatos("la carpeta de salida no está vacía y no es de un informe: %s" % salida)
    for relativo in previos:
        partes = Path(relativo).parts
        if isinstance(relativo, str) and partes and ".." not in partes and not Path(relativo).is_absolute():
            viejo = salida.joinpath(*partes)
            if viejo.is_file() and not viejo.is_symlink():
                viejo.unlink()
    for relativo, contenido in sorted(archivos.items()):
        destino = salida.joinpath(*relativo.split("/"))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
    (salida / MANIFIESTO).write_text(
        json.dumps({"version": VERSION, "archivos": sorted(archivos)}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    return sorted(archivos)
