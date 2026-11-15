"""Markdown a HTML: lo justo para leer un documento de trabajo, sabiendo de qué líneas sale cada bloque.

Solo biblioteca estándar. No busca cubrir todo Markdown: títulos, párrafos, listas, tablas, citas, bloques de código,
reglas y el formato dentro de la línea. Lo que no reconoce queda como texto. Todo el texto de entrada se escapa.
"""
import html
import re

_CERCA = re.compile(r"^(\s*)(```+|~~~+)\s*([\w-]*)\s*$")
_TITULO = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")
_REGLA = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_SEPARADOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")

_CODIGO = re.compile(r"(`+)(.+?)\1")
_ENLACE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_NEGRITA = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_CURSIVA = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_TACHADO = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_GUARDADO = re.compile("\x00(\\d+)\x00")
TOPE_LINEA = 20000


def en_linea(texto):
    """Formato dentro de una línea: código, negrita, cursiva, tachado y enlaces http. El resto, escapado."""
    guardados = []

    def guardar(m):
        guardados.append("<code>%s</code>" % html.escape(m.group(2).strip(), quote=True))
        return "\x00%d\x00" % (len(guardados) - 1)

    def enlace(m):
        if "\x00" in m.group(2):              # un tramo de código dentro del destino: no es un enlace
            return m.group(0)
        destino = html.unescape(m.group(2))
        if re.match(r"^https?://", destino):
            return '<a href="%s" rel="noopener noreferrer">%s</a>' % (html.escape(destino, quote=True), m.group(1))
        return '%s <span class="ruta">(%s)</span>' % (m.group(1), m.group(2))

    texto = texto.replace("\x00", "")
    if len(texto) > TOPE_LINEA:               # una línea desmedida va como texto, sin buscarle formato
        return html.escape(texto, quote=True)
    t = _CODIGO.sub(guardar, texto)
    t = html.escape(t, quote=True)
    t = _ENLACE.sub(enlace, t)
    t = _NEGRITA.sub(r"<strong>\1</strong>", t)
    t = _CURSIVA.sub(r"<em>\1</em>", t)
    t = _TACHADO.sub(r"<del>\1</del>", t)
    return _GUARDADO.sub(lambda m: guardados[int(m.group(1))], t)


def _celdas(linea):
    """Parte una fila de tabla por las barras, sin cortar dentro de un tramo de código."""
    t = linea.strip()
    if t.startswith("|"):
        t = t[1:]
    celdas, actual, en_codigo, i = [], [], False, 0
    while i < len(t):
        c = t[i]
        if c == "\\" and i + 1 < len(t) and t[i + 1] == "|":
            actual.append("|")
            i += 2
            continue
        if c == "`":
            en_codigo = not en_codigo
        if c == "|" and not en_codigo:
            celdas.append("".join(actual).strip())
            actual = []
        else:
            actual.append(c)
        i += 1
    resto = "".join(actual).strip()
    if resto:
        celdas.append(resto)
    return celdas


def _es_separador(linea):
    return bool(_SEPARADOR.match(linea.strip()))


def _titulo(linea):
    """(nivel, texto) si la línea es un título; si no, None. Sin expresiones que puedan atascarse."""
    m = _TITULO.match(linea)
    if not m:
        return None
    texto = m.group(2).rstrip()
    sin_cierre = texto.rstrip("#")
    if sin_cierre != texto and (not sin_cierre or sin_cierre[-1] in " \t"):
        texto = sin_cierre.rstrip()
    return len(m.group(1)), texto


def sin_comentarios(texto):
    """Quita los comentarios HTML fuera de los bloques de código, sin mover ninguna línea de lugar."""
    salida, cierre, en_comentario = [], None, False
    for linea in texto.split("\n"):
        if cierre is not None:
            salida.append(linea)
            if cierre.match(linea):
                cierre = None
            continue
        if not en_comentario:
            m = _CERCA.match(linea)
            if m:
                cierre = re.compile(r"^\s*%s{%d,}\s*$" % (re.escape(m.group(2)[0]), len(m.group(2))))
                salida.append(linea)
                continue
        resto, visible = linea, []
        while True:
            if en_comentario:
                fin = resto.find("-->")
                if fin < 0:
                    break                      # sigue abierto: el resto de la línea no se muestra
                resto, en_comentario = resto[fin + 3:], False
            inicio = resto.find("<!--")
            if inicio < 0:
                visible.append(resto)
                break
            visible.append(resto[:inicio])
            resto, en_comentario = resto[inicio + 4:], True
        salida.append("".join(visible))
    return "\n".join(salida)


def _corta_parrafo(lineas, j):
    l = lineas[j]
    if not l.strip():
        return True
    if _CERCA.match(l) or _titulo(l) or _REGLA.match(l) or _ITEM.match(l):
        return True
    s = l.lstrip()
    if s.startswith(">"):
        return True
    return s.startswith("|") and j + 1 < len(lineas) and _es_separador(lineas[j + 1])


def _lista(lineas, i, despl):
    n, items, j = len(lineas), [], i
    while j < n:
        l = lineas[j]
        m = _ITEM.match(l)
        if m and not _REGLA.match(l):
            items.append({"sangria": len(m.group(1).expandtabs(4)), "ord": m.group(2)[0].isdigit(),
                          "lineas": [m.group(3)], "a": j + 1 + despl, "b": j + 1 + despl})
            j += 1
            continue
        if not l.strip():
            k = j + 1
            while k < n and not lineas[k].strip():
                k += 1
            if k < n and (_ITEM.match(lineas[k]) or lineas[k][:1] in (" ", "\t")):
                j = k
                continue
            break
        if l[:1] not in (" ", "\t") and (_CERCA.match(l) or _titulo(l) or _REGLA.match(l) or l.startswith((">", "|"))):
            break
        items[-1]["lineas"].append(l.strip())
        items[-1]["b"] = j + 1 + despl
        j += 1
    return {"t": "lista", "a": i + 1 + despl, "b": items[-1]["b"], "items": items}, j


def bloques(texto, despl=0):
    """Lista de bloques con sus líneas de origen (`a` y `b`, desde 1)."""
    lineas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i, n, salida = 0, len(lineas), []
    while i < n:
        l = lineas[i]
        if not l.strip():
            i += 1
            continue
        m = _CERCA.match(l)
        if m:
            marca, cuerpo, j = m.group(2), [], i + 1
            cierre = re.compile(r"^\s*%s{%d,}\s*$" % (re.escape(marca[0]), len(marca)))
            while j < n and not cierre.match(lineas[j]):
                cuerpo.append(lineas[j])
                j += 1
            salida.append({"t": "pre", "a": i + 1 + despl, "b": min(j + 1, n) + despl, "leng": m.group(3),
                           "texto": "\n".join(cuerpo)})
            i = j + 1
            continue
        titulo = _titulo(l)
        if titulo:
            salida.append({"t": "h", "a": i + 1 + despl, "b": i + 1 + despl, "nivel": titulo[0], "texto": titulo[1]})
            i += 1
            continue
        if _REGLA.match(l):
            salida.append({"t": "hr", "a": i + 1 + despl, "b": i + 1 + despl})
            i += 1
            continue
        if l.lstrip().startswith("|") and i + 1 < n and _es_separador(lineas[i + 1]):
            j = i + 2
            while j < n and lineas[j].lstrip().startswith("|"):
                j += 1
            salida.append({"t": "tabla", "a": i + 1 + despl, "b": j + despl, "cab": _celdas(l),
                           "filas": [(k + 1 + despl, _celdas(lineas[k])) for k in range(i + 2, j)]})
            i = j
            continue
        if l.lstrip().startswith(">"):
            j, cuerpo = i, []
            while j < n and lineas[j].lstrip().startswith(">"):
                cuerpo.append(re.sub(r"^\s*>\s?", "", lineas[j]))
                j += 1
            salida.append({"t": "cita", "a": i + 1 + despl, "b": j + despl,
                           "dentro": bloques("\n".join(cuerpo), i + despl)})
            i = j
            continue
        if _ITEM.match(l):
            bloque, i = _lista(lineas, i, despl)
            salida.append(bloque)
            continue
        j, cuerpo = i, []
        while j < n and (j == i or not _corta_parrafo(lineas, j)):
            cuerpo.append(lineas[j].strip())
            j += 1
        salida.append({"t": "p", "a": i + 1 + despl, "b": j + despl, "texto": " ".join(cuerpo)})
        i = j
    return salida


class _Marcas:
    """Decide qué bloques caen dentro de un pasaje señalado y dónde va el ancla de cada pasaje."""

    def __init__(self, rangos):
        self.rangos = [(int(a), int(b), str(ancla)) for a, b, ancla in rangos]
        self.usadas = set()

    def atributos(self, a, b, clases=()):
        """(atributos del bloque, anclas de más). La primera cita nueva da el id; cada una de las demás, un ancla vacía."""
        clases, anclas = list(clases), []
        for ra, rb, nombre in self.rangos:
            if a <= rb and b >= ra:
                if "resaltado" not in clases:
                    clases.append("resaltado")
                if nombre not in self.usadas:
                    anclas.append(nombre)
                    self.usadas.add(nombre)
        partes = []
        if anclas:
            partes.append(' id="%s"' % html.escape(anclas[0], quote=True))
        if clases:
            partes.append(' class="%s"' % " ".join(clases))
        extra = "".join('<span class="ancla" id="%s"></span>' % html.escape(n, quote=True) for n in anclas[1:])
        return "".join(partes), extra


def _html_lista(items, marcas):
    out, pila = [], []
    for it in items:
        while pila and it["sangria"] < pila[-1][0]:
            out.append("</li></%s>" % pila.pop()[1])
        if not pila or it["sangria"] > pila[-1][0]:
            etiqueta = "ol" if it["ord"] else "ul"
            out.append("<%s>" % etiqueta)
            pila.append((it["sangria"], etiqueta))
        else:
            out.append("</li>")
        atributos, extra = marcas.atributos(it["a"], it["b"])
        out.append("<li%s>%s%s" % (atributos, extra, en_linea(" ".join(it["lineas"]))))
    while pila:
        out.append("</li></%s>" % pila.pop()[1])
    return "".join(out)


def _html_fila(marcas, linea, celdas, etiqueta):
    atributos, extra = marcas.atributos(linea, linea)
    dentro = [en_linea(c) for c in celdas] or [""]
    dentro[0] = extra + dentro[0]
    return "<tr%s>%s</tr>" % (atributos, "".join("<%s>%s</%s>" % (etiqueta, c, etiqueta) for c in dentro))


def _html(lista, marcas, base_titulos):
    out = []
    for b in lista:
        t = b["t"]
        if t == "p":
            atributos, extra = marcas.atributos(b["a"], b["b"])
            out.append("<p%s>%s%s</p>" % (atributos, extra, en_linea(b["texto"])))
        elif t == "h":
            nivel = min(6, b["nivel"] + base_titulos)
            atributos, extra = marcas.atributos(b["a"], b["b"])
            out.append("<h%d%s>%s%s</h%d>" % (nivel, atributos, extra, en_linea(b["texto"]), nivel))
        elif t == "hr":
            out.append("<hr>")
        elif t == "pre":
            es_diagrama = b["leng"] == "mermaid"
            atributos, extra = marcas.atributos(b["a"], b["b"], ["mermaid"] if es_diagrama else [])
            molde = "%s<pre%s>%s</pre>" if es_diagrama else "%s<pre%s><code>%s</code></pre>"
            out.append(molde % (extra, atributos, html.escape(b["texto"], quote=False)))
        elif t == "tabla":
            cabeza = _html_fila(marcas, b["a"], b["cab"], "th")
            cuerpo = "".join(_html_fila(marcas, linea, celdas, "td") for linea, celdas in b["filas"])
            out.append('<div class="tabla"><table><thead>%s</thead><tbody>%s</tbody></table></div>' % (cabeza, cuerpo))
        elif t == "cita":
            out.append("<blockquote>%s</blockquote>" % _html(b["dentro"], marcas, base_titulos))
        elif t == "lista":
            out.append(_html_lista(b["items"], marcas))
    return "\n".join(out)


def a_html(texto, rangos=(), base_titulos=1):
    """Convierte el documento. `rangos` son los pasajes a señalar: (línea inicial, línea final, ancla)."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    return _html(bloques(sin_comentarios(texto)), _Marcas(rangos), base_titulos)
