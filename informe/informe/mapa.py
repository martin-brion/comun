"""Dibuja un mapa por columnas: cajas en una grilla, flechas entre columnas vecinas y vistas para elegir qué se mira.

El dibujo sale hecho al armar el sitio, como SVG. No usa ninguna librería: la posición de cada caja la da su columna y
su fila, y por eso el mismo dato da siempre el mismo dibujo. Los colores vienen de las variables de la hoja de estilos.
"""
import html

ANCHO = 992            # ancho al que apunta el dibujo: entra en la hoja sin achicarse
MARGEN = 18
SEPARACION = 112       # espacio entre columnas: ahí van las flechas y sus etiquetas
ALTO_CABEZA = 32
ALTO_BANDA = 46
RELLENO = 12
ALTO_TITULO = 19
ALTO_TEXTO = 16
ALTO_FICHA = 24
ENTRE_ITEMS = 10
ENTRE_FILAS = 28
CAR_TITULO = 7.3       # ancho medio de una letra, en píxeles, para partir renglones sin medir en un navegador
CAR_TEXTO = 6.3
CAR_FICHA = 6.1
CAR_ETIQUETA = 6.0
TODAS = "Todas"


def _e(texto):
    return html.escape(str(texto), quote=True)


def _partir(texto, maximo):
    """Parte un texto en renglones de hasta `maximo` letras, cortando por palabras."""
    maximo = max(4, int(maximo))
    renglones = []
    for parrafo in str(texto).split("\n"):
        actual = ""
        for palabra in parrafo.split():
            while len(palabra) > maximo:
                if actual:
                    renglones.append(actual)
                    actual = ""
                renglones.append(palabra[:maximo])
                palabra = palabra[maximo:]
            if not actual:
                actual = palabra
            elif len(actual) + 1 + len(palabra) <= maximo:
                actual += " " + palabra
            else:
                renglones.append(actual)
                actual = palabra
        if actual:
            renglones.append(actual)
    return renglones or [""]


def _vista(vistas):
    """El atributo que dice en qué vistas está un elemento. Sin lista, está en todas."""
    if vistas is None:
        return ""
    return ' data-v="%s"' % _e(" ".join(vistas) if vistas else "-")


def _medir_caja(caja, ancho):
    interior = ancho - 2 * RELLENO
    titulos = _partir(caja["titulo"], interior / CAR_TITULO)
    textos = _partir(caja["texto"], interior / CAR_TEXTO) if caja.get("texto") else []
    fichas, x, y = [], 0.0, 0.0
    for ficha in caja.get("dentro") or []:
        w = min(interior, len(ficha["titulo"]) * CAR_FICHA + 16)
        if x and x + w > interior:
            x, y = 0.0, y + ALTO_FICHA + 6
        fichas.append((ficha, x, y, w))
        x += w + 6
    alto_fichas = (y + ALTO_FICHA) if fichas else 0
    alto = (RELLENO - 2) * 2 + len(titulos) * ALTO_TITULO
    if textos:
        alto += 2 + len(textos) * ALTO_TEXTO
    if fichas:
        alto += 8 + alto_fichas
    return {"titulos": titulos, "textos": textos, "fichas": fichas, "alto": alto}


def _anclas(flechas, cajas):
    """Reparte a lo largo de cada lado de cada caja las flechas que salen o llegan por ahí."""
    lados = {}
    for f in flechas:
        a, b = cajas[f["de"]], cajas[f["a"]]
        if a["col"] == b["col"]:
            f["tipo"] = "vertical"
            la, lb = ("abajo", "arriba") if b["y"] > a["y"] else ("arriba", "abajo")
        else:
            f["tipo"] = "horizontal"
            la, lb = ("der", "izq") if b["col"] > a["col"] else ("izq", "der")
        lados.setdefault((f["de"], la), []).append((f, "p1", b))
        lados.setdefault((f["a"], lb), []).append((f, "p2", a))
    for (nombre, lado), lista in lados.items():
        caja = cajas[nombre]
        vertical = lado in ("arriba", "abajo")
        lista.sort(key=lambda t: (t[2]["x"] + t[2]["w"] / 2) if vertical else (t[2]["y"] + t[2]["h"] / 2))
        for k, (f, punta, _otra) in enumerate(lista):
            fraccion = (k + 1) / (len(lista) + 1)
            if vertical:
                f[punta] = (caja["x"] + caja["w"] * fraccion, caja["y"] + (caja["h"] if lado == "abajo" else 0))
            else:
                f[punta] = (caja["x"] + (caja["w"] if lado == "der" else 0), caja["y"] + caja["h"] * fraccion)


def _vinculo(dibujo, enlace, base, titulo):
    """Envuelve un dibujo en un vínculo a otra página del sitio, si lo tiene."""
    if not enlace:
        return dibujo
    return '<a class="mp-vinculo" href="%s" aria-label="%s">%s</a>' % (_e(base + enlace), _e(titulo), dibujo)


def enlaces(b):
    """Los vínculos que lleva un bloque `mapa`, para comprobar que cada uno tiene su página."""
    for c in b["columnas"]:
        for item in c["items"]:
            if item.get("enlace"):
                yield item["enlace"]
            for ficha in item.get("dentro") or []:
                if ficha.get("enlace"):
                    yield ficha["enlace"]


def dibujar(b, numero, base=""):
    """Devuelve la figura completa (botones de vista, dibujo y pie) para un bloque `mapa`.

    `base` es lo que hay que anteponer a un vínculo para llegar a la raíz del sitio desde la página que lo muestra."""
    columnas = b["columnas"]
    n = len(columnas)
    ancho_col = max(170.0, min(320.0, (ANCHO - 2 * MARGEN - (n - 1) * SEPARACION) / n))
    ancho = 2 * MARGEN + n * ancho_col + (n - 1) * SEPARACION
    y0 = MARGEN + (ALTO_BANDA if b.get("titulo") else 0) + ALTO_CABEZA

    # 1. Medir cada cosa y saber cuánto mide cada fila.
    filas = sorted({item["fila"] + k for c in columnas for item in c["items"] for k in range(item.get("filas", 1))})
    alto_fila = dict.fromkeys(filas, 0.0)
    medidas, largas = {}, []
    for ci, c in enumerate(columnas):
        for fila in filas:
            alto = 0.0
            for ii, item in enumerate(c["items"]):
                if item["fila"] != fila:
                    continue
                if "nota" in item:
                    m = {"renglones": _partir(item["nota"], ancho_col / CAR_TEXTO)}
                    m["alto"] = len(m["renglones"]) * ALTO_TEXTO + 2
                else:
                    m = _medir_caja(item, ancho_col)
                medidas[(ci, ii)] = m
                if item.get("filas", 1) > 1:
                    largas.append((item, m))
                    continue
                alto += (ENTRE_ITEMS if alto else 0) + m["alto"]
            alto_fila[fila] = max(alto_fila[fila], alto)
    # Una caja que ocupa varias filas se estira hasta el final de la última; si no le alcanza, esa fila crece.
    for item, m in largas:
        abarca = [item["fila"] + k for k in range(item["filas"])]
        disponible = sum(alto_fila[f] for f in abarca) + ENTRE_FILAS * (len(abarca) - 1)
        if m["alto"] > disponible:
            alto_fila[abarca[-1]] += m["alto"] - disponible
        else:
            m["alto"] = disponible
    techo, y = {}, y0
    for fila in filas:
        techo[fila] = y
        y += alto_fila[fila] + ENTRE_FILAS
    alto = y - ENTRE_FILAS + MARGEN

    # 2. Ubicar.
    cajas, dibujo_cajas, dibujo_notas, fondos = {}, [], [], []
    for ci, c in enumerate(columnas):
        x = MARGEN + ci * (ancho_col + SEPARACION)
        if c.get("fondo"):
            fondos.append('<rect class="mp-fondo" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10"/>'
                          % (x - 10, y0 - 8, ancho_col + 20, alto - MARGEN - y0 + 16))
        cursor = {}
        for ii, item in enumerate(c["items"]):
            m = medidas[(ci, ii)]
            y_item = cursor.get(item["fila"], techo[item["fila"]])
            cursor[item["fila"]] = y_item + m["alto"] + ENTRE_ITEMS
            if "nota" in item:
                renglones = "".join('<text class="mp-nota" x="%.1f" y="%.1f">%s</text>'
                                    % (x, y_item + 12 + k * ALTO_TEXTO, _e(r)) for k, r in enumerate(m["renglones"]))
                dibujo_notas.append("<g%s>%s</g>" % (_vista(item.get("vistas")), renglones))
                continue
            cajas[item["id"]] = {"col": ci, "x": x, "y": y_item, "w": ancho_col, "h": m["alto"],
                                 "vistas": item.get("vistas")}
            partes = ['<rect class="mp-caja" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="7"/>'
                      % (x, y_item, ancho_col, m["alto"])]
            yt = y_item + RELLENO - 2
            for r in m["titulos"]:
                partes.append('<text class="mp-titulo" x="%.1f" y="%.1f">%s</text>' % (x + RELLENO, yt + 14, _e(r)))
                yt += ALTO_TITULO
            if m["textos"]:
                yt += 2
                for r in m["textos"]:
                    partes.append('<text class="mp-texto" x="%.1f" y="%.1f">%s</text>' % (x + RELLENO, yt + 12, _e(r)))
                    yt += ALTO_TEXTO
            dibujo_cajas.append(_vinculo('<g class="mp-%s"%s>%s</g>' % (item.get("estado", "normal"),
                                                                       _vista(item.get("vistas")), "".join(partes)),
                                         item.get("enlace"), base, item["titulo"]))
            if m["fichas"]:
                yt += 8
                for ficha, fx, fy, fw in m["fichas"]:
                    dibujo_cajas.append(_vinculo(
                        '<g class="mp-ficha mp-%s"%s><rect class="mp-caja" x="%.1f" y="%.1f" width="%.1f" height="%d" rx="5"/>'
                        '<text class="mp-texto" x="%.1f" y="%.1f" text-anchor="middle">%s</text></g>'
                        % (ficha.get("estado", "normal"), _vista(ficha.get("vistas", item.get("vistas"))),
                           x + RELLENO + fx, yt + fy, fw, ALTO_FICHA, x + RELLENO + fx + fw / 2, yt + fy + 16,
                           _e(ficha["titulo"])), ficha.get("enlace"), base, ficha["titulo"]))

    # 3. Flechas: entre columnas vecinas, con un codo en el espacio que las separa; o derecho hacia abajo.
    flechas = [dict(f) for f in b.get("flechas") or []]
    _anclas(flechas, cajas)
    # Si las dos cajas se enfrentan, la flecha va derecha: las dos puntas a la misma altura, dentro del tramo común.
    ocupadas = {}
    for f in flechas:
        if f["tipo"] != "horizontal":
            continue
        a, z = cajas[f["de"]], cajas[f["a"]]
        bajo, alto_comun = max(a["y"], z["y"]) + 12, min(a["y"] + a["h"], z["y"] + z["h"]) - 12
        if bajo > alto_comun:
            continue
        y = (min(max(f["p1"][1], bajo), alto_comun) + min(max(f["p2"][1], bajo), alto_comun)) / 2
        hueco = min(a["col"], z["col"])
        while any(abs(y - otra) < 14 for otra in ocupadas.get(hueco, [])) and y + 14 <= alto_comun:
            y += 14
        ocupadas.setdefault(hueco, []).append(y)
        f["p1"], f["p2"] = (f["p1"][0], y), (f["p2"][0], y)
    codos = {}
    for f in flechas:
        if f["tipo"] == "horizontal" and abs(f["p1"][1] - f["p2"][1]) > 6:
            codos.setdefault(min(cajas[f["de"]]["col"], cajas[f["a"]]["col"]), []).append(f)
    for hueco, lista in codos.items():
        lista.sort(key=lambda f: f["p1"][1] + f["p2"][1])
        x_hueco = MARGEN + hueco * (ancho_col + SEPARACION) + ancho_col
        for k, f in enumerate(lista):
            f["xm"] = x_hueco + SEPARACION * (k + 1) / (len(lista) + 1)
    dibujo_flechas, dibujo_etiquetas = [], []
    for f in flechas:
        (x1, y1), (x2, y2) = f["p1"], f["p2"]
        etiqueta = f.get("texto")
        if f["tipo"] == "vertical":
            camino = "M%.1f %.1f L%.1f %.1f" % (x1, y1, x2, y2)
            lugar = (x1 + 8, (y1 + y2) / 2 + 4, "start", False)
        elif "xm" not in f:
            ym = (y1 + y2) / 2
            camino = "M%.1f %.1f L%.1f %.1f" % (x1, ym, x2, ym)
            lugar = ((x1 + x2) / 2, ym - 6, "middle", False)
        else:
            camino = "M%.1f %.1f L%.1f %.1f L%.1f %.1f L%.1f %.1f" % (x1, y1, f["xm"], y1, f["xm"], y2, x2, y2)
            lugar = (f["xm"], (y1 + y2) / 2 + 4, "middle", True)
        if "vistas" in f:
            vistas = f["vistas"]
        else:
            va, vb = cajas[f["de"]]["vistas"], cajas[f["a"]]["vistas"]
            vistas = vb if va is None else va if vb is None else [v for v in va if v in vb]
        dibujo_flechas.append('<path class="mp-flecha"%s d="%s" marker-end="url(#mp-punta-%d)"/>'
                              % (_vista(vistas), camino, numero))
        if etiqueta:
            x, y, alineado, con_fondo = lugar
            fondo = ""
            if con_fondo:
                w = len(etiqueta) * CAR_ETIQUETA + 10
                fondo = '<rect class="mp-etiqueta-fondo" x="%.1f" y="%.1f" width="%.1f" height="16" rx="3"/>' % (x - w / 2, y - 12, w)
            dibujo_etiquetas.append('<g%s>%s<text class="mp-etiqueta" x="%.1f" y="%.1f" text-anchor="%s">%s</text></g>'
                                    % (_vista(vistas), fondo, x, y, alineado, _e(etiqueta)))

    # 4. Cabeza y armado.
    cabeza = []
    if b.get("titulo"):
        cabeza.append('<rect class="mp-fondo" x="%d" y="%d" width="%.1f" height="34" rx="7"/>' % (MARGEN - 10, MARGEN - 6, ancho - 2 * MARGEN + 20))
        cabeza.append('<text class="mp-titulo" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (ancho / 2, MARGEN + 16, _e(b["titulo"])))
    for ci, c in enumerate(columnas):
        centro = MARGEN + ci * (ancho_col + SEPARACION) + ancho_col / 2
        cabeza.append('<text class="mp-columna" x="%.1f" y="%.1f" text-anchor="middle">%s</text>' % (centro, y0 - 16, _e(c["titulo"])))
    descripcion = b.get("pie") or b.get("titulo") or "Mapa por columnas"
    svg = ('<svg class="mp" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="%s">'
           '<defs><marker id="mp-punta-%d" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
           '<path class="mp-punta" d="M0 0 L10 5 L0 10 z"/></marker></defs>%s%s%s%s%s%s</svg>'
           % (round(ancho), round(alto), round(ancho), round(alto), _e(descripcion), numero, "".join(fondos), "".join(cabeza),
              "".join(dibujo_flechas), "".join(dibujo_cajas), "".join(dibujo_notas), "".join(dibujo_etiquetas)))

    botones = ""
    if b.get("vistas"):
        lista = ['<button type="button" data-vista="" aria-pressed="true">%s</button>' % TODAS]
        lista += ['<button type="button" data-vista="%s" aria-pressed="false">%s</button>' % (_e(v["id"]), _e(v["nombre"]))
                  for v in b["vistas"]]
        botones = '<div class="filtros vistas" role="group" aria-label="Elegir qué se mira"><span class="rotulo">Ver:</span>%s</div>' \
                  % "".join(lista)
    return (botones, '<div class="mapa-dibujo">%s</div>' % svg)
