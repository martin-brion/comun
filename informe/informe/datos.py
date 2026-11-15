"""Valida el archivo de datos de un informe. El contrato está en CONTRATO.md; acá se hace cumplir.

Un dato mal formado frena el armado y dice dónde está. Nada se completa en silencio.
"""
import re

ESTADOS = ("ok", "exc", "dif", "na")
SEVERIDADES = ("alta", "media", "baja")
_ID = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_FUENTE = re.compile(r"^[A-Za-z0-9 ,'\"-]{1,120}$")
_URL_FUENTES = re.compile(r"^https://fonts\.googleapis\.com/css2\?[A-Za-z0-9:;@,+&=._-]{1,400}$")
_ARCHIVO = re.compile(r"^([a-z][a-z0-9_-]{0,39}):([^\x00-\x1f:*?\"<>|]{1,300})$")
TEMA = {"acento": _COLOR, "acento_oscuro": _COLOR, "fuente_titulos": _FUENTE, "fuente_texto": _FUENTE,
        "fuentes_url": _URL_FUENTES}


class ErrorDatos(ValueError):
    pass


def _fallo(donde, que):
    raise ErrorDatos("%s: %s" % (donde, que))


def _texto(d, clave, donde, opcional=False, tope=4000):
    v = d.get(clave)
    if v is None:
        if opcional:
            return None
        _fallo(donde, 'falta "%s"' % clave)
    if not isinstance(v, str) or not v.strip():
        _fallo(donde, '"%s" tiene que ser un texto no vacío' % clave)
    if len(v) > tope:
        _fallo(donde, '"%s" pasa de %d caracteres' % (clave, tope))
    return v


def _lista(d, clave, donde, minimo=1):
    v = d.get(clave)
    if not isinstance(v, list) or len(v) < minimo:
        _fallo(donde, '"%s" tiene que ser una lista con al menos %d elemento(s)' % (clave, minimo))
    return v


def _objeto(v, donde):
    if not isinstance(v, dict):
        _fallo(donde, "tiene que ser un objeto")
    return v


def _solo(d, permitidas, donde):
    sobran = sorted(set(d) - set(permitidas))
    if sobran:
        _fallo(donde, "clave desconocida: %s" % ", ".join(sobran))


def _textos(d, clave, donde):
    for i, v in enumerate(_lista(d, clave, donde)):
        if not isinstance(v, str) or not v.strip():
            _fallo("%s.%s[%d]" % (donde, clave, i), "tiene que ser un texto no vacío")


def _mermaid(d, donde):
    t = _texto(d, "mermaid", donde, tope=20000)
    if "<script" in t.lower():
        _fallo(donde, "el diagrama no puede traer etiquetas de script")


def _b_texto(b, donde):
    _solo(b, ("tipo", "parrafos"), donde)
    _textos(b, "parrafos", donde)


def _b_unico(b, donde):
    _solo(b, ("tipo", "texto"), donde)
    _texto(b, "texto", donde, tope=8000)


def _b_veredicto(b, donde):
    _solo(b, ("tipo", "columnas"), donde)
    for i, c in enumerate(_lista(b, "columnas", donde)):
        dc = "%s.columnas[%d]" % (donde, i)
        _solo(_objeto(c, dc), ("titulo", "items"), dc)
        _texto(c, "titulo", dc)
        _textos(c, "items", dc)


def _b_cifras(b, donde):
    _solo(b, ("tipo", "items"), donde)
    for i, c in enumerate(_lista(b, "items", donde)):
        dc = "%s.items[%d]" % (donde, i)
        _solo(_objeto(c, dc), ("valor", "texto"), dc)
        _texto(c, "valor", dc, tope=24)
        _texto(c, "texto", dc, tope=200)


def _b_diagrama(b, donde):
    _solo(b, ("tipo", "titulo", "mermaid", "pie"), donde)
    _texto(b, "titulo", donde, opcional=True)
    _texto(b, "pie", donde, opcional=True)
    _mermaid(b, donde)


def _b_antes_despues(b, donde):
    _solo(b, ("tipo", "antes", "despues", "pie"), donde)
    _texto(b, "pie", donde, opcional=True)
    for lado in ("antes", "despues"):
        dl = "%s.%s" % (donde, lado)
        if lado not in b:
            _fallo(donde, 'falta "%s"' % lado)
        _solo(_objeto(b[lado], dl), ("titulo", "mermaid"), dl)
        _texto(b[lado], "titulo", dl, opcional=True)
        _mermaid(b[lado], dl)


def _b_matriz(b, donde):
    _solo(b, ("tipo", "columnas", "filas"), donde)
    ids = []
    for i, c in enumerate(_lista(b, "columnas", donde)):
        dc = "%s.columnas[%d]" % (donde, i)
        _solo(_objeto(c, dc), ("id", "nombre", "nota"), dc)
        if not _ID.match(_texto(c, "id", dc)):
            _fallo(dc, '"id" inválido: minúsculas, números y guiones')
        if c["id"] in ids:
            _fallo(dc, 'el id "%s" está repetido' % c["id"])
        ids.append(c["id"])
        _texto(c, "nombre", dc, tope=60)
        _texto(c, "nota", dc, opcional=True)
    for i, f in enumerate(_lista(b, "filas", donde)):
        df = "%s.filas[%d]" % (donde, i)
        _solo(_objeto(f, df), ("pieza", "regla", "celdas"), df)
        _texto(f, "pieza", df, tope=80)
        _texto(f, "regla", df, opcional=True, tope=200)
        celdas = _objeto(f.get("celdas"), df + ".celdas")
        if sorted(celdas) != sorted(ids):
            _fallo(df, "las celdas tienen que ser exactamente una por columna")
        for cid, celda in celdas.items():
            dc = "%s.celdas.%s" % (df, cid)
            if (not isinstance(celda, list) or len(celda) != 2 or celda[0] not in ESTADOS
                    or not isinstance(celda[1], str) or not celda[1].strip()):
                _fallo(dc, "tiene que ser [estado, texto], con estado en %s" % ", ".join(ESTADOS))


def _b_hallazgos(b, donde):
    _solo(b, ("tipo", "items"), donde)
    for i, h in enumerate(_lista(b, "items", donde)):
        dh = "%s.items[%d]" % (donde, i)
        _solo(_objeto(h, dh), ("severidad", "tema", "titulo", "texto", "evidencia"), dh)
        if h.get("severidad") not in SEVERIDADES:
            _fallo(dh, '"severidad" tiene que ser una de: %s' % ", ".join(SEVERIDADES))
        _texto(h, "tema", dh, tope=40)
        _texto(h, "titulo", dh, tope=200)
        _texto(h, "texto", dh)
        for j, e in enumerate(_lista(h, "evidencia", dh)):
            de = "%s.evidencia[%d]" % (dh, j)
            _solo(_objeto(e, de), ("parafrasis", "cita", "archivo", "fuente"), de)
            _texto(e, "parafrasis", de, tope=1200)
            cita = _texto(e, "cita", de, opcional=True, tope=2000)
            archivo = _texto(e, "archivo", de, opcional=True, tope=360)
            _texto(e, "fuente", de, opcional=True, tope=300)
            if (cita is None) != (archivo is None):
                _fallo(de, '"cita" y "archivo" van juntos: una cita sin archivo no se puede comprobar')
            if archivo is not None and not _ARCHIVO.match(archivo):
                _fallo(de, '"archivo" va como raiz:ruta/relativa.md')
            if archivo is None and e.get("fuente") is None:
                _fallo(de, 'sin cita, la evidencia dice de dónde sale en "fuente"')


def _b_tarjetas(b, donde):
    _solo(b, ("tipo", "items"), donde)
    for i, c in enumerate(_lista(b, "items", donde)):
        dc = "%s.items[%d]" % (donde, i)
        _solo(_objeto(c, dc), ("titulo", "texto"), dc)
        _texto(c, "titulo", dc, tope=200)
        _texto(c, "texto", dc)


def _b_pasos(b, donde):
    _solo(b, ("tipo", "items"), donde)
    _textos(b, "items", donde)


def _b_fases(b, donde):
    _solo(b, ("tipo", "items"), donde)
    for i, c in enumerate(_lista(b, "items", donde)):
        dc = "%s.items[%d]" % (donde, i)
        _solo(_objeto(c, dc), ("titulo", "cuando", "texto", "items"), dc)
        _texto(c, "titulo", dc, tope=120)
        _texto(c, "cuando", dc, opcional=True, tope=120)
        _texto(c, "texto", dc, opcional=True)
        if "items" in c:
            _textos(c, "items", dc)
        if "items" not in c and "texto" not in c:
            _fallo(dc, 'lleva "texto" o "items"')


def _b_decisiones(b, donde):
    _solo(b, ("tipo", "items"), donde)
    for i, c in enumerate(_lista(b, "items", donde)):
        dc = "%s.items[%d]" % (donde, i)
        _solo(_objeto(c, dc), ("id", "decision", "recomendacion"), dc)
        _texto(c, "id", dc, tope=12)
        _texto(c, "decision", dc)
        _texto(c, "recomendacion", dc)


def _b_desplegable(b, donde):
    _solo(b, ("tipo", "titulo", "bloques"), donde)
    _texto(b, "titulo", donde, tope=200)
    for j, hijo in enumerate(_lista(b, "bloques", donde)):
        dh = "%s.bloques[%d]" % (donde, j)
        tipo = _objeto(hijo, dh).get("tipo")
        if tipo not in BLOQUES or tipo in ("desplegable", "hallazgos"):
            _fallo(dh, 'dentro de un desplegable no va un bloque "%s"' % tipo)
        BLOQUES[tipo](hijo, "%s (%s)" % (dh, tipo))


BLOQUES = {
    "desplegable": _b_desplegable,
    "texto": _b_texto, "nota": _b_unico, "subtitulo": _b_unico, "codigo": _b_unico, "veredicto": _b_veredicto,
    "cifras": _b_cifras, "diagrama": _b_diagrama, "antes_despues": _b_antes_despues, "matriz": _b_matriz,
    "hallazgos": _b_hallazgos, "tarjetas": _b_tarjetas, "pasos": _b_pasos, "fases": _b_fases,
    "decisiones": _b_decisiones,
}


def validar(d):
    """Devuelve los mismos datos si cumplen el contrato; si no, levanta ErrorDatos diciendo dónde."""
    _objeto(d, "datos")
    _solo(d, ("titulo", "antetitulo", "bajada", "fuentes", "tema", "secciones"), "datos")
    _texto(d, "titulo", "datos", tope=120)
    for clave in ("antetitulo", "bajada", "fuentes"):
        _texto(d, clave, "datos", opcional=True, tope=1200)
    if "tema" in d:
        tema = _objeto(d["tema"], "tema")
        _solo(tema, TEMA, "tema")
        for clave, valor in tema.items():
            if not isinstance(valor, str) or not TEMA[clave].match(valor):
                _fallo("tema.%s" % clave, "valor inválido")
    ids = set()
    for i, s in enumerate(_lista(d, "secciones", "datos")):
        ds = "secciones[%d]" % i
        _solo(_objeto(s, ds), ("id", "titulo", "menu", "intro", "bloques"), ds)
        if not _ID.match(_texto(s, "id", ds)):
            _fallo(ds, '"id" inválido: minúsculas, números y guiones')
        if s["id"] in ids:
            _fallo(ds, 'el id "%s" está repetido' % s["id"])
        ids.add(s["id"])
        _texto(s, "titulo", ds, tope=160)
        _texto(s, "menu", ds, opcional=True, tope=30)
        _texto(s, "intro", ds, opcional=True)
        for j, b in enumerate(_lista(s, "bloques", ds)):
            db = "%s.bloques[%d]" % (ds, j)
            tipo = _objeto(b, db).get("tipo")
            if tipo not in BLOQUES:
                _fallo(db, 'tipo de bloque desconocido: "%s". Los que hay: %s' % (tipo, ", ".join(sorted(BLOQUES))))
            BLOQUES[tipo](b, "%s (%s)" % (db, tipo))
    return d
