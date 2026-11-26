"""El censo de máquinas virtuales de un hipervisor. El vigía no le habla al hipervisor: la instancia deja un archivo que
el hipervisor escribe cada tanto (por ejemplo, por una llave de comando forzado) y el vigía lo lee y lo compara con las
máquinas que se esperan. Solo observa: no enciende ni apaga nada.

Forma del archivo: {"tomada": <segundos>, "maquinas": [{"nombre": texto, "estado": "corriendo" | "apagada" | otro}]}.
Un archivo ausente, ilegible o viejo deja el censo «desconocido»: no abre ni cierra nada de cada máquina. Una entrada
con el nombre o el estado fuera de forma (un hipervisor admite nombres con espacios) no invalida el resto: esa máquina
se informa aparte, con su nombre saneado, y las demás se comparan igual.
"""
import hashlib
import json
import re

MAX_BYTES = 1 << 20
MAX_MAQUINAS = 500
_NOMBRE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_ESTADO = re.compile(r"^[a-z]{1,20}$")
_FUERA = re.compile(r"[^A-Za-z0-9._-]")


def _saneado(nombre):
    """Un nombre fuera de forma, escrito solo con caracteres seguros para un identificador y un aviso: lo demás pasa a
    `_`, se recorta, y lleva un sufijo de su huella para que dos nombres distintos no se confundan."""
    return _FUERA.sub("_", nombre)[:40] + "-" + hashlib.sha256(nombre.encode("utf-8", "replace")).hexdigest()[:8]


def leer(m, ahora):
    """{"estado": "ok" | "desconocido", "motivo", "maquinas": {nombre: estado}, "fuera_de_forma": {clave: motivo}}.

    `fuera_de_forma` lleva las entradas que nombran una máquina pero no se pueden comparar: con el nombre fuera de forma
    (clave: el nombre saneado) o con un nombre válido y el estado fuera de forma (clave: el nombre). Una entrada que ni
    siquiera es un objeto con un nombre de texto es un archivo roto: el censo entero queda desconocido."""
    vacio = {"maquinas": {}, "fuera_de_forma": {}}
    try:
        with open(m["archivo"], "rb") as f:
            crudo = f.read(MAX_BYTES + 1)
    except OSError:
        return {"estado": "desconocido", "motivo": "no está el archivo del censo", **vacio}
    try:
        if len(crudo) > MAX_BYTES:
            raise ValueError
        datos = json.loads(crudo.decode("utf-8"))
        tomada = datos["tomada"]
        lista = datos["maquinas"]
        if type(tomada) is not int or not isinstance(lista, list) or len(lista) > MAX_MAQUINAS:
            raise ValueError
        maquinas, fuera = {}, {}
        for x in lista:
            if not (isinstance(x, dict) and isinstance(x.get("nombre"), str)):
                raise ValueError
            nombre, est = x["nombre"], x.get("estado")
            if not _NOMBRE.fullmatch(nombre):
                fuera[_saneado(nombre)] = "el nombre no tiene la forma esperada"
            elif not (isinstance(est, str) and _ESTADO.fullmatch(est)):
                fuera[nombre] = "el estado no tiene la forma esperada"
            else:
                maquinas[nombre] = est
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return {"estado": "desconocido", "motivo": "el archivo del censo no tiene la forma esperada", **vacio}
    if ahora - tomada > m["vigencia_s"] or tomada - ahora > 300:
        return {"estado": "desconocido", "motivo": "el censo está vencido", **vacio}
    # Un nombre que figura válido en una entrada y fuera de forma en otra no se compara: no se sabe cuál es la verdad.
    for nombre in set(maquinas) & set(fuera):
        del maquinas[nombre]
    return {"estado": "ok", "motivo": "", "maquinas": maquinas, "fuera_de_forma": fuera}


def hallazgos(m, censo):
    """[(identificador, clase, texto)] de lo que pide a una persona. Con el censo desconocido, uno solo: el censo."""
    if censo["estado"] != "ok":
        return [("maquinas-censo", "censo de máquinas", f"El censo de máquinas virtuales no se pudo leer: "
                 f"{censo['motivo']}. Mientras tanto no se sabe si falta alguna.")]
    r = []
    fuera = censo.get("fuera_de_forma") or {}
    for clave, motivo in sorted(fuera.items()):
        r.append((f"maquina-fuera-de-forma-{clave}", "máquina fuera de forma",
                  f"El censo del hipervisor trae una máquina que no se puede comparar ({motivo}): {clave}. "
                  "Las demás se siguen vigilando."))
    for nombre, e in sorted(m["esperadas"].items()):
        estado = censo["maquinas"].get(nombre)
        if nombre in fuera:
            continue          # figura, pero no se sabe cómo está: su propio incidente lo dice
        if estado is None:
            r.append((f"maquina-ausente-{nombre}", "máquina ausente",
                      f"La máquina virtual {nombre} no figura en el censo del hipervisor."))
        elif estado != "corriendo" and not e["apagada"]:
            r.append((f"maquina-detenida-{nombre}", "máquina detenida",
                      f"La máquina virtual {nombre} figura «{estado}» en el censo del hipervisor."))
    for nombre, estado in sorted(censo["maquinas"].items()):
        e = m["esperadas"].get(nombre)
        # Corre y nadie la lee: no está declarada, o lo está sin servidor y sin decir a propósito que no se vigila (por
        # ejemplo, una que se parcha por su propia vía).
        if estado == "corriendo" and (e is None or (e["servidor"] is None and not e["sin_vigia"])):
            r.append((f"maquina-sin-vigia-{nombre}", "máquina sin vigía",
                      f"La máquina virtual {nombre} corre en el hipervisor y el vigía no la tiene declarada: nadie "
                      "mira sus parches."))
    return r
