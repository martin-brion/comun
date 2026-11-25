"""El censo de máquinas virtuales de un hipervisor. El vigía no le habla al hipervisor: la instancia deja un archivo que
el hipervisor escribe cada tanto (por ejemplo, por una llave de comando forzado) y el vigía lo lee y lo compara con las
máquinas que se esperan. Solo observa: no enciende ni apaga nada.

Forma del archivo: {"tomada": <segundos>, "maquinas": [{"nombre": texto, "estado": "corriendo" | "apagada" | otro}]}.
Un archivo ausente, ilegible o viejo deja el censo «desconocido»: no abre ni cierra nada de cada máquina.
"""
import json
import re

MAX_BYTES = 1 << 20
MAX_MAQUINAS = 500
_NOMBRE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_ESTADO = re.compile(r"^[a-z]{1,20}$")


def leer(m, ahora):
    """{"estado": "ok" | "desconocido", "motivo", "maquinas": {nombre: estado}}."""
    try:
        with open(m["archivo"], "rb") as f:
            crudo = f.read(MAX_BYTES + 1)
    except OSError:
        return {"estado": "desconocido", "motivo": "no está el archivo del censo", "maquinas": {}}
    try:
        if len(crudo) > MAX_BYTES:
            raise ValueError
        datos = json.loads(crudo.decode("utf-8"))
        tomada = datos["tomada"]
        lista = datos["maquinas"]
        if type(tomada) is not int or not isinstance(lista, list) or len(lista) > MAX_MAQUINAS:
            raise ValueError
        maquinas = {}
        for x in lista:
            if not (isinstance(x, dict) and isinstance(x.get("nombre"), str) and _NOMBRE.match(x["nombre"])
                    and isinstance(x.get("estado"), str) and _ESTADO.match(x["estado"])):
                raise ValueError
            maquinas[x["nombre"]] = x["estado"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return {"estado": "desconocido", "motivo": "el archivo del censo no tiene la forma esperada", "maquinas": {}}
    if ahora - tomada > m["vigencia_s"] or tomada - ahora > 300:
        return {"estado": "desconocido", "motivo": "el censo está vencido", "maquinas": {}}
    return {"estado": "ok", "motivo": "", "maquinas": maquinas}


def hallazgos(m, censo):
    """[(identificador, clase, texto)] de lo que pide a una persona. Con el censo desconocido, uno solo: el censo."""
    if censo["estado"] != "ok":
        return [("maquinas-censo", "censo de máquinas", f"El censo de máquinas virtuales no se pudo leer: "
                 f"{censo['motivo']}. Mientras tanto no se sabe si falta alguna.")]
    r = []
    for nombre, e in sorted(m["esperadas"].items()):
        estado = censo["maquinas"].get(nombre)
        if estado is None:
            r.append((f"maquina-ausente-{nombre}", "máquina ausente",
                      f"La máquina virtual {nombre} no figura en el censo del hipervisor."))
        elif estado != "corriendo" and not e["apagada"]:
            r.append((f"maquina-detenida-{nombre}", "máquina detenida",
                      f"La máquina virtual {nombre} figura «{estado}» en el censo del hipervisor."))
    for nombre, estado in sorted(censo["maquinas"].items()):
        if nombre not in m["esperadas"] and estado == "corriendo":
            r.append((f"maquina-sin-vigia-{nombre}", "máquina sin vigía",
                      f"La máquina virtual {nombre} corre en el hipervisor y el vigía no la tiene declarada: nadie "
                      "mira sus parches."))
    return r
