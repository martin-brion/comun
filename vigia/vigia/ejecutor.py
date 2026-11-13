"""El ejecutor: aplica, en su ventana, las candidatas que pasan todas las compuertas.

No sabe hacer nada por sí mismo en un servidor: llama acciones con nombre, de una lista cerrada, que la instancia define
como comandos en lista (nunca una línea de shell). Antes de cada efecto deja la intención en el diario; después, el
resultado. Si algo falla, vuelve atrás con la referencia exacta que había. Tras un corte mira qué quedó a medias y lo
resuelve midiendo: nunca repite una acción a ciegas.

Dos tipos de acción: `imagen` (la vuelta atrás es la imagen anterior) y `datos` (antes copia los datos del servicio bajo
una marca de ese intento; la vuelta atrás repone esa copia exacta y recién después levanta la imagen anterior).
"""
import os
import re
import subprocess
import time

from . import censo, config as _config, corrida, escaner, estado, fuentes, informe

_SERVICIO = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_FIJA = re.compile(r"^[a-z0-9][A-Za-z0-9._/:-]{0,299}@(sha256:[0-9a-f]{64})$")
_MARCA = re.compile(r"^m[0-9]{9,12}$")
_PATRON = {"servicio": _SERVICIO, "referencia": _FIJA, "marca": _MARCA}
MAX_FALLAS = 2
PASO_S = 10


def _correr(argv, tope):
    """Código de salida de una acción; None si no existe o no terminó. La salida de la acción no se guarda."""
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=tope).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def accion(cfg, servidor, nombre, valores, correr, tope=900):
    """Corre una acción con nombre. True si salió con cero, False si no, None si la instancia no la definió."""
    plantilla = cfg["acciones"].get(servidor, {}).get(nombre)
    if plantilla is None:
        return None
    for campo in _config.ACCIONES[nombre]:
        if not isinstance(valores.get(campo), str) or not _PATRON[campo].match(valores[campo]):
            return False
    return correr([x.format(**valores) for x in plantilla], tope) == 0


def en_ventana(cfg, tm):
    v = cfg["ventana"]
    if v is None:
        return False
    return tm.tm_wday in v["dias"] and v["desde"] <= f"{tm.tm_hour:02d}:{tm.tm_min:02d}" < v["hasta"]


def _servicios(pieza):
    """Los servicios de compose que usan la imagen, o None si alguno no se puede nombrar sin ambigüedad."""
    nombres = [s.partition("/")[2] for s in pieza["servicios"]]
    if not nombres or len(pieza["servicios"]) != len(pieza["contenedores"]) or len(set(nombres)) != len(nombres):
        return None
    return nombres if all(_SERVICIO.match(n) for n in nombres) else None


def compuertas(cfg, db, servidor, pieza, presente, correr):
    """[(nombre, pasa, detalle)] en orden. El respaldo va al final: es la única que corre un comando."""
    c = pieza["candidata"]
    g = [("versión", c["compuerta_version"] == "pasaría", c["compuerta_version"])]
    playbook = pieza.get("playbook")
    habilitado = playbook in cfg["playbooks"]
    g.append(("tipo de acción", habilitado, playbook if habilitado else "la pieza no tiene un tipo de acción habilitado"))
    if not habilitado:
        return g
    faltan = [a for a in _config.PLAYBOOKS[playbook] if a not in cfg["acciones"].get(servidor, {})]
    g.append(("acciones", not faltan, "definidas" if not faltan else "faltan: " + ", ".join(faltan)))
    estrenado = estado.estrenado(db, playbook)
    g.append(("estreno", presente or estrenado, "ya estrenado" if estrenado else (
        "primera vez, con el dueño presente" if presente else "la primera vez de este tipo pide al dueño presente")))
    g.append(("imagen fija", bool(c.get("referencia")) and bool(_FIJA.match(c["referencia"])),
              "con huella" if c.get("referencia") else "no se conoce la huella de la versión propuesta"))
    g.append(("vuelta atrás", bool(c.get("anterior")) and bool(_FIJA.match(c["anterior"])),
              "con la referencia exacta de hoy" if c.get("anterior") else "no se conoce la huella de la imagen de hoy"))
    g.append(("servicios", _servicios(pieza) is not None, "de compose" if _servicios(pieza) else "no se pueden nombrar"))
    fila = estado.candidata(db, c["id"])
    if fila and fila["estado"] == "escalada":
        g.append(("sin escalar", False, "espera a una persona: " + fila["motivo"]))
    if all(ok for _, ok, _ in g):
        respaldo = accion(cfg, servidor, "respaldo_vigente", {}, correr)
        g.append(("respaldo", respaldo is True, "verificado" if respaldo else "no hay un respaldo vigente verificado"))
    return g


def _huella(foto, contenedor):
    """Las huellas de la imagen con que corre un contenedor."""
    imagen = foto["imagenes"].get(contenedor["imagen_id"], {})
    return {d.rpartition("@")[2] for d in imagen.get("digests", [])} | {contenedor["imagen_ref"].rpartition("@")[2]}


def _mide(cfg, servidor, servicio, tomar, reloj):
    """(foto, contenedores de ese servicio) o (None, None) si no se pudo medir."""
    t = tomar(servidor, int(reloj()), cfg["foto_vigencia_s"])
    if t["estado"] != "ok" or t["foto"]["contenedores"] is None:
        return None, None
    return t["foto"], [c for c in t["foto"]["contenedores"] if c["servicio"] == servicio]


def _sano(cfg, servidor, servicio, huella, tomar, reloj, dormir):
    """Espera a que el servicio corra con esa imagen y esté sano. No alcanza con que el comando haya salido bien."""
    limite = reloj() + cfg["espera_salud_s"]
    while True:
        foto, suyos = _mide(cfg, servidor, servicio, tomar, reloj)
        if foto is not None and suyos and all(
                c["estado"] == "running" and c["salud"] in ("healthy", "") and huella in _huella(foto, c) for c in suyos):
            return True
        if reloj() >= limite:
            return False
        dormir(PASO_S)


def _volver(cfg, db, servidor, c, servicio, tomar, correr, reloj, dormir):
    """Deja el servicio con la imagen anterior y lo verifica. «vuelto» si quedó como estaba; si no, «roto»."""
    nombre = servidor["nombre"]
    m = estado.intencion(db, c["id"], nombre, "volver", {"servicio": servicio, "a": c["anterior"]}, int(reloj()))
    vuelta = accion(cfg, nombre, "volver_imagen", {"servicio": servicio, "referencia": c["anterior"]}, correr) is True
    vuelta = vuelta and _sano(cfg, servidor, servicio, _FIJA.match(c["anterior"]).group(1), tomar, reloj, dormir)
    vuelta = vuelta and accion(cfg, nombre, "verificar", {"servicio": servicio}, correr) is True
    estado.resultado(db, m, "hecho" if vuelta else "fallo", "", int(reloj()))
    return "vuelto" if vuelta else "roto"


def _aplicar_servicio(cfg, db, servidor, c, servicio, playbook, tomar, correr, reloj, dormir):
    """«hecho», «vuelto» (falló y quedó como estaba) o «roto» (falló y la vuelta atrás también).

    Con el tipo `datos` copia antes los datos del servicio bajo una marca propia de este intento y, si la versión nueva
    falla, repone esa copia ANTES de levantar la imagen anterior: la imagen vieja no arranca sobre datos ya migrados."""
    nombre = servidor["nombre"]
    marca = None
    if playbook == "datos":
        marca = f"m{int(reloj())}"
        n = estado.intencion(db, c["id"], nombre, "respaldar", {"servicio": servicio, "de": c["anterior"], "marca": marca},
                             int(reloj()))
        copiado = accion(cfg, nombre, "respaldar_datos", {"servicio": servicio, "marca": marca}, correr) is True
        estado.resultado(db, n, "hecho" if copiado else "fallo",
                         "" if copiado else "no se pudo copiar; la versión nueva no llegó a correr", int(reloj()))
        if not copiado:
            # La copia deja el servicio detenido: se lo levanta con lo que tenía. Sus datos no cambiaron.
            return _volver(cfg, db, servidor, c, servicio, tomar, correr, reloj, dormir)
    intento = {"servicio": servicio, "de": c["anterior"], "a": c["referencia"]}
    if marca:
        intento["marca"] = marca
    n = estado.intencion(db, c["id"], nombre, "aplicar", intento, int(reloj()))
    bien = accion(cfg, nombre, "aplicar_imagen", {"servicio": servicio, "referencia": c["referencia"]}, correr) is True
    bien = bien and _sano(cfg, servidor, servicio, c["indice"], tomar, reloj, dormir)
    bien = bien and accion(cfg, nombre, "verificar", {"servicio": servicio}, correr) is True
    if bien:
        estado.resultado(db, n, "hecho", "", int(reloj()))
        return "hecho"
    estado.resultado(db, n, "fallo", "no aplicó o no pasó la verificación", int(reloj()))
    if marca:
        r = estado.intencion(db, c["id"], nombre, "restaurar", {"servicio": servicio, "marca": marca}, int(reloj()))
        repuesto = accion(cfg, nombre, "restaurar_datos", {"servicio": servicio, "marca": marca}, correr) is True
        estado.resultado(db, r, "hecho" if repuesto else "fallo", "", int(reloj()))
        if not repuesto:
            return "roto"
    return _volver(cfg, db, servidor, c, servicio, tomar, correr, reloj, dormir)


def recuperar(cfg, db, tomar, reloj):
    """Resuelve lo que un corte dejó a medias, midiendo. No corre ninguna acción. Devuelve los servidores que quedaron
    con algo ambiguo: en esos no se aplica nada hasta que lo vea una persona."""
    ambiguos = set()
    por_nombre = {s["nombre"]: s for s in cfg["servidores"]}
    for e in estado.a_medias(db):
        ahora = int(reloj())
        servidor = por_nombre.get(e["servidor"])
        foto, suyos = _mide(cfg, servidor, e["datos"]["servicio"], tomar, reloj) if servidor else (None, None)
        huellas = [_huella(foto, c) for c in suyos] if suyos else []

        def todos(clave):
            fija = _FIJA.match(e["datos"].get(clave) or "")
            return bool(fija) and bool(huellas) and all(fija.group(1) in h for h in huellas)
        if e["paso"] == "respaldar" and todos("de"):
            # La copia no terminó, pero el servicio corre con lo de siempre: sus datos no se tocaron.
            estado.resultado(db, e["n"], "no aplicado", "recuperado tras un corte: la copia no terminó y seguía la versión "
                             "anterior", ahora)
            estado.pasar(db, e["candidata"], "detectada", "", ahora)
        elif e["paso"] == "aplicar" and todos("a"):
            estado.resultado(db, e["n"], "hecho", "recuperado tras un corte: ya corría la versión nueva", ahora)
            estado.pasar(db, e["candidata"], "detectada", "", ahora)
        elif e["paso"] == "aplicar" and todos("de"):
            estado.resultado(db, e["n"], "no aplicado", "recuperado tras un corte: seguía la versión anterior", ahora)
            estado.pasar(db, e["candidata"], "detectada", "", ahora)
        elif e["paso"] == "volver" and todos("a"):
            estado.resultado(db, e["n"], "hecho", "recuperado tras un corte: ya había vuelto", ahora)
            estado.pasar(db, e["candidata"], "esperando", "un intento falló y se volvió atrás", ahora)
        else:
            # Una restauración a medias cae siempre acá: cómo quedaron los datos no se puede medir desde afuera.
            estado.resultado(db, e["n"], "ambiguo", "tras un corte no se pudo saber cómo quedó", ahora)
            estado.pasar(db, e["candidata"], "escalada", "quedó a medias tras un corte", ahora)
            copia = f" Su copia de ese intento lleva la marca {e['datos']['marca']}." if e["datos"].get("marca") else ""
            estado.incidente(db, f"ambiguo-{e['candidata']}", "a medias",
                             f"En {e['servidor']}, el servicio {e['datos']['servicio']} quedó a medias tras un corte "
                             f"(paso: {e['paso']}) y no se pudo saber cómo quedó; puede estar detenido.{copia} No se "
                             "aplica nada más ahí hasta revisarlo.", ahora)
            ambiguos.add(e["servidor"])
    return ambiguos


def _elegir(cfg, db, fecha):
    """Un servidor por ventana: el de hoy si la ventana ya se tomó; si no, el que hace más tiempo que no tiene una."""
    hoy = estado.ventana(db, fecha)
    con_acciones = [s for s in cfg["servidores"] if cfg["acciones"].get(s["nombre"])]
    if hoy:
        return next((s for s in con_acciones if s["nombre"] == hoy["servidor"]), None)
    return min(con_acciones, key=lambda s: estado.ultima_ventana(db, s["nombre"]), default=None)


def aplicar(cfg, presente=False, tomar=censo.tomar, pedir=fuentes.pedir, escanear=escaner.escanear, correr=_correr,
            reloj=time.time, dormir=time.sleep, local=time.localtime):
    """Una pasada del ejecutor. Devuelve el acta: qué miró, qué compuertas frenaron y qué hizo."""
    ahora = int(reloj())
    acta = {"ts": ahora, "presente": presente, "servidor": None, "motivo": "", "pasos": []}
    candado = corrida._candado(cfg["estado_dir"], ahora)
    db = estado.abrir(cfg["estado_dir"])
    try:
        if os.path.exists(os.path.join(cfg["estado_dir"], "PAUSA")):
            acta["motivo"] = "en pausa: existe el archivo PAUSA"
            return acta
        ambiguos = recuperar(cfg, db, tomar, reloj)
        if not presente and not en_ventana(cfg, local(ahora)):
            acta["motivo"] = "fuera de la ventana"
            return acta
        fecha = time.strftime("%Y-%m-%d", local(ahora))
        servidor = _elegir(cfg, db, fecha)
        if servidor is None:
            acta["motivo"] = "ningún servidor tiene acciones definidas, o la ventana de hoy fue de otro"
            return acta
        acta["servidor"] = servidor["nombre"]
        if servidor["nombre"] in ambiguos:
            acta["motivo"] = "algo quedó a medias tras un corte: espera a una persona"
            return acta
        observado = corrida._correr(cfg, ahora, tomar, pedir, escanear, True)
        medido = next(s for s in observado["servidores"] if s["nombre"] == servidor["nombre"])
        if medido["estado"] != "ok" or medido["piezas"] is None:
            acta["motivo"] = "no se pudo medir el servidor"
            return acta
        estado.tomar_ventana(db, fecha, servidor["nombre"])
        candidatas = sorted((p for p in medido["piezas"] if p.get("candidata")),
                            key=lambda p: (p["candidata"]["plazo"]["vence"], p["pieza"]))
        for pieza in candidatas:
            c = pieza["candidata"]
            paso = {"pieza": pieza["pieza"], "de": c["actual"], "a": c["propuesta"], "resultado": "bloqueada"}
            acta["pasos"].append(paso)
            if estado.ventana(db, fecha)["aplicadas"] >= cfg["max_por_ventana"]:
                paso["compuertas"] = [("tope de la ventana", False, f"ya se aplicaron {cfg['max_por_ventana']}")]
                continue
            paso["compuertas"] = compuertas(cfg, db, servidor["nombre"], pieza, presente, correr)
            if not all(ok for _, ok, _ in paso["compuertas"]):
                continue
            estado.pasar(db, c["id"], "aplicando", "", int(reloj()))
            resultado = "hecho"
            for servicio in _servicios(pieza):
                resultado = _aplicar_servicio(cfg, db, servidor, c, servicio, pieza["playbook"], tomar, correr, reloj,
                                              dormir)
                if resultado != "hecho":
                    break
            ahora = int(reloj())
            if resultado == "hecho":
                paso["resultado"] = "aplicada"
                estado.pasar(db, c["id"], "cerrada", "aplicada por el vigía y verificada", ahora)
                estado.estrenar(db, pieza["playbook"], c["id"], ahora)
                estado.contar_aplicada(db, fecha)
                for clase in ("fallo", "plazo"):
                    estado.cerrar_incidente(db, f"{clase}-{c['id']}", ahora)
            elif resultado == "vuelto":
                fallas = sum(1 for e in estado.diario(db) if e["candidata"] == c["id"]
                             and e["paso"] in ("aplicar", "respaldar") and e["resultado"] == "fallo")
                paso["resultado"] = "falló; quedó como estaba"
                if fallas >= MAX_FALLAS:
                    estado.pasar(db, c["id"], "escalada", f"no entró en {fallas} intentos", ahora)
                    estado.incidente(db, f"fallo-{c['id']}", "no entró",
                                     f"En {servidor['nombre']}, {pieza['pieza']} {c['actual']} → {c['propuesta']} falló "
                                     f"{fallas} veces. Se volvió atrás bien las dos; no se intenta más sola.", ahora)
                else:
                    estado.pasar(db, c["id"], "esperando", "un intento falló y se volvió atrás", ahora)
            else:
                paso["resultado"] = "falló y la vuelta atrás también"
                estado.pasar(db, c["id"], "escalada", "falló y la vuelta atrás también", ahora)
                estado.incidente(db, f"roto-{c['id']}", "vuelta atrás fallida",
                                 f"En {servidor['nombre']}, {pieza['pieza']} {c['actual']} → {c['propuesta']} falló y la "
                                 "vuelta atrás tampoco verificó. El servicio puede estar caído. El vigía frenó la ventana.",
                                 ahora)
                acta["motivo"] = "se frenó la ventana: una vuelta atrás falló"
                break
        return acta
    finally:
        try:
            acta["ficha"] = informe.publicar_acta(cfg["registro_dir"], acta)
            acta["avisos"] = corrida.avisar(cfg, db, int(reloj()), correr)
        finally:
            db.close()
            try:
                os.unlink(candado)
            except OSError:
                pass
