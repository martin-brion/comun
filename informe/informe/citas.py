"""Ubica una cita textual dentro de un documento y dice en qué líneas está.

La comparación ignora lo que cambia al copiar un pasaje: saltos de línea, espacios repetidos, los asteriscos y los
acentos graves del formato, y el `>` de una cita al empezar la línea. Lo demás tiene que coincidir.
"""
import unicodedata

MINIMO = 12
MAXIMO = 600
_QUITAR = frozenset("*`")


class CitaInvalida(ValueError):
    pass


def _normal(texto):
    """Devuelve el texto normalizado y, por cada carácter, la línea de la que salió."""
    texto = unicodedata.normalize("NFC", texto.replace("\r\n", "\n").replace("\r", "\n"))
    sal, lin, n, espacio, inicio = [], [], 1, True, True
    for c in texto:
        if c == "\n":
            if not espacio:
                sal.append(" ")
                lin.append(n)
                espacio = True
            n += 1
            inicio = True
            continue
        if c in _QUITAR:
            continue
        if inicio and c == ">":
            continue
        if c.isspace():
            if not espacio:
                sal.append(" ")
                lin.append(n)
                espacio = True
            continue
        inicio = False
        sal.append(c)
        lin.append(n)
        espacio = False
    return "".join(sal), lin


def ubicar(fuente, cita):
    """(línea inicial, línea final, veces que aparece) o None si la cita no está en la fuente."""
    buscada = _normal(cita)[0].strip()
    if len(buscada) < MINIMO:
        raise CitaInvalida("la cita es demasiado corta para ubicarla (menos de %d caracteres)" % MINIMO)
    if len(buscada) > MAXIMO:
        raise CitaInvalida("la cita es demasiado larga (más de %d caracteres): citá solo el pasaje" % MAXIMO)
    norm, lin = _normal(fuente)
    p = norm.find(buscada)
    if p < 0:
        return None
    return lin[p], lin[p + len(buscada) - 1], norm.count(buscada)
