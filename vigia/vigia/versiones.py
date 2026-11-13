"""Referencias de imagen y etiquetas de versión: leerlas, compararlas y decir qué tan grande es un salto.

Dos etiquetas se comparan solo si son de la misma familia: mismo prefijo, misma variante («-alpine», «-rootless») y la
misma cantidad de números. Una preliminar (rc, beta, dev) nunca es candidata.
"""
import re
from dataclasses import dataclass
from typing import Optional

_ETIQUETA = re.compile(r"^(v?)(\d+(?:\.\d+)*)(?:[-_.]?(\S*))?$")
_PREVIA = re.compile(r"^(rc|alpha|beta|dev|pre|preview|nightly|snapshot|test)\d*$", re.I)
_SEGURO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,299}$")
SALTOS = ("patch", "minor", "mayor")          # de menor a mayor
NIVELES = ("nunca", "patch", "minor")


@dataclass(frozen=True)
class Referencia:
    registro: str
    repo: str
    tag: str
    digest: str

    @property
    def pieza(self):
        return f"{self.registro}/{self.repo}"


def referencia(texto) -> Optional[Referencia]:
    """`[registro/]repo[:tag][@digest]` → Referencia, o None si no parece una referencia de imagen."""
    if not isinstance(texto, str) or not _SEGURO.match(texto):
        return None
    resto, _, digest = texto.partition("@")
    if digest and not re.match(r"^sha256:[0-9a-f]{64}$", digest):
        return None
    tag = ""
    if ":" in resto.rsplit("/", 1)[-1]:
        resto, _, tag = resto.rpartition(":")
    primero, barra, cola = resto.partition("/")
    if barra and ("." in primero or ":" in primero or primero == "localhost"):
        registro, repo = primero, cola
    else:
        registro, repo = "docker.io", resto
    if registro == "docker.io" and "/" not in repo:
        repo = "library/" + repo
    if not repo or repo.startswith("/") or ".." in repo:
        return None
    return Referencia(registro, repo, tag, digest)


@dataclass(frozen=True)
class Etiqueta:
    texto: str
    prefijo: str
    numeros: tuple
    variante: str
    fecha: str          # compilación fechada (AAAAMMDD) sobre la misma versión, o ""
    previa: bool

    @property
    def clave(self):
        return (self.numeros, self.fecha)


def etiqueta(texto) -> Optional[Etiqueta]:
    m = _ETIQUETA.match(texto or "")
    if not m:
        return None
    numeros = tuple(int(n) for n in m.group(2).split("."))
    if any(n > 10 ** 9 for n in numeros):
        return None
    resto = m.group(3) or ""
    if re.match(r"^(19|20)\d{6}$", resto):
        return Etiqueta(texto, m.group(1), numeros, "", resto, False)
    previa = any(_PREVIA.match(t) for t in re.split(r"[.\-_]", resto) if t) or bool(re.match(r"^(rc|a|b)\d+$", resto, re.I))
    return Etiqueta(texto, m.group(1), numeros, resto, "", previa)


def misma_familia(a: Etiqueta, b: Etiqueta):
    return (a.prefijo == b.prefijo and a.variante == b.variante and len(a.numeros) == len(b.numeros)
            and bool(a.fecha) == bool(b.fecha))


def salto(actual: Etiqueta, nueva: Etiqueta, linea=None):
    """Tamaño del paso entre dos etiquetas de la misma familia. `linea` es cuántos números de adelante definen la línea que
    un parche no cambia (por defecto, todos menos el último). Una compilación fechada más nueva de la misma versión es parche."""
    if actual.numeros == nueva.numeros:
        return "patch"
    linea = max(1, min(linea or len(actual.numeros) - 1, len(actual.numeros)))
    for i, (x, y) in enumerate(zip(actual.numeros, nueva.numeros)):
        if x != y:
            return "mayor" if i == 0 else ("minor" if i < linea else "patch")
    return "patch"


def flotante(tag, hermanas):
    """Una etiqueta flotante cambia de imagen sin cambiar de nombre: «latest», «18», «1.30-alpine» habiendo «1.30.6-alpine»."""
    e = etiqueta(tag)
    if e is None:
        return True
    for h in hermanas:
        o = etiqueta(h)
        if o is None or o.previa or o.prefijo != e.prefijo or o.variante != e.variante:
            continue
        if len(o.numeros) > len(e.numeros) and o.numeros[:len(e.numeros)] == e.numeros:
            return True
        if o.numeros == e.numeros and o.fecha and not e.fecha:
            return True
    return False


def permite(nivel, tamano):
    """¿El nivel automático declarado («nunca», «patch», «minor») cubre un salto de ese tamaño? Un salto mayor, nunca."""
    if nivel not in NIVELES or tamano not in SALTOS or tamano == "mayor" or nivel == "nunca":
        return False
    return SALTOS.index(tamano) <= SALTOS.index(nivel)
