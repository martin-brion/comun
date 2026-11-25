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
           "espera_salud_s", "aviso", "ventana_urgente", "espera_reinicio_s", "maquinas", "repos", "sistema_plazo_h",
           "pausa_max_dias"}
_COMPONENTE = {"nivel", "expuesto", "linea", "base", "nota", "playbook"}
# Los tipos de acción que el ejecutor sabe hacer, y las acciones con nombre que cada uno le pide a la instancia.
PLAYBOOKS = {"imagen": ("respaldo_vigente", "aplicar_imagen", "verificar", "volver_imagen"),
             "datos": ("respaldo_vigente", "respaldar_datos", "aplicar_imagen", "verificar", "restaurar_datos",
                       "volver_imagen"),
             # Una imagen propia que se rehace igual, sobre la versión nueva de su base: la vuelta atrás es la imagen
             # anterior, armada sobre la base anterior, que la instancia conserva.
             "propia": ("respaldo_vigente", "construir_propia", "aplicar_propia", "verificar", "volver_propia"),
             # El reinicio del servidor, para un kernel o una biblioteca que la rutina del sistema ya instaló. No tiene
             # vuelta atrás: va último en la pasada y, si algo no vuelve, escala.
             "reinicio": ("respaldo_vigente", "reiniciar", "verificar_servidor")}
ACCIONES = {"respaldo_vigente": (), "aplicar_imagen": ("servicio", "referencia"), "verificar": ("servicio",),
            "volver_imagen": ("servicio", "referencia"), "respaldar_datos": ("servicio", "marca"),
            "restaurar_datos": ("servicio", "marca"), "construir_propia": ("servicio", "base"),
            "aplicar_propia": ("servicio", "base"), "volver_propia": ("servicio", "base"), "reiniciar": (),
            "verificar_servidor": ()}
# Huecos que una acción de un servicio puede usar además de los suyos: el proyecto de compose, cuando dos proyectos
# tienen un servicio con el mismo nombre.
OPCIONALES = {a: ("proyecto",) for a, h in ACCIONES.items() if "servicio" in h}
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_MAQUINA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_REMOTO = re.compile(r"^https://[a-z0-9][a-z0-9.-]{0,199}(:\d{1,5})?/[A-Za-z0-9._/~-]{1,300}$")
_RAMA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


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
    if huecos - set(ACCIONES[nombre]) - set(OPCIONALES.get(nombre, ())):
        raise ConfigError(f"acción {nombre}: hueco desconocido")
    return list(argv)


def _comando(valor):
    return isinstance(valor, list) and bool(valor) and all(isinstance(x, str) and x for x in valor)


def _maquinas(m, servidores):
    """El censo de máquinas virtuales: un archivo que deja la instancia (lo escribe el hipervisor) y las que se esperan."""
    if m is None:
        return None
    if (not isinstance(m, dict) or set(m) - {"archivo", "vigencia_s", "esperadas"}
            or not isinstance(m.get("archivo"), str) or not m["archivo"]):
        raise ConfigError("maquinas: archivo, vigencia_s y esperadas")
    vigencia = m.get("vigencia_s", 3600)
    esperadas = m.get("esperadas", {})
    if type(vigencia) is not int or vigencia <= 0 or not isinstance(esperadas, dict):
        raise ConfigError("maquinas inválido")
    limpias = {}
    for nombre, e in esperadas.items():
        if not _MAQUINA.fullmatch(str(nombre)) or not isinstance(e, dict) or set(e) - {"servidor", "apagada", "sin_vigia"}:
            raise ConfigError(f"maquinas: {nombre} inválida")
        servidor = e.get("servidor")
        if servidor is not None and servidor not in servidores:
            raise ConfigError(f"maquinas: {nombre} nombra un servidor desconocido")
        if not isinstance(e.get("apagada", False), bool) or not isinstance(e.get("sin_vigia", False), bool):
            raise ConfigError(f"maquinas: {nombre}: apagada y sin_vigia son sí o no")
        if servidor is not None and e.get("sin_vigia"):
            raise ConfigError(f"maquinas: {nombre}: tiene servidor o es sin_vigia, no las dos")
        limpias[nombre] = {"servidor": servidor, "apagada": e.get("apagada", False), "sin_vigia": e.get("sin_vigia", False)}
    return {"archivo": m["archivo"], "vigencia_s": vigencia, "esperadas": limpias}


def _repos(lista, servidores):
    """Repositorios montados (los addons de un Odoo, por ejemplo): el commit local contra la punta de su rama remota."""
    if not isinstance(lista, list):
        raise ConfigError("repos es una lista")
    vistos, limpios = set(), []
    for r in lista:
        if (not isinstance(r, dict) or set(r) - {"nombre", "servidor", "local", "remoto", "rama"}
                or not _NOMBRE.fullmatch(str(r.get("nombre"))) or r["nombre"] in vistos):
            raise ConfigError("repos: cada uno con nombre único, servidor, local, remoto y rama")
        if r.get("servidor") not in servidores or not _comando(r.get("local")):
            raise ConfigError(f"repos {r['nombre']}: servidor conocido y local como comando en lista")
        if (not isinstance(r.get("remoto"), str) or not _REMOTO.fullmatch(r["remoto"])
                or not isinstance(r.get("rama"), str) or not _RAMA.fullmatch(r["rama"])):
            raise ConfigError(f"repos {r['nombre']}: remoto https y rama válidos")
        vistos.add(r["nombre"])
        limpios.append({k: r[k] for k in ("nombre", "servidor", "local", "remoto", "rama")})
    return limpios


def validar(crudo):
    if not isinstance(crudo, dict) or set(crudo) - _CLAVES:
        raise ConfigError("claves desconocidas en la configuración")
    servidores = crudo.get("servidores")
    if not isinstance(servidores, list) or not servidores:
        raise ConfigError("falta la lista de servidores")
    vistos = set()
    for s in servidores:
        if not isinstance(s, dict) or set(s) - {"nombre", "lector"} or not _NOMBRE.fullmatch(str(s.get("nombre"))):
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
            and all(isinstance(ventana[k], str) and _HORA.fullmatch(ventana[k]) for k in ("desde", "hasta"))
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
    urgente = crudo.get("ventana_urgente")
    if urgente is not None:
        urgente = {"dias": list(range(7)), **urgente} if isinstance(urgente, dict) else urgente
        if not (isinstance(urgente, dict) and set(urgente) == {"dias", "desde", "hasta"}
                and isinstance(urgente["dias"], list) and urgente["dias"]
                and all(type(d) is int and 0 <= d <= 6 for d in urgente["dias"])
                and all(isinstance(urgente[k], str) and _HORA.fullmatch(urgente[k]) for k in ("desde", "hasta"))
                and urgente["desde"] < urgente["hasta"]):
            raise ConfigError("ventana_urgente inválida: un tramo desde < hasta en el mismo día y, si se dan, días 0 a 6")
    reinicio = crudo.get("espera_reinicio_s", 1200)
    if type(reinicio) is not int or not 60 <= reinicio <= 7200:
        raise ConfigError("espera_reinicio_s inválido")
    sistema_h = crudo.get("sistema_plazo_h", 72)
    pausa = crudo.get("pausa_max_dias", 7)
    if type(sistema_h) is not int or sistema_h <= 0 or type(pausa) is not int or not 1 <= pausa <= 90:
        raise ConfigError("sistema_plazo_h o pausa_max_dias inválido")
    maquinas = _maquinas(crudo.get("maquinas"), vistos)
    repos = _repos(crudo.get("repos", []), vistos)
    aviso = crudo.get("aviso")
    if aviso is not None and not (isinstance(aviso, list) and aviso and all(isinstance(x, str) and x for x in aviso)):
        raise ConfigError("aviso es un comando en lista")
    return {"servidores": [{"nombre": s["nombre"], "lector": s.get("lector", "local")} for s in servidores],
            "propias": list(propias), "por_defecto": base,
            "componentes": {k: _componente(k, v, base) for k, v in componentes.items()},
            "maduracion_dias": maduracion, "plazos_h": plazos, "fuentes": fuentes, "escaner": escaner,
            "estado_dir": crudo["estado_dir"], "registro_dir": crudo["registro_dir"], "foto_vigencia_s": vigencia,
            "ventana": ventana, "acciones": limpias, "playbooks": list(playbooks), "max_por_ventana": tope,
            "espera_salud_s": espera, "aviso": aviso, "ventana_urgente": urgente, "espera_reinicio_s": reinicio,
            "sistema_plazo_h": sistema_h, "pausa_max_dias": pausa, "maquinas": maquinas, "repos": repos}


def cargar(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return validar(json.load(f))
    except (OSError, ValueError) as e:
        raise ConfigError(f"no se pudo leer la configuración: {type(e).__name__}") from None


def componente(cfg, pieza):
    return cfg["componentes"].get(pieza, cfg["por_defecto"])
