"""Los parámetros de la instancia (`vigia.json`). Todo lo que nombra un servidor, una imagen propia o un horario vive acá,
nunca en el código. Un archivo con una clave desconocida o un valor fuera de forma no arranca: no se adivina."""
import json
import re

from . import versiones

PLAZOS_H = {"kev_expuesto": 24, "alta_expuesta": 72, "alta_interna": 168, "ordinario": 720}
POR_DEFECTO = {"nivel": "nunca", "expuesto": False}
_NOMBRE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_CLAVES = {"servidores", "propias", "componentes", "por_defecto", "maduracion_dias", "plazos_h", "fuentes", "escaner",
           "estado_dir", "registro_dir", "foto_vigencia_s"}
_COMPONENTE = {"nivel", "expuesto", "linea", "base", "nota"}


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
    return r


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
    return {"servidores": [{"nombre": s["nombre"], "lector": s.get("lector", "local")} for s in servidores],
            "propias": list(propias), "por_defecto": base,
            "componentes": {k: _componente(k, v, base) for k, v in componentes.items()},
            "maduracion_dias": maduracion, "plazos_h": plazos, "fuentes": fuentes, "escaner": escaner,
            "estado_dir": crudo["estado_dir"], "registro_dir": crudo["registro_dir"], "foto_vigencia_s": vigencia}


def cargar(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return validar(json.load(f))
    except (OSError, ValueError) as e:
        raise ConfigError(f"no se pudo leer la configuración: {type(e).__name__}") from None


def componente(cfg, pieza):
    return cfg["componentes"].get(pieza, cfg["por_defecto"])
