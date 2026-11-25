"""Una corrida completa: foto de cada servidor, fuentes, análisis, estado y registro. Solo observa.

Además de las candidatas, abre los incidentes de lo que solo una persona puede resolver y que duró lo suficiente para no
ser un tropiezo: un servidor que no se deja medir, parches de seguridad del sistema que no entran, un KEV en algo
expuesto sin arreglo publicado, una máquina virtual ausente y el vigía en pausa por demasiado tiempo. Cada uno se
cierra solo cuando deja de pasar.
"""
import json
import os
import re
import sqlite3
import subprocess
import time

from . import analisis, censo, escaner, estado, fuentes, informe, maquinas, repos

CANDADO_VIEJO_S = 2 * 3600
SIN_MEDIR_S = 24 * 3600
BOOT_ID = "/proc/sys/kernel/random/boot_id"


class Ocupado(Exception):
    pass


def _arranque_actual():
    try:
        with open(BOOT_ID, encoding="ascii") as f:
            return f.read(64).strip()
    except OSError:
        return ""


def _candado(directorio, ahora, arranque=_arranque_actual):
    """Una corrida a la vez. Un candado de más de dos horas es de una corrida que murió: se toma. También el de un
    arranque anterior del servidor: un reinicio mata la corrida que lo pidió y su candado queda en el disco."""
    os.makedirs(directorio, exist_ok=True)
    ruta = os.path.join(directorio, "corrida.candado")
    actual = arranque()
    try:
        with open(ruta, encoding="ascii", errors="replace") as f:
            previo = f.read(64).strip()
        if ahora - os.stat(ruta).st_mtime > CANDADO_VIEJO_S or (actual and previo and previo != actual):
            os.unlink(ruta)
    except OSError:
        pass
    try:
        fd = os.open(ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Ocupado("hay otra corrida en curso") from None
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(actual)
    return ruta


def _avisador(argv, tope):
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=tope).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def _archivo(i):
    return re.sub(r"[^A-Za-z0-9_-]", "-", i["id"])


def avisar(cfg, db, ahora, correr=_avisador):
    """Deja un archivo por incidente que pide aviso (uno al abrirse y un recordatorio por día), uno por noticia y uno por
    cada cierre de un incidente avisado; si la instancia definió un comando de aviso, se lo pasa. El canal es de la
    instancia: no puede depender de lo que el vigía actualiza."""
    carpeta = os.path.join(cfg["estado_dir"], "avisos")
    rutas = []
    for i in estado.por_avisar(db, ahora):
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, _archivo(i) + ".txt")
        titulo = "aviso" if i["tipo"] == "noticia" else i["clase"]
        informe._escribir(ruta, f"Vigía de parches · {titulo}\n\n{i['texto']}\n")
        rutas.append(ruta)
        if cfg["aviso"]:
            correr(list(cfg["aviso"]) + [ruta], 120)
    for i in estado.por_avisar_cierre(db, ahora):
        os.makedirs(carpeta, exist_ok=True)
        try:
            os.unlink(os.path.join(carpeta, _archivo(i) + ".txt"))
        except OSError:
            pass
        ruta = os.path.join(carpeta, _archivo(i) + ".cerrado.txt")
        informe._escribir(ruta, f"Vigía de parches · resuelto: {i['clase']}\n\nSe resolvió el "
                                f"{informe._cuando(i['cerrado'])}. Era:\n\n{i['texto']}\n")
        rutas.append(ruta)
        if cfg["aviso"]:
            correr(list(cfg["aviso"]) + [ruta], 120)
    return rutas


def latido(cfg, clave, ahora):
    """Deja la hora de la última corrida y de la última pasada del ejecutor: un monitor de afuera la mira (`salud`)."""
    ruta = os.path.join(cfg["estado_dir"], "latido.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        datos = datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        datos = {}
    datos[clave] = ahora
    os.makedirs(cfg["estado_dir"], exist_ok=True)
    informe._escribir(ruta, json.dumps(datos, sort_keys=True) + "\n")


def salud(cfg, ahora, horas):
    """(código, texto) para un monitor de afuera: 0 si el vigía corrió hace menos de `horas` y no está en pausa de más;
    4 si no. El monitor no puede vivir en lo que el vigía vigila."""
    try:
        with open(os.path.join(cfg["estado_dir"], "latido.json"), encoding="utf-8") as f:
            ultima = json.load(f).get("corrida")
    except (OSError, ValueError, AttributeError):
        ultima = None
    if type(ultima) is not int:
        return 4, "vigía: sin corridas registradas"
    problemas = []
    if ahora - ultima > horas * 3600:
        problemas.append(f"la última corrida fue hace {(ahora - ultima) // 3600} h")
    pausa = os.path.join(cfg["estado_dir"], "PAUSA")
    if os.path.exists(pausa) and ahora - os.stat(pausa).st_mtime > cfg["pausa_max_dias"] * 86400:
        problemas.append(f"en pausa hace más de {cfg['pausa_max_dias']} días")
    # Solo lectura: la corre un monitor, quizás con otro usuario, y no puede crear ni cambiar nada.
    try:
        ruta = os.path.abspath(os.path.join(cfg["estado_dir"], "vigia.sqlite3")).replace("\\", "/")
        db = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=10)
        try:
            abiertos = db.execute("SELECT COUNT(*) FROM incidentes WHERE cerrado IS NULL AND "
                                  "COALESCE(tipo, 'incidente') = 'incidente'").fetchone()[0]
        finally:
            db.close()
    except sqlite3.Error:
        abiertos = "sin dato"
    if problemas:
        return 4, "vigía: " + "; ".join(problemas) + f" · incidentes abiertos: {abiertos}"
    return 0, f"vigía: bien · incidentes abiertos: {abiertos}"


def correr(cfg, ahora=None, tomar=censo.tomar, pedir=fuentes.pedir, escanear=escaner.escanear, con_fuentes=True,
           leer_local=repos._correr):
    ahora = int(ahora if ahora is not None else time.time())
    candado = _candado(cfg["estado_dir"], ahora)
    try:
        resultado = _correr(cfg, ahora, tomar, pedir, escanear, con_fuentes, leer_local)
        db = estado.abrir(cfg["estado_dir"])
        try:
            resultado["avisos"] = avisar(cfg, db, ahora)
        finally:
            db.close()
        latido(cfg, "corrida", ahora)
        return resultado
    finally:
        try:
            os.unlink(candado)
        except OSError:
            pass


def _durable(db, clave, activo, ahora, umbral_s, clase, texto):
    """Abre un incidente solo si lo que pasa ya dura `umbral_s`; lo cierra en cuanto deja de pasar."""
    inicio = estado.desde(db, clave, activo, ahora)
    if inicio is not None and ahora - inicio >= umbral_s:
        estado.incidente(db, clave, clase, texto, ahora)
    elif inicio is None:
        estado.cerrar_incidente(db, clave, ahora)


def _sistema(cfg, db, nombre, sis, ahora):
    """Los incidentes del sistema de un servidor: parches automáticos apagados, o de seguridad que no entran."""
    _durable(db, f"sistema-desatendidas-{nombre}", sis["desatendidas"] is False, ahora, 0, "parches del sistema",
             f"En {nombre}, los parches automáticos del sistema están apagados: nadie instala los de seguridad.")
    pendientes = bool(sis["de_seguridad"]) and sis["desatendidas"] is not False
    _durable(db, f"sistema-seguridad-{nombre}", pendientes, ahora, cfg["sistema_plazo_h"] * 3600,
             "parches del sistema", f"En {nombre} hay {sis['de_seguridad']} parches de seguridad del sistema pendientes "
             f"desde hace más de {cfg['sistema_plazo_h']} h: la rutina desatendida no los está instalando.")


def _urgencia(p):
    """Un KEV que el escáner encuentra en una pieza expuesta, con arreglo publicado: la candidata va por el carril de
    urgencia (sin maduración, en la ventana extraordinaria). Devuelve los KEV, o una lista vacía."""
    v = p.get("vulnerabilidades") or {}
    # Solo una imagen pública: la versión propuesta es otra imagen publicada, que el escáner puede revisar antes. Rehacer
    # una propia sobre su base no prueba que se arregle una falla de sus dependencias.
    if not p.get("candidata") or not p["expuesto"] or p.get("propia") or p.get("sistema") or v.get("estado") != "ok":
        return []
    return list(v.get("kev_con_arreglo") or [])


def _correr(cfg, ahora, tomar, pedir, escanear, con_fuentes, leer_local=repos._correr):
    consultadas = {}

    def consultar(tipo, registro, repo):
        clave = f"{registro}/{repo}"
        if clave not in consultadas:
            if not con_fuentes:
                consultadas[clave] = {"estado": "desconocido", "motivo": "corrida sin fuentes", "etiquetas": [], "hay_mas": False}
            elif tipo == "docker_hub":
                consultadas[clave] = fuentes.docker_hub(repo, pedir)
            else:
                consultadas[clave] = fuentes.registro_oci(registro, repo, pedir)
        return consultadas[clave]

    def detalle(registro, repo, tag):
        if not con_fuentes:
            return None
        if registro == "docker.io":
            return fuentes.etiqueta_hub(repo, tag, pedir)
        return fuentes.detalle_oci(registro, repo, tag, pedir)

    kev = fuentes.kev(pedir) if con_fuentes and cfg["fuentes"]["kev"] else {
        "estado": "desconocido", "motivo": "fuente apagada", "cves": set()}
    db = estado.abrir(cfg["estado_dir"])
    cambios = {"nuevas": [], "cerradas": [], "descartadas": []}
    servidores = []
    try:
        for servidor in cfg["servidores"]:
            t = tomar(servidor, ahora, cfg["foto_vigencia_s"])
            _durable(db, f"sin-medir-{servidor['nombre']}", t["estado"] != "ok", ahora, SIN_MEDIR_S, "servidor sin medir",
                     f"El vigía no puede medir {servidor['nombre']} desde hace más de un día ({t['motivo']}): no sabe "
                     "qué corre ni qué le falta.")
            if t["estado"] != "ok":
                servidores.append({"nombre": t["nombre"], "estado": "desconocido", "motivo": t["motivo"], "sistema": None,
                                   "piezas": None, "errores": []})
                continue
            foto = t["foto"]
            sis = analisis.sistema(foto)
            _sistema(cfg, db, servidor["nombre"], sis, ahora)
            piezas = analisis.piezas(cfg, servidor["nombre"], foto, consultar, detalle, ahora)
            if piezas is not None:
                ids = sorted({p["imagen_id"] for p in piezas if p["imagen_id"]})
                if servidor["lector"] == "local":
                    vulnerabilidades = escanear(cfg["escaner"]["comando"], ids, kev["cves"], cfg["escaner"]["tope_s"])
                else:
                    vulnerabilidades = {i: {"estado": "desconocido", "motivo": "el escáner corre en el propio servidor"}
                                        for i in ids}
                c = estado.registrar(db, servidor["nombre"], piezas, ahora)
                for clave in cambios:
                    cambios[clave] += c[clave]
                sin_arreglo = set()
                for p in piezas:
                    p["vulnerabilidades"] = vulnerabilidades.get(p["imagen_id"]) if p["imagen_id"] else None
                    v = p["vulnerabilidades"] or {}
                    if p["expuesto"] and v.get("estado") == "ok" and v.get("kev_sin_arreglo"):
                        clave = f"kev:{servidor['nombre']}:" + analisis.identificador(servidor["nombre"], p["pieza"], "kev", "")
                        sin_arreglo.add(clave)
                        estado.incidente(db, clave, "falla explotada sin arreglo",
                                         f"En {servidor['nombre']}, {p['pieza']} da a internet y tiene fallas con "
                                         f"explotación conocida sin arreglo publicado ({', '.join(v['kev_sin_arreglo'])}). "
                                         "El vigía no puede arreglarlo: hace falta mitigarlo a mano.", ahora)
                    if p.get("candidata"):
                        urgencia = _urgencia(p)
                        if urgencia:
                            p["candidata"]["urgencia"] = urgencia
                        desde = estado.detectada(db, p["candidata"]["id"]) or ahora
                        p["candidata"]["detectada"] = desde
                        # El reinicio pendiente cuenta como un parche de seguridad: casi siempre lo es.
                        vuln = {"altas": 1} if p.get("sistema") else p["vulnerabilidades"]
                        p["candidata"]["plazo"] = analisis.plazo(cfg, p["candidata"], vuln, desde)
                        if analisis._fecha(p["candidata"]["plazo"]["vence"]).timestamp() < ahora:
                            estado.incidente(db, f"plazo-{p['candidata']['id']}", "plazo vencido",
                                             f"En {servidor['nombre']}, {p['pieza']} sigue en {p['candidata']['actual']}: el "
                                             f"plazo para pasar a {p['candidata']['propuesta']} venció el "
                                             f"{p['candidata']['plazo']['vence'][:10]}.", ahora)
                estado.cerrar_por_prefijo(db, f"kev:{servidor['nombre']}:", ahora, salvo=sin_arreglo)
                for identificador in c["cerradas"] + c["descartadas"]:
                    for clase in ("plazo", "fallo", "roto", "ambiguo", "trabada"):
                        estado.cerrar_incidente(db, f"{clase}-{identificador}", ahora)
                    estado.desbloquear(db, identificador)
            servidores.append({"nombre": servidor["nombre"], "estado": "ok", "motivo": "",
                               "sistema": sis, "piezas": piezas, "errores": foto["errores"]})
        _pausa(cfg, db, ahora)
        censo_vm = None
        if cfg["maquinas"]:
            censo_vm = maquinas.leer(cfg["maquinas"], ahora)
            vistos = set()
            for clave, clase, texto in maquinas.hallazgos(cfg["maquinas"], censo_vm):
                vistos.add(clave)
                estado.incidente(db, clave, clase, texto, ahora)
            if censo_vm["estado"] == "ok":
                estado.cerrar_por_prefijo(db, "maquina", ahora, salvo=vistos)
        lista_repos = repos.revisar(cfg, pedir, leer_local, con_fuentes) if cfg["repos"] else []
        corrida = {"ts": ahora, "servidores": servidores, "cambios": cambios,
                   "kev": {"estado": kev["estado"], "motivo": kev.get("motivo", "")},
                   "fuentes": {k: {"estado": v["estado"], "motivo": v["motivo"]} for k, v in consultadas.items()},
                   "maquinas": censo_vm, "repos": lista_repos, "incidentes": estado.incidentes(db)}
        corrida["ficha"] = informe.publicar(cfg["registro_dir"], corrida)
        texto = informe.resumen(corrida)
        informe._escribir(os.path.join(cfg["estado_dir"], "resumen.txt"), texto)
        estado.anotar_corrida(db, ahora, {
            "servidores": {s["nombre"]: s["estado"] for s in servidores},
            "nuevas": len(cambios["nuevas"]), "cerradas": len(cambios["cerradas"]), "descartadas": len(cambios["descartadas"])})
        corrida["resumen"] = texto
        corrida["_kev"] = kev["cves"]
        return corrida
    finally:
        db.close()


def _pausa(cfg, db, ahora):
    ruta = os.path.join(cfg["estado_dir"], "PAUSA")
    try:
        vieja = ahora - os.stat(ruta).st_mtime > cfg["pausa_max_dias"] * 86400
    except OSError:
        vieja = False
    if vieja:
        estado.incidente(db, "pausa", "vigía en pausa",
                         f"El vigía está en pausa (archivo PAUSA) hace más de {cfg['pausa_max_dias']} días: no aplica nada.",
                         ahora)
    else:
        estado.cerrar_incidente(db, "pausa", ahora)
