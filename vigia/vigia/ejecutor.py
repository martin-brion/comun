"""El ejecutor: aplica, en su ventana, las candidatas que pasan todas las compuertas.

No sabe hacer nada por sí mismo en un servidor: llama acciones con nombre, de una lista cerrada, que la instancia define
como comandos en lista (nunca una línea de shell). Antes de cada efecto deja la intención en el diario; después, el
resultado. Si algo falla, vuelve atrás con la referencia exacta que había. Tras un corte mira qué quedó a medias y lo
resuelve midiendo: nunca repite una acción a ciegas.

Cuatro tipos de acción: `imagen` (la vuelta atrás es la imagen anterior), `datos` (antes copia los datos del servicio
bajo una marca de ese intento; la vuelta atrás repone esa copia exacta y recién después levanta la imagen anterior),
`propia` (una imagen propia se rehace igual sobre la versión nueva de su base; la vuelta atrás es la imagen armada sobre
la base anterior, que la instancia conserva) y `reinicio` (el servidor, cuando el sistema lo pide; no tiene vuelta atrás:
va último y, si algo no vuelve, escala).

Dos ventanas: la ordinaria, que toma todo, y la de urgencia, que solo toma una candidata con una falla explotada en algo
expuesto y arreglo publicado. Esa candidata no espera la maduración, pero antes se comprueba con el escáner que la
versión propuesta ya no tiene esa falla.

Lo que frena por algo que solo una persona destraba no queda en silencio: el estreno pendiente se avisa una vez, y una
candidata trabada dos ventanas seguidas en la misma compuerta, también.
"""
import os
import re
import string
import subprocess
import time

from . import censo, config as _config, corrida, escaner, estado, fuentes, informe

_SERVICIO = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_PROYECTO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_FIJA = re.compile(r"^[a-z0-9][A-Za-z0-9._/:-]{0,299}@(sha256:[0-9a-f]{64})$")
_MARCA = re.compile(r"^m[0-9]{9,12}$")
_PATRON = {"servicio": _SERVICIO, "referencia": _FIJA, "marca": _MARCA, "base": _FIJA, "proyecto": _PROYECTO}
_BASE = "org.opencontainers.image.base.digest"
_VERSION = "org.opencontainers.image.version"
MAX_FALLAS = 2
TOPE_ARMADO = 3600                 # armar una imagen entera puede llevar más que los 15 minutos de las demás acciones
TOPE_REINICIO = 300
PASO_S = 10
# Compuertas que solo destraba una persona: si una candidata frena en la misma dos ventanas seguidas, se avisa.
HUMANAS = ("acciones", "imagen fija", "vuelta atrás", "servicios", "arranque medible", "respaldo")
VENTANAS_TRABADA = 2


def _correr(argv, tope):
    """Código de salida de una acción; None si no existe o no terminó. La salida de la acción no se guarda."""
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=tope).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def _huecos(plantilla):
    return {campo for x in plantilla for _, campo, _, _ in string.Formatter().parse(x) if campo}


def accion(cfg, servidor, nombre, valores, correr, tope=900):
    """Corre una acción con nombre. True si salió con cero, False si no, None si la instancia no la definió."""
    plantilla = cfg["acciones"].get(servidor, {}).get(nombre)
    if plantilla is None:
        return None
    for campo in set(_config.ACCIONES[nombre]) | _huecos(plantilla):
        if not isinstance(valores.get(campo), str) or not _PATRON[campo].fullmatch(valores[campo]):
            return False
    return correr([x.format(**valores) for x in plantilla], tope) == 0


def _en(v, tm):
    return v is not None and tm.tm_wday in v["dias"] and v["desde"] <= f"{tm.tm_hour:02d}:{tm.tm_min:02d}" < v["hasta"]


def en_ventana(cfg, tm):
    return _en(cfg["ventana"], tm)


def en_ventana_urgente(cfg, tm):
    return _en(cfg["ventana_urgente"], tm)


def _con_proyecto(cfg, servidor, playbook):
    """¿Todas las acciones de servicio de ese tipo nombran el proyecto de compose?"""
    mapa = cfg["acciones"].get(servidor, {})
    propias = [a for a in _config.PLAYBOOKS.get(playbook, ()) if a in _config.OPCIONALES and a in mapa]
    return bool(propias) and all("proyecto" in _huecos(mapa[a]) for a in propias)


def _servicios(pieza, todos=None, con_proyecto=False):
    """[(proyecto, servicio)] de los servicios de compose que usan la imagen, o None si alguno no se puede nombrar sin
    ambigüedad. Si las acciones no reciben el proyecto, el nombre del servicio tiene que ser único en todo el servidor."""
    pares = [tuple(s.split("/", 1)) for s in pieza["servicios"]]
    if not pares or len(pares) != len(pieza["contenedores"]):
        return None
    if not all(_PROYECTO.fullmatch(p) and _SERVICIO.fullmatch(s) for p, s in pares):
        return None
    nombres = [s for _, s in pares]
    if len(set(nombres)) != len(nombres) and not con_proyecto:
        return None
    if not con_proyecto and todos is not None:
        en_servidor = [x.split("/", 1)[1] for x in todos]
        if any(en_servidor.count(s) > 1 for s in nombres):
            return None
    return pares


def _arreglo(candidata):
    """Los KEV por los que esta candidata va sin esperar la maduración, o una lista vacía."""
    urgencia = candidata.get("urgencia") or []
    return urgencia if candidata["compuerta_version"].startswith("espera:") else []


def _urgente(candidata):
    """Los KEV por los que esta candidata es urgente, haya madurado o no: el arreglo se comprueba igual."""
    return candidata.get("urgencia") or []


def compuertas(cfg, db, servidor, pieza, presente, correr, todos=None, comprobar=None):
    """[(nombre, pasa, detalle)] en orden. El respaldo va al final: es la única que corre un comando del servidor."""
    c = pieza["candidata"]
    urgencia = _arreglo(c)
    g = [("versión", c["compuerta_version"] == "pasaría" or bool(urgencia),
          c["compuerta_version"] if not urgencia else
          f"urgencia: {', '.join(urgencia)} con explotación conocida; no espera la maduración")]
    playbook = pieza.get("playbook")
    habilitado = playbook in cfg["playbooks"]
    # Una propia solo con `propia` y el sistema solo con `reinicio`, y al revés: si no, se pondría a correr la imagen
    # base sola o se reiniciaría el servidor por una imagen.
    corresponde = ((playbook == "propia") == bool(pieza.get("propia"))
                   and (playbook == "reinicio") == bool(pieza.get("sistema")))
    clase = "el sistema" if pieza.get("sistema") else ("una pieza propia" if pieza.get("propia") else "una pieza pública")
    g.append(("tipo de acción", habilitado and corresponde, playbook if habilitado and corresponde else (
        "la pieza no tiene un tipo de acción habilitado" if not habilitado else
        f"el tipo «{playbook}» no corresponde a {clase}")))
    if not (habilitado and corresponde):
        return g
    faltan = [a for a in _config.PLAYBOOKS[playbook] if a not in cfg["acciones"].get(servidor, {})]
    g.append(("acciones", not faltan, "definidas" if not faltan else "faltan: " + ", ".join(faltan)))
    estrenado = estado.estrenado(db, playbook)
    g.append(("estreno", presente or estrenado, "ya estrenado" if estrenado else (
        "primera vez, con el dueño presente" if presente else "la primera vez de este tipo pide al dueño presente")))
    if playbook == "reinicio":
        g.append(("arranque medible", pieza.get("arrancado") is not None,
                  "se sabe cuándo arrancó" if pieza.get("arrancado") else "no se sabe cuándo arrancó el servidor: no se "
                  "podría comprobar el reinicio"))
    else:
        g.append(("imagen fija", bool(c.get("referencia")) and bool(_FIJA.fullmatch(c["referencia"])),
                  "con huella" if c.get("referencia") else "no se conoce la huella de la versión propuesta"))
        g.append(("vuelta atrás", bool(c.get("anterior")) and bool(_FIJA.fullmatch(c["anterior"])),
                  "con la referencia exacta de hoy" if c.get("anterior") else "no se conoce la huella de la imagen de hoy"))
        pares = _servicios(pieza, todos, _con_proyecto(cfg, servidor, playbook))
        g.append(("servicios", pares is not None, "de compose" if pares else
                  "no se pueden nombrar sin ambigüedad (dos con el mismo nombre y acciones sin {proyecto})"))
    fila = estado.candidata(db, c["id"])
    if fila and fila["estado"] == "escalada":
        g.append(("sin escalar", False, "espera a una persona: " + fila["motivo"]))
    if _urgente(c) and all(ok for _, ok, _ in g):
        ok, detalle = (comprobar(c["referencia"], _urgente(c)) if comprobar and c.get("referencia")
                       else (False, "no hay con qué escanear"))
        g.append(("arreglo comprobado", ok, detalle))
    if all(ok for _, ok, _ in g):
        respaldo = accion(cfg, servidor, "respaldo_vigente", {}, correr)
        g.append(("respaldo", respaldo is True, "verificado" if respaldo else "no hay un respaldo vigente verificado"))
    return g


def _huella(foto, contenedor):
    """Las huellas de la imagen con que corre un contenedor."""
    imagen = foto["imagenes"].get(contenedor["imagen_id"], {})
    return {d.rpartition("@")[2] for d in imagen.get("digests", [])} | {contenedor["imagen_ref"].rpartition("@")[2]}


def _base(foto, contenedor):
    """La huella de la base sobre la que se armó la imagen de un contenedor, según sus rótulos (vacío si no la dice)."""
    huella = foto["imagenes"].get(contenedor["imagen_id"], {}).get("etiquetas", {}).get(_BASE, "")
    return {huella} if huella else set()


def _medida(playbook, version=None):
    """Qué se compara para saber con qué corre un servicio: la imagen o, en una propia, la base de su imagen, y solo si
    además es la misma versión (una vuelta atrás a otra versión armada sobre la misma base no cuenta)."""
    if playbook != "propia":
        return _huella

    def medir(foto, contenedor):
        rotulos = foto["imagenes"].get(contenedor["imagen_id"], {}).get("etiquetas", {})
        return _base(foto, contenedor) if version is None or rotulos.get(_VERSION) == version else set()
    return medir


def _mide(cfg, servidor, servicio, tomar, reloj, proyecto=None):
    """(foto, contenedores de ese servicio) o (None, None) si no se pudo medir."""
    t = tomar(servidor, int(reloj()), cfg["foto_vigencia_s"])
    if t["estado"] != "ok" or t["foto"]["contenedores"] is None:
        return None, None
    return t["foto"], [c for c in t["foto"]["contenedores"]
                       if c["servicio"] == servicio and (proyecto is None or c["proyecto"] == proyecto)]


def _sano(cfg, servidor, servicio, huella, tomar, reloj, dormir, medida=_huella, proyecto=None):
    """Espera a que el servicio corra con esa imagen (o, en una propia, sobre esa base) y esté sano. No alcanza con que el
    comando haya salido bien."""
    limite = reloj() + cfg["espera_salud_s"]
    while True:
        foto, suyos = _mide(cfg, servidor, servicio, tomar, reloj, proyecto)
        if foto is not None and suyos and all(
                c["estado"] == "running" and c["salud"] in ("healthy", "") and huella in medida(foto, c) for c in suyos):
            return True
        if reloj() >= limite:
            return False
        dormir(PASO_S)


def _valores(par, **otros):
    proyecto, servicio = par
    return dict(otros, servicio=servicio, proyecto=proyecto)


def _volver(cfg, db, servidor, c, par, tomar, correr, reloj, dormir, playbook=None):
    """Deja el servicio con la imagen anterior y lo verifica. «vuelto» si quedó como estaba; si no, «roto»."""
    nombre = servidor["nombre"]
    proyecto, servicio = par
    datos = {"servicio": servicio, "proyecto": proyecto, "a": c["anterior"]}
    if playbook == "propia":
        datos.update(tipo=playbook, version=c.get("version"))
    m = estado.intencion(db, c["id"], nombre, "volver", datos, int(reloj()))
    if playbook == "propia":
        vuelta = accion(cfg, nombre, "volver_propia", _valores(par, base=c["anterior"]), correr) is True
    else:
        vuelta = accion(cfg, nombre, "volver_imagen", _valores(par, referencia=c["anterior"]), correr) is True
    vuelta = vuelta and _sano(cfg, servidor, servicio, _FIJA.fullmatch(c["anterior"]).group(1), tomar, reloj, dormir,
                              _medida(playbook, c.get("version")), proyecto)
    vuelta = vuelta and accion(cfg, nombre, "verificar", _valores(par), correr) is True
    estado.resultado(db, m, "hecho" if vuelta else "fallo", "", int(reloj()))
    return "vuelto" if vuelta else "roto"


def _aplicar_servicio(cfg, db, servidor, c, par, playbook, tomar, correr, reloj, dormir):
    """«hecho», «vuelto» (falló y quedó como estaba) o «roto» (falló y la vuelta atrás también).

    Con el tipo `datos` copia antes los datos del servicio bajo una marca propia de este intento y, si la versión nueva
    falla, repone esa copia ANTES de levantar la imagen anterior: la imagen vieja no arranca sobre datos ya migrados."""
    nombre = servidor["nombre"]
    proyecto, servicio = par
    marca = None
    if playbook == "propia":
        # Se arma y se prueba aislada antes de tocar lo que corre. Si no se arma, nada cambió: no hay vuelta atrás que hacer.
        n = estado.intencion(db, c["id"], nombre, "construir",
                             {"servicio": servicio, "proyecto": proyecto, "de": c["anterior"], "a": c["referencia"],
                              "tipo": playbook, "version": c.get("version")}, int(reloj()))
        armada = accion(cfg, nombre, "construir_propia", _valores(par, base=c["referencia"]), correr,
                        TOPE_ARMADO) is True
        estado.resultado(db, n, "hecho" if armada else "fallo",
                         "" if armada else "no se pudo armar o no pasó su prueba aislada; no se tocó lo que corre",
                         int(reloj()))
        if not armada:
            return "vuelto"
    if playbook == "datos":
        marca = f"m{int(reloj())}"
        n = estado.intencion(db, c["id"], nombre, "respaldar",
                             {"servicio": servicio, "proyecto": proyecto, "de": c["anterior"], "marca": marca}, int(reloj()))
        copiado = accion(cfg, nombre, "respaldar_datos", _valores(par, marca=marca), correr) is True
        estado.resultado(db, n, "hecho" if copiado else "fallo",
                         "" if copiado else "no se pudo copiar; la versión nueva no llegó a correr", int(reloj()))
        if not copiado:
            # La copia deja el servicio detenido: se lo levanta con lo que tenía. Sus datos no cambiaron.
            return _volver(cfg, db, servidor, c, par, tomar, correr, reloj, dormir, playbook)
    intento = {"servicio": servicio, "proyecto": proyecto, "de": c["anterior"], "a": c["referencia"]}
    if playbook == "propia":
        intento.update(tipo=playbook, version=c.get("version"))
    if marca:
        intento["marca"] = marca
    n = estado.intencion(db, c["id"], nombre, "aplicar", intento, int(reloj()))
    if playbook == "propia":
        bien = accion(cfg, nombre, "aplicar_propia", _valores(par, base=c["referencia"]), correr) is True
    else:
        bien = accion(cfg, nombre, "aplicar_imagen", _valores(par, referencia=c["referencia"]), correr) is True
    bien = bien and _sano(cfg, servidor, servicio, c["indice"], tomar, reloj, dormir, _medida(playbook, c.get("version")),
                          proyecto)
    bien = bien and accion(cfg, nombre, "verificar", _valores(par), correr) is True
    if bien:
        estado.resultado(db, n, "hecho", "", int(reloj()))
        return "hecho"
    estado.resultado(db, n, "fallo", "no aplicó o no pasó la verificación", int(reloj()))
    if marca:
        r = estado.intencion(db, c["id"], nombre, "restaurar", {"servicio": servicio, "proyecto": proyecto, "marca": marca},
                             int(reloj()))
        repuesto = accion(cfg, nombre, "restaurar_datos", _valores(par, marca=marca), correr) is True
        estado.resultado(db, r, "hecho" if repuesto else "fallo", "", int(reloj()))
        if not repuesto:
            return "roto"
    return _volver(cfg, db, servidor, c, par, tomar, correr, reloj, dormir, playbook)


def _corriendo(foto):
    """Los servicios de compose que corren sanos en una foto: {(proyecto, servicio)}."""
    return {(c["proyecto"], c["servicio"]) for c in foto["contenedores"] or []
            if c["proyecto"] and c["servicio"] and c["estado"] == "running" and c["salud"] in ("healthy", "")}


def _volvieron(cfg, servidor, antes, tomar, reloj, dormir):
    """Espera a que vuelvan a correr sanos todos los servicios que corrían antes del reinicio."""
    limite = reloj() + cfg["espera_salud_s"]
    while True:
        t = tomar(servidor, int(reloj()), cfg["foto_vigencia_s"])
        if t["estado"] == "ok" and t["foto"]["contenedores"] is not None and set(antes) <= _corriendo(t["foto"]):
            return True
        if reloj() >= limite:
            return False
        dormir(PASO_S)


def _reiniciar(cfg, db, servidor, c, tomar, correr, reloj, dormir):
    """«hecho» (reinició, volvió todo lo que corría y verificó), «vuelto» (no reinició: nada cambió) o «roto» (no volvió
    a responder, o volvió sin algo). Si el vigía corre en el mismo servidor, el reinicio lo corta: lo resuelve la pasada
    siguiente, midiendo (ver `recuperar`)."""
    nombre = servidor["nombre"]
    t = tomar(servidor, int(reloj()), cfg["foto_vigencia_s"])
    if t["estado"] != "ok" or t["foto"]["contenedores"] is None or t["foto"]["sistema"].get("arrancado") is None:
        return "vuelto"
    a0 = t["foto"]["sistema"]["arrancado"]
    antes = sorted(_corriendo(t["foto"]))
    m = estado.intencion(db, c["id"], nombre, "reiniciar",
                         {"tipo": "reinicio", "servicio": "", "arrancado": a0, "antes": [list(x) for x in antes]},
                         int(reloj()))
    accion(cfg, nombre, "reiniciar", {}, correr, TOPE_REINICIO)
    limite = reloj() + cfg["espera_reinicio_s"]
    arriba_igual = False
    while True:
        t = tomar(servidor, int(reloj()), cfg["foto_vigencia_s"])
        arranque = t["foto"]["sistema"].get("arrancado") if t["estado"] == "ok" else None
        if arranque is not None and arranque > a0:
            break
        arriba_igual = arranque == a0
        if reloj() >= limite:
            if arriba_igual:
                estado.resultado(db, m, "fallo", "no reinició; siguió todo como estaba", int(reloj()))
                return "vuelto"
            estado.resultado(db, m, "fallo", "no volvió a responder después de pedir el reinicio", int(reloj()))
            return "roto"
        dormir(PASO_S)
    bien = _volvieron(cfg, servidor, antes, tomar, reloj, dormir)
    bien = bien and accion(cfg, nombre, "verificar_servidor", {}, correr) is True
    estado.resultado(db, m, "hecho" if bien else "fallo", "" if bien else "reinició, pero no volvió todo o no verificó",
                     int(reloj()))
    return "hecho" if bien else "roto"


def _recuperar_reinicio(cfg, db, e, servidor, tomar, reloj, correr):
    """Un reinicio a medias (casi siempre porque el reinicio cortó al propio vigía). «resuelto», «espera» (todavía no se
    puede saber: el servidor recién arranca) o «ambiguo»."""
    ahora = int(reloj())
    t = tomar(servidor, ahora, cfg["foto_vigencia_s"]) if servidor else {"estado": "desconocido"}
    arranque = t["foto"]["sistema"].get("arrancado") if t["estado"] == "ok" else None
    reciente = ahora - e["abierta"] < cfg["espera_reinicio_s"]
    if arranque is None:
        if reciente:
            return "espera"
        estado.resultado(db, e["n"], "ambiguo", "tras pedir el reinicio no se pudo volver a medir el servidor", ahora)
        escalar(db, e["candidata"], e["servidor"], f"ambiguo-{e['candidata']}", "a medias",
                f"Se pidió reiniciar {e['servidor']} y desde entonces no se lo pudo medir: puede no haber vuelto.", ahora)
        return "ambiguo"
    if arranque <= e["datos"]["arrancado"]:
        if reciente:
            return "espera"
        estado.resultado(db, e["n"], "no aplicado", "recuperado tras un corte: el servidor no reinició", ahora)
        estado.pasar(db, e["candidata"], "detectada", "", ahora)
        return "resuelto"
    antes = {tuple(x) for x in e["datos"]["antes"]}
    if t["foto"]["contenedores"] is not None and antes <= _corriendo(t["foto"]) \
            and accion(cfg, servidor["nombre"], "verificar_servidor", {}, correr) is True:
        estado.resultado(db, e["n"], "hecho", "recuperado tras el reinicio: volvió todo y verificó", ahora)
        estado.pasar(db, e["candidata"], "cerrada", "reiniciado por el vigía y verificado", ahora)
        estado.estrenar(db, "reinicio", e["candidata"], ahora)
        estado.cerrar_incidente(db, "estreno-reinicio", ahora)
        return "resuelto"
    if ahora - arranque < cfg["espera_salud_s"] + 300:
        return "espera"
    estado.resultado(db, e["n"], "fallo", "reinició, pero no volvió todo o no verificó", ahora)
    escalar(db, e["candidata"], e["servidor"], f"roto-{e['candidata']}", "reinicio incompleto",
            f"{e['servidor']} reinició, pero no volvieron todos los servicios que corrían antes o no pasó "
            "`verificar_servidor`. No tiene vuelta atrás: revisarlo.", ahora)
    return "ambiguo"


RESUELTO = (" Cuando esté resuelto, decírselo al vigía: `python -m vigia resuelto --config <configuración> {id}`; hasta "
            "entonces no aplica nada más en ese servidor.")


def escalar(db, candidata_id, servidor, incidente, clase, texto, ahora):
    """Una candidata que espera a una persona, con su incidente, y el servidor bloqueado hasta que se resuelva: todo en
    una sola transacción."""
    estado.escalar(db, candidata_id, servidor, incidente, clase, texto + RESUELTO.format(id=incidente), ahora)


def _como_corre(cfg, servidor, par, de, a, medir, tomar, reloj):
    """«a» si el servicio corre sano lo nuevo, «de» si corre sano lo de antes, None si otra cosa o no se pudo medir."""
    proyecto, servicio = par
    foto, suyos = _mide(cfg, servidor, servicio, tomar, reloj, proyecto) if servidor else (None, None)
    if foto is None or not suyos or not all(c["estado"] == "running" and c["salud"] in ("healthy", "") for c in suyos):
        return None
    for nombre, huella in (("a", a), ("de", de)):
        if huella and all(huella in medir(foto, c) for c in suyos):
            return nombre
    return None


def _recuperar_intento(cfg, db, it, servidor, tomar, reloj, correr):
    """Decide cómo quedó un intento que un corte dejó abierto, con todos sus pasos ya medidos. Lo nuevo corriendo no
    alcanza: se verifica. Si no se puede acreditar ni lo nuevo ni lo de antes en todos sus servicios, escala."""
    ahora = int(reloj())
    d, cid = it["datos"], it["candidata"]
    medir = _medida(d.get("tipo"), d.get("version"))
    de, a = (_FIJA.fullmatch(d.get(k) or "") for k in ("de", "a"))
    pares = [tuple(x) for x in d["pares"]]
    como = [_como_corre(cfg, servidor, par, de and de.group(1), a and a.group(1), medir, tomar, reloj) for par in pares]
    if como and all(x == "de" for x in como):
        fallas = any(e["candidata"] == cid and e["n"] > it["paso_desde"] and e["resultado"] == "fallo"
                     for e in estado.diario(db))
        estado.pasar(db, cid, "esperando" if fallas else "detectada",
                     "un intento falló y se volvió atrás" if fallas else "", ahora)
        estado.cerrar_intento(db, it["n"], "no aplicado", ahora)
        return True
    if como and all(x == "a" for x in como) and all(
            accion(cfg, servidor["nombre"], "verificar", _valores(par), correr) is True for par in pares):
        estado.pasar(db, cid, "cerrada", "aplicada por el vigía y verificada (resuelto tras un corte)", ahora)
        estado.estrenar(db, d.get("tipo"), cid, ahora)
        for prefijo in ("estreno-" + str(d.get("tipo")), f"fallo-{cid}", f"plazo-{cid}", f"trabada-{cid}"):
            estado.cerrar_incidente(db, prefijo, ahora)
        if d.get("ventana") and estado.ventana(db, d["ventana"]):
            estado.contar_aplicada(db, d["ventana"])
        if d.get("sin_madurar"):
            estado.noticia(db, f"urgencia-{cid}-{ahora}", "aplicada por urgencia",
                           f"En {it['servidor']} se aplicó por urgencia {d.get('pieza', '')} → {d.get('propuesta', '')}, "
                           f"sin esperar la maduración, por {', '.join(d['sin_madurar'])}. Un corte la interrumpió y se "
                           "resolvió midiendo y verificando.", ahora)
        estado.cerrar_intento(db, it["n"], "hecho", ahora)
        return True
    copia = next((f" Su copia de ese intento lleva la marca {e['datos']['marca']}." for e in estado.diario(db)
                  if e["candidata"] == cid and e["n"] > it["paso_desde"] and e["datos"].get("marca")), "")
    escalar(db, cid, it["servidor"], f"ambiguo-{cid}", "a medias",
            f"En {it['servidor']}, un intento sobre {', '.join(s for _, s in pares)} quedó a medias tras un corte: no se "
            f"pudo acreditar que corra sano y verificado lo nuevo ni lo de antes; puede estar detenido.{copia}", ahora)
    estado.cerrar_intento(db, it["n"], "ambiguo", ahora)
    return False


def recuperar(cfg, db, tomar, reloj, correr=_correr):
    """Resuelve lo que un corte dejó a medias, midiendo. No corre ninguna acción que cambie algo (solo `verificar` y
    `verificar_servidor`, que comprueban). Primero mide cada paso que quedó abierto; después decide cada intento abierto
    (también los que el corte dejó entre dos pasos). Devuelve los servidores donde esta pasada no aplica nada: los
    bloqueados hasta que una persona diga que está resuelto, y los que todavía están volviendo de un reinicio."""
    esperan = set()
    por_nombre = {s["nombre"]: s for s in cfg["servidores"]}
    intentos = {it["candidata"]: it for it in estado.intentos_abiertos(db)}
    for it in intentos.values():
        it["paso_desde"] = max((e["n"] for e in estado.diario(db) if e["candidata"] == it["candidata"]
                                and e["abierta"] < it["abierto"]), default=0)
    for e in estado.a_medias(db):
        ahora = int(reloj())
        servidor = por_nombre.get(e["servidor"])
        if e["paso"] == "reiniciar":
            if _recuperar_reinicio(cfg, db, e, servidor, tomar, reloj, correr) != "resuelto":
                esperan.add(e["servidor"])
            continue
        envuelto = e["candidata"] in intentos
        proyecto = e["datos"].get("proyecto")
        foto, suyos = _mide(cfg, servidor, e["datos"]["servicio"], tomar, reloj, proyecto) if servidor else (None, None)
        medir = _medida(e["datos"].get("tipo"), e["datos"].get("version"))
        huellas = [medir(foto, c) for c in suyos] if suyos else []

        def todos(clave):
            fija = _FIJA.fullmatch(e["datos"].get(clave) or "")
            return bool(fija) and bool(huellas) and all(fija.group(1) in h for h in huellas)

        def seguir(nuevo, motivo=""):
            # Con un intento abierto, lo decide el intento entero, después.
            if not envuelto:
                estado.pasar(db, e["candidata"], nuevo, motivo, ahora)
        if e["paso"] == "construir" and todos("de"):
            # El armado no toca lo que corre: si sigue lo de siempre, no pasó nada.
            estado.resultado(db, e["n"], "no aplicado", "recuperado tras un corte: el armado no terminó y seguía lo de "
                             "siempre", ahora)
            seguir("detectada")
        elif e["paso"] == "respaldar" and todos("de"):
            # La copia no terminó, pero el servicio corre con lo de siempre: sus datos no se tocaron.
            estado.resultado(db, e["n"], "no aplicado", "recuperado tras un corte: la copia no terminó y seguía la versión "
                             "anterior", ahora)
            seguir("detectada")
        elif e["paso"] == "aplicar" and todos("a") and (envuelto or accion(
                cfg, e["servidor"], "verificar", {"servicio": e["datos"]["servicio"], "proyecto": proyecto or "x"},
                correr) is True):
            estado.resultado(db, e["n"], "hecho", "recuperado tras un corte: ya corría la versión nueva", ahora)
            seguir("detectada")
        elif e["paso"] == "aplicar" and todos("de"):
            estado.resultado(db, e["n"], "no aplicado", "recuperado tras un corte: seguía la versión anterior", ahora)
            seguir("detectada")
        elif e["paso"] == "volver" and todos("a"):
            estado.resultado(db, e["n"], "hecho", "recuperado tras un corte: ya había vuelto", ahora)
            seguir("esperando", "un intento falló y se volvió atrás")
        else:
            # Una restauración a medias cae siempre acá: cómo quedaron los datos no se puede medir desde afuera.
            estado.resultado(db, e["n"], "ambiguo", "tras un corte no se pudo saber cómo quedó", ahora)
            copia = f" Su copia de ese intento lleva la marca {e['datos']['marca']}." if e["datos"].get("marca") else ""
            escalar(db, e["candidata"], e["servidor"], f"ambiguo-{e['candidata']}", "a medias",
                    f"En {e['servidor']}, el servicio {e['datos']['servicio']} quedó a medias tras un corte "
                    f"(paso: {e['paso']}) y no se pudo saber cómo quedó; puede estar detenido.{copia}", ahora)
            if envuelto:
                estado.cerrar_intento(db, intentos.pop(e["candidata"])["n"], "ambiguo", ahora)
    for it in intentos.values():
        _recuperar_intento(cfg, db, it, por_nombre.get(it["servidor"]), tomar, reloj, correr)
    return esperan | estado.bloqueados(db)


def resuelto(cfg, incidente, ahora):
    """Una persona dice que un incidente está resuelto: se cierra, se levanta el bloqueo del servidor y su candidata,
    si estaba escalada, vuelve a quedar a la vista del vigía. False si no había un incidente abierto con ese nombre."""
    db = estado.abrir(cfg["estado_dir"])
    try:
        return estado.resolver(db, incidente, incidente.split("-", 1)[1] if "-" in incidente else "", ahora)
    finally:
        db.close()


def _elegir(cfg, db, fecha):
    """Un servidor por ventana: el de hoy si la ventana ya se tomó; si no, el que hace más tiempo que no tiene una."""
    hoy = estado.ventana(db, fecha)
    con_acciones = [s for s in cfg["servidores"] if cfg["acciones"].get(s["nombre"])]
    if hoy:
        return next((s for s in con_acciones if s["nombre"] == hoy["servidor"]), None)
    return min(con_acciones, key=lambda s: estado.ultima_ventana(db, s["nombre"]), default=None)


def _urgentes(medido):
    return [p for p in medido["piezas"] or [] if p.get("candidata") and p["candidata"].get("urgencia")]


def _elegir_urgente(cfg, db, clave, observado, excluidos=()):
    """En la ventana de urgencia: el servidor que ya la tomó hoy o, si no, el que tiene la urgencia de plazo más corto,
    entre los que no esperan a una persona."""
    hoy = estado.ventana(db, clave)
    medidos = {s["nombre"]: s for s in observado["servidores"] if s["estado"] == "ok" and s["piezas"] is not None}
    con_acciones = [s for s in cfg["servidores"] if cfg["acciones"].get(s["nombre"]) and s["nombre"] in medidos
                    and s["nombre"] not in excluidos]
    if hoy:
        return next((s for s in con_acciones if s["nombre"] == hoy["servidor"]), None)
    candidatos = [(min(p["candidata"]["plazo"]["vence"] for p in _urgentes(medidos[s["nombre"]])), s["nombre"], s)
                  for s in con_acciones if _urgentes(medidos[s["nombre"]])]
    return min(candidatos)[2] if candidatos else None


def _comprobador(cfg, servidor, escanear, kev):
    """Escanea la versión propuesta y dice si ya no tiene los KEV por los que se apura. Solo con el escáner local."""
    def comprobar(referencia, urgencia):
        if servidor["lector"] != "local" or not cfg["escaner"]["comando"]:
            return False, "el escáner corre en el propio servidor: no se puede comprobar el arreglo"
        r = escanear(cfg["escaner"]["comando"], [referencia], kev, cfg["escaner"]["tope_s"]).get(referencia) or {}
        if r.get("estado") != "ok":
            return False, "no se pudo escanear la versión propuesta: " + (r.get("motivo") or "sin resultado")
        siguen = sorted(set(urgencia) & set(r.get("kev") or []))
        return (not siguen, "la versión propuesta ya no tiene " + ", ".join(urgencia) if not siguen
                else "la versión propuesta sigue con " + ", ".join(siguen))
    return comprobar


def aplicar(cfg, presente=False, tomar=censo.tomar, pedir=fuentes.pedir, escanear=escaner.escanear, correr=_correr,
            reloj=time.time, dormir=time.sleep, local=time.localtime):
    """Una pasada del ejecutor. Devuelve el acta: qué miró, qué compuertas frenaron y qué hizo."""
    ahora = int(reloj())
    acta = {"ts": ahora, "presente": presente, "servidor": None, "motivo": "", "pasos": [], "ventana": "ordinaria"}
    candado = corrida._candado(cfg["estado_dir"], ahora)
    db = estado.abrir(cfg["estado_dir"])
    try:
        if os.path.exists(os.path.join(cfg["estado_dir"], "PAUSA")):
            acta["motivo"] = "en pausa: existe el archivo PAUSA"
            return acta
        ambiguos = recuperar(cfg, db, tomar, reloj, correr)
        tm = local(ahora)
        ordinaria = presente or en_ventana(cfg, tm)
        if not ordinaria and not en_ventana_urgente(cfg, tm):
            acta["motivo"] = "fuera de la ventana"
            return acta
        fecha = time.strftime("%Y-%m-%d", tm)
        if ordinaria:
            clave = fecha
            servidor = _elegir(cfg, db, fecha)
            if servidor is None:
                acta["motivo"] = "ningún servidor tiene acciones definidas, o la ventana de hoy fue de otro"
                return acta
            if servidor["nombre"] in ambiguos:
                # Toma igual su turno, como un servidor que no se pudo medir: los demás no esperan por él.
                estado.tomar_ventana(db, clave, servidor["nombre"])
                acta["servidor"] = servidor["nombre"]
                acta["motivo"] = ("espera a una persona (algo quedó roto o a medias) o un reinicio todavía se está "
                                  "resolviendo")
                return acta
            observado = corrida._correr(cfg, ahora, tomar, pedir, escanear, True)
        else:
            acta["ventana"] = "urgencia"
            clave = fecha + " urgencia"
            observado = corrida._correr(cfg, ahora, tomar, pedir, escanear, True)
            servidor = _elegir_urgente(cfg, db, clave, observado, ambiguos)
            if servidor is None:
                acta["motivo"] = "ventana de urgencia: no hay nada urgente"
                return acta
            if servidor["nombre"] in ambiguos:
                acta["servidor"] = servidor["nombre"]
                acta["motivo"] = "algo quedó a medias tras un corte o un reinicio todavía se está resolviendo"
                return acta
        acta["servidor"] = servidor["nombre"]
        medido = next(s for s in observado["servidores"] if s["nombre"] == servidor["nombre"])
        if medido["estado"] != "ok" or medido["piezas"] is None:
            # Toma igual su turno: si no, un servidor caído sería siempre «el que hace más que no tiene ventana» y los
            # demás no tendrían nunca la suya.
            estado.tomar_ventana(db, clave, servidor["nombre"])
            acta["motivo"] = "no se pudo medir el servidor"
            return acta
        estado.tomar_ventana(db, clave, servidor["nombre"])
        todos = [s for p in medido["piezas"] for s in p["servicios"]]
        comprobar = _comprobador(cfg, servidor, escanear, observado.get("_kev") or set())
        candidatas = sorted((p for p in medido["piezas"] if p.get("candidata")
                             and (ordinaria or p["candidata"].get("urgencia"))),
                            key=lambda p: (bool(p.get("sistema")), p["candidata"]["plazo"]["vence"], p["pieza"]))
        vistas = set()
        for pieza in candidatas:
            c = pieza["candidata"]
            paso = {"pieza": pieza["pieza"], "de": c["actual"], "a": c["propuesta"], "resultado": "bloqueada"}
            acta["pasos"].append(paso)
            if c["id"] in vistas:
                # La misma candidata en dos imágenes de la misma pieza: se intenta una vez por pasada.
                paso["compuertas"] = [("una vez por pasada", False, "ya se intentó en esta pasada")]
                continue
            vistas.add(c["id"])
            if estado.ventana(db, clave)["aplicadas"] >= cfg["max_por_ventana"]:
                paso["compuertas"] = [("tope de la ventana", False, f"ya se aplicaron {cfg['max_por_ventana']}")]
                continue
            if pieza.get("sistema") and any(p["resultado"] not in ("aplicada", "bloqueada") for p in acta["pasos"]):
                paso["compuertas"] = [("pasada sin fallas", False, "algo falló en esta pasada: no se reinicia encima")]
                continue
            paso["compuertas"] = compuertas(cfg, db, servidor["nombre"], pieza, presente, correr, todos, comprobar)
            _seguimiento_en(db, servidor["nombre"], pieza, paso["compuertas"], fecha, reloj)
            if not all(ok for _, ok, _ in paso["compuertas"]):
                continue
            urgencia = _arreglo(c)
            estado.pasar(db, c["id"], "aplicando", "", int(reloj()))
            resultado, hechos, intento = "hecho", [], None
            if pieza.get("sistema"):
                resultado = _reiniciar(cfg, db, servidor, c, tomar, correr, reloj, dormir)
            else:
                pares = _servicios(pieza, todos, _con_proyecto(cfg, servidor["nombre"], pieza["playbook"]))
                intento = estado.abrir_intento(db, c["id"], servidor["nombre"], {
                    "tipo": pieza["playbook"], "pares": [list(x) for x in pares], "de": c["anterior"],
                    "a": c["referencia"], "version": c.get("version"), "ventana": clave, "sin_madurar": urgencia,
                    "pieza": pieza["pieza"], "propuesta": c["propuesta"]}, int(reloj()))
                for par in pares:
                    resultado = _aplicar_servicio(cfg, db, servidor, c, par, pieza["playbook"], tomar, correr, reloj,
                                                  dormir)
                    if resultado != "hecho":
                        break
                    hechos.append(par)
            if resultado == "vuelto" and hechos:
                # Los servicios que ya habían cambiado vuelven también: «quedó como estaba» tiene que ser cierto para todos.
                # Con `datos` no: volver la imagen sobre datos ya migrados los rompería; eso lo ve una persona.
                if pieza["playbook"] == "datos":
                    resultado = "a medias"
                for par in reversed(hechos if resultado == "vuelto" else []):
                    if _volver(cfg, db, servidor, c, par, tomar, correr, reloj, dormir, pieza["playbook"]) != "vuelto":
                        resultado = "roto"
            ahora = int(reloj())
            if resultado == "hecho":
                paso["resultado"] = "aplicada"
                estado.pasar(db, c["id"], "cerrada", "aplicada por el vigía y verificada", ahora)
                estado.estrenar(db, pieza["playbook"], c["id"], ahora)
                estado.cerrar_incidente(db, f"estreno-{pieza['playbook']}", ahora)
                estado.contar_aplicada(db, clave)
                for prefijo in ("fallo", "plazo", "trabada"):
                    estado.cerrar_incidente(db, f"{prefijo}-{c['id']}", ahora)
                if urgencia:
                    estado.noticia(db, f"urgencia-{c['id']}-{ahora}", "aplicada por urgencia",
                                   f"En {servidor['nombre']} se aplicó {pieza['pieza']} {c['actual']} → {c['propuesta']} "
                                   f"sin esperar la maduración, por {', '.join(urgencia)} (falla con explotación "
                                   "conocida, en algo que da a internet). Verificada; el escáner comprobó que la versión "
                                   "nueva ya no la tiene.", ahora)
                _cerrar(db, intento, resultado, ahora)
            elif resultado == "vuelto":
                fallas = sum(1 for e in estado.diario(db) if e["candidata"] == c["id"]
                             and e["paso"] in ("aplicar", "respaldar", "construir", "reiniciar") and e["resultado"] == "fallo")
                paso["resultado"] = "falló; quedó como estaba"
                if fallas >= MAX_FALLAS:
                    estado.pasar(db, c["id"], "escalada", f"no entró en {fallas} intentos", ahora)
                    armado = all(e["paso"] == "construir" for e in estado.diario(db)
                                 if e["candidata"] == c["id"] and e["resultado"] == "fallo")
                    como = ("no se pudo armar o no pasó su prueba aislada; no se tocó lo que corre" if armado else
                            "se volvió atrás bien cada vez")
                    estado.incidente(db, f"fallo-{c['id']}", "no entró",
                                     f"En {servidor['nombre']}, {pieza['pieza']} {c['actual']} → {c['propuesta']} falló "
                                     f"{fallas} veces: {como}. No se intenta más sola.", ahora)
                else:
                    estado.pasar(db, c["id"], "esperando", "un intento falló y se volvió atrás", ahora)
                _cerrar(db, intento, resultado, ahora)
            elif resultado == "a medias":
                paso["resultado"] = "falló a medias"
                escalar(db, c["id"], servidor["nombre"], f"roto-{c['id']}", "a medias",
                        f"En {servidor['nombre']}, {pieza['pieza']} {c['actual']} → {c['propuesta']} cambió en "
                        f"{', '.join(s for _, s in hechos)} y falló en el siguiente, que quedó como estaba. Con "
                        "datos no se vuelve atrás a ciegas: revisarlo.", ahora)
                _cerrar(db, intento, resultado, ahora)
                acta["motivo"] = "se frenó la ventana: una pieza quedó a medias"
                break
            else:
                paso["resultado"] = ("el reinicio no volvió bien" if pieza.get("sistema")
                                     else "falló y la vuelta atrás también")
                texto = (f"{servidor['nombre']} no volvió bien del reinicio: no respondió, o no volvieron todos los "
                         "servicios que corrían antes, o no pasó `verificar_servidor`. No tiene vuelta atrás: revisarlo."
                         if pieza.get("sistema") else
                         f"En {servidor['nombre']}, {pieza['pieza']} {c['actual']} → {c['propuesta']} falló y la "
                         "vuelta atrás tampoco verificó. El servicio puede estar caído.")
                escalar(db, c["id"], servidor["nombre"], f"roto-{c['id']}", "reinicio incompleto" if pieza.get("sistema")
                        else "vuelta atrás fallida", texto, ahora)
                _cerrar(db, intento, resultado, ahora)
                acta["motivo"] = "se frenó la ventana: " + paso["resultado"]
                break
        return acta
    except BaseException:
        acta["fallo"] = True
        raise
    finally:
        try:
            acta["ficha"] = informe.publicar_acta(cfg["registro_dir"], acta)
            acta["avisos"] = corrida.avisar(cfg, db, int(reloj()), correr)
            if not acta.get("fallo"):
                # Solo una pasada que terminó deja su latido: una que falla siempre no puede parecer sana.
                corrida.latido(cfg, "ejecutor", int(reloj()))
        finally:
            db.close()
            candado.soltar()


def _cerrar(db, intento, resultado, ahora):
    """El intento se cierra último: recién cuando la candidata, el incidente y el bloqueo ya quedaron escritos. Un corte
    antes deja el intento abierto, y la pasada siguiente lo vuelve a medir."""
    if intento:
        estado.cerrar_intento(db, intento, resultado, ahora)


def _seguimiento_en(db, servidor, pieza, compuertas_, fecha, reloj):
    """El seguimiento de las compuertas, con la hora del reloj de la pasada."""
    ahora = int(reloj())
    c = pieza["candidata"]
    frenos = [(n, d) for n, ok, d in compuertas_ if not ok]
    nombres = [n for n, _ in frenos]
    playbook = pieza.get("playbook")
    if any(n == "respaldo" and ok for n, ok, _ in compuertas_):
        estado.cerrar_incidente(db, f"respaldo:{servidor}", ahora)
    if nombres == ["estreno"]:
        estado.incidente(db, f"estreno-{playbook}", "estreno pendiente",
                         f"El tipo de acción «{playbook}» tiene su primera aplicación lista: en {servidor}, "
                         f"{c['pieza']} {c['actual']} → {c['propuesta']}. La primera vez de cada tipo pide al dueño "
                         "presente: `python -m vigia aplicar --presente`. Hasta entonces no se aplica nada de este tipo.",
                         ahora)
    # El estreno no tapa una compuerta de persona: si además frena otra, la que manda es esa.
    humanas = [(n, d) for n, d in frenos if n != "estreno"]
    if humanas and humanas[0][0] in HUMANAS and not set(nombres) & {"versión", "tipo de acción"}:
        n, detalle = humanas[0]
        veces = estado.bloqueo(db, c["id"], n, detalle, fecha)
        if veces < VENTANAS_TRABADA:
            return
        if n == "respaldo":
            estado.incidente(db, f"respaldo:{servidor}", "sin respaldo vigente",
                             f"En {servidor}, `respaldo_vigente` no dio un respaldo verificado en {veces} ventanas "
                             "seguidas: el vigía no aplica nada ahí hasta que vuelva a haberlo.", ahora)
        else:
            estado.incidente(db, f"trabada-{c['id']}", "trabada",
                             f"En {servidor}, {c['pieza']} {c['actual']} → {c['propuesta']} frenó en {veces} ventanas "
                             f"seguidas en la compuerta «{n}»: {detalle}. Hace falta una persona.", ahora)
    else:
        estado.desbloquear(db, c["id"])
        estado.cerrar_incidente(db, f"trabada-{c['id']}", ahora)
