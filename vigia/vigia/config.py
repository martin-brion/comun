"""Los parámetros de la instancia (`vigia.json`). Todo lo que nombra un servidor, una imagen propia o un horario vive acá,
nunca en el código. Un archivo con una clave desconocida o un valor fuera de forma no arranca: no se adivina."""
import json
import re
import string

from . import versiones

PLAZOS_H = {"kev_expuesto": 24, "alta_expuesta": 72, "alta_interna": 168, "ordinario": 720}
POR_DEFECTO = {"nivel": "nunca", "expuesto": False}
_NOMBRE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_CLAVES = {"servidores", "propias", "componentes", "por_defecto", "maduracion_dias", "plazos_h", "fuentes", "escaner",
           "estado_dir", "registro_dir", "foto_vigencia_s", "ventana", "acciones", "playbooks", "max_por_ventana",
           "espera_salud_s", "aviso"}
_COMPONENTE = {"nivel", "expuesto", "linea", "base", "nota", "playbook"}
# Los tipos de acción que el ejecutor sabe hacer, y las acciones con nombre que cada uno le pide a la instancia.
PLAYBOOKS = {"imagen": ("respaldo_vigente", "aplicar_imagen", "verificar", "volver_imagen"),
             "datos": ("respaldo_vigente", "respaldar_datos", "aplicar_imagen", "verificar", "restaurar_datos",
                       "volver_imagen")}
ACCIONES = {"respaldo_vigente": (), "aplicar_imagen": ("servicio", "referencia"), "verificar": ("servicio",),
            "volver_imagen": ("servicio", "referencia"), "respaldar_datos": ("servicio", "marca"),
            "restaurar_datos": ("servicio", "marca")}
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(Exception):
    pass


def _componente(nombre, c, base):
    if not isinstance(c, dict) or set(c) - _COMPONENTE:
        raise ConfigError(f"componente {nombre}: claves desconocidas")
    r = {**base, **c}
    if r["nivel"] not in versiones.NIVELES:
        raise ConfigError(f"componente {nombre}: nivel inválido")
    if not isinstance(r["expuesto"], bool):
        raise ConfigError(f"componente {nombre}: expuesto es sí o no")
    if "linea" in r and (type(r["linea"]) is not int or not 1 <= r["linea"] <= 4):
        raise ConfigError(f"componente {nombre}: linea inválida")
    if "base" in r and versiones.referencia(r["base"]) is None:
        raise ConfigError(f"componente {nombre}: base inválida")
    if "playbook" in r and r["playbook"] not in PLAYBOOKS:
        raise ConfigError(f"componente {nombre}: playbook desconocido")
    return r


def _plantilla(nombre, argv):
    """Una acción es una lista de argumentos, nunca una línea de shell. Solo admite los huecos de esa acción."""
    if not (isinstance(argv, list) and argv and all(isinstance(x, str) and x for x in argv)):
        raise ConfigError(f"acción {nombre}: es un comando en lista")
    huecos = set()
    try:
        for x in argv:
            for _, campo, formato, conversion in string.Formatter().parse(x):
                if campo is not None:
                    if formato or conversion:
                        raise ConfigError(f"acción {nombre}: hueco con formato")
                    huecos.add(campo)
    except ValueError:
        raise ConfigError(f"acción {nombre}: llaves sin cerrar") from None
    if huecos - set(ACCIONES[nombre]):
        raise ConfigError(f"acción {nombre}: hueco desconocido")
    return list(argv)


def validar(crudo):
    if not isinstance(crudo, dict) or set(crudo) - _CLAVES:
        raise ConfigError("claves desconocidas en la configuración")
    servidores = crudo.get("servidores")
    if not isinstance(servidores, list) or not servidores:
        raise ConfigError("falta la lista de servidores")
    vistos = set()
    for s in servidores:
        if not isinstance(s, dict) or set(s) - {"nombre", "lector"} or not _NOMBRE.match(str(s.get("nombre"))):
            raise ConfigError("servidor inválido")
        if s["nombre"] in vistos:
            raise ConfigError("servidor repetido")
        vistos.add(s["nombre"])
        lector = s.get("lector", "local")
        if lector != "local" and not (isinstance(lector, list) and lector and all(isinstance(x, str) and x for x in lector)):
            raise ConfigError(f"servidor {s['nombre']}: lector es «local» o un comando en lista")
    propias = crudo.get("propias", [])
    if not isinstance(propias, list) or not all(isinstance(p, str) and p for p in propias):
        raise ConfigError("propias es una lista de prefijos")
    base = _componente("por_defecto", crudo.get("por_defecto", {}), POR_DEFECTO)
    componentes = crudo.get("componentes", {})
    if not isinstance(componentes, dict):
        raise ConfigError("componentes inválido")
    plazos = {**PLAZOS_H, **crudo.get("plazos_h", {})}
    if set(plazos) != set(PLAZOS_H) or not all(type(v) is int and v > 0 for v in plazos.values()):
        raise ConfigError("plazos_h inválido")
    maduracion = crudo.get("maduracion_dias", 7)
    if type(maduracion) is not int or not 0 <= maduracion <= 90:
        raise ConfigError("maduracion_dias inválido")
    fuentes = {"docker_hub": True, "registros": [], "kev": True, **crudo.get("fuentes", {})}
    if (set(fuentes) != {"docker_hub", "registros", "kev"} or not isinstance(fuentes["registros"], list)
            or not all(isinstance(r, str) for r in fuentes["registros"])
            or not isinstance(fuentes["docker_hub"], bool) or not isinstance(fuentes["kev"], bool)):
        raise ConfigError("fuentes inválido")
    escaner = {"comando": None, "tope_s": 2700, **crudo.get("escaner", {})}
    if set(escaner) != {"comando", "tope_s"} or type(escaner["tope_s"]) is not int or escaner["tope_s"] <= 0 or not (
            escaner["comando"] is None or (isinstance(escaner["comando"], list) and escaner["comando"]
                                           and all(isinstance(x, str) and x for x in escaner["comando"]))):
        raise ConfigError("escaner inválido")
    for clave in ("estado_dir", "registro_dir"):
        if not isinstance(crudo.get(clave), str) or not crudo[clave]:
            raise ConfigError(f"falta {clave}")
    vigencia = crudo.get("foto_vigencia_s", 900)
    if type(vigencia) is not int or vigencia <= 0:
        raise ConfigError("foto_vigencia_s inválido")
    ventana = crudo.get("ventana")
    if ventana is not None and not (
            isinstance(ventana, dict) and set(ventana) == {"dias", "desde", "hasta"}
            and isinstance(ventana["dias"], list) and ventana["dias"]
            and all(type(d) is int and 0 <= d <= 6 for d in ventana["dias"])
            and all(isinstance(ventana[k], str) and _HORA.match(ventana[k]) for k in ("desde", "hasta"))
            and ventana["desde"] < ventana["hasta"]):
        raise ConfigError("ventana inválida: días 0 (lunes) a 6 y un tramo desde < hasta en el mismo día")
    playbooks = crudo.get("playbooks", [])
    if not isinstance(playbooks, list) or set(playbooks) - set(PLAYBOOKS):
        raise ConfigError("playbooks inválido")
    acciones = crudo.get("acciones", {})
    if not isinstance(acciones, dict) or set(acciones) - vistos:
        raise ConfigError("acciones: servidor desconocido")
    limpias = {}
    for servidor, mapa in acciones.items():
        if not isinstance(mapa, dict) or set(mapa) - set(ACCIONES):
            raise ConfigError(f"acciones de {servidor}: acción desconocida")
        limpias[servidor] = {nombre: _plantilla(nombre, argv) for nombre, argv in mapa.items()}
    tope = crudo.get("max_por_ventana", 3)
    espera = crudo.get("espera_salud_s", 180)
    if type(tope) is not int or not 1 <= tope <= 20 or type(espera) is not int or not 0 <= espera <= 3600:
        raise ConfigError("max_por_ventana o espera_salud_s inválido")
    aviso = crudo.get("aviso")
    if aviso is not None and not (isinstance(aviso, list) and aviso and all(isinstance(x, str) and x for x in aviso)):
        raise ConfigError("aviso es un comando en lista")
    return {"servidores": [{"nombre": s["nombre"], "lector": s.get("lector", "local")} for s in servidores],
            "propias": list(propias), "por_defecto": base,
            "componentes": {k: _componente(k, v, base) for k, v in componentes.items()},
            "maduracion_dias": maduracion, "plazos_h": plazos, "fuentes": fuentes, "escaner": escaner,
            "estado_dir": crudo["estado_dir"], "registro_dir": crudo["registro_dir"], "foto_vigencia_s": vigencia,
            "ventana": ventana, "acciones": limpias, "playbooks": list(playbooks), "max_por_ventana": tope,
            "espera_salud_s": espera, "aviso": aviso}


def cargar(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return validar(json.load(f))
    except (OSError, ValueError) as e:
        raise ConfigError(f"no se pudo leer la configuración: {type(e).__name__}") from None


def componente(cfg, pieza):
    return cfg["componentes"].get(pieza, cfg["por_defecto"])
