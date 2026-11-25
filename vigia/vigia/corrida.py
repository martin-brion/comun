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
from datetime import datetime, timezone

from . import analisis, censo, escaner, estado, fuentes, informe, maquinas, repos

SIN_MEDIR_S = 24 * 3600


class Ocupado(Exception):
    pass


class Candado:
    """Una corrida a la vez, con un bloqueo del sistema operativo sobre `corrida.candado`. Lo tiene el proceso mientras
    vive: si muere (un corte, el reinicio que él mismo pidió), el sistema lo suelta solo. Ni la antigüedad del archivo ni
    su contenido deciden nada: una pasada larga nunca se encuentra con otra."""

    def __init__(self, directorio):
        os.makedirs(directorio, exist_ok=True)
        self._f = open(os.path.join(directorio, "corrida.candado"), "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._f.close()
            raise Ocupado("hay otra corrida en curso") from None

    def soltar(self):
        try:
            if os.name == "nt":
                import msvcrt
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._f.close()


def _candado(directorio, ahora=None):
    return Candado(directorio)


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
    cada cierre de un incidente avisado; si la instancia definió un comando de aviso, se lo pasa. Un aviso cuenta como
    dado recién cuando el comando sale con cero: si falla, se reintenta en la pasada siguiente. El canal es de la
    instancia: no puede depender de lo que el vigía actualiza."""
    carpeta = os.path.join(cfg["estado_dir"], "avisos")
    rutas = []
    for i in estado.por_avisar(db, ahora):
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, _archivo(i) + ".txt")
        titulo = "aviso" if i["tipo"] == "noticia" else i["clase"]
        informe._escribir(ruta, f"Vigía de parches · {titulo}\n\n{i['texto']}\n")
        rutas.append(ruta)
        if not cfg["aviso"] or correr(list(cfg["aviso"]) + [ruta], 120) == 0:
            estado.avisado(db, i["id"], ahora)
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
        if not cfg["aviso"] or correr(list(cfg["aviso"]) + [ruta], 120) == 0:
            estado.avisado(db, i["id"], ahora, cierre=True)
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
            latidos = json.load(f)
        latidos = latidos if isinstance(latidos, dict) else {}
    except (OSError, ValueError):
        latidos = {}
    ultima = latidos.get("corrida")
    if type(ultima) is not int:
        return 4, "vigía: sin corridas registradas"
    problemas = []
    if ahora - ultima > horas * 3600:
        problemas.append(f"la última corrida fue hace {(ahora - ultima) // 3600} h")
    if cfg["ventana"] or cfg["ventana_urgente"]:
        # El ejecutor deja su latido en cada pasada, aunque esté fuera de la ventana: si no lo deja, su temporizador murió.
        limite = horas if cfg["ventana_urgente"] else 7 * 24 + horas
        pasada = latidos.get("ejecutor")
        if type(pasada) is not int:
            problemas.append("el ejecutor no registró ninguna pasada")
        elif ahora - pasada > limite * 3600:
            problemas.append(f"la última pasada del ejecutor fue hace {(ahora - pasada) // 3600} h")
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
        candado.soltar()


def _durable(db, clave, activo, ahora, umbral_s, clase, texto):
    """Abre un incidente solo si lo que pasa ya dura `umbral_s`; lo cierra en cuanto deja de pasar. Con `activo` None (no
    se pudo medir) no cambia nada: lo desconocido no abre ni cierra."""
    if activo is None:
        return
    inicio = estado.desde(db, clave, activo, ahora)
    if inicio is not None and ahora - inicio >= umbral_s:
        estado.incidente(db, clave, clase, texto, ahora)
    elif inicio is None:
        estado.cerrar_incidente(db, clave, ahora)


def _sistema(cfg, db, nombre, sis, ahora):
    """Los incidentes del sistema de un servidor: parches automáticos apagados, o de seguridad que no entran."""
    apagadas = None if sis["desatendidas"] is None else sis["desatendidas"] is False
    _durable(db, f"sistema-desatendidas-{nombre}", apagadas, ahora, 0, "parches del sistema",
             f"En {nombre}, los parches automáticos del sistema están apagados: nadie instala los de seguridad.")
    if sis["paquetes"] is None:
        return
    # Se mide la antigüedad de cada paquete pendiente en su versión, no la de «haber alguno»: si la rutina instala los de
    # ayer y hoy aparecen otros, está funcionando.
    claves = {f"{p['nombre']}={p['a']}" for p in sis["paquetes"] if p["seguridad"]}
    mas_viejo = estado.desde_conjunto(db, f"paquete:{nombre}:", claves, ahora)
    clave = f"sistema-seguridad-{nombre}"
    if mas_viejo is not None and ahora - mas_viejo >= cfg["sistema_plazo_h"] * 3600:
        estado.incidente(db, clave, "parches del sistema",
                         f"En {nombre} hay parches de seguridad del sistema pendientes desde hace más de "
                         f"{cfg['sistema_plazo_h']} h: la rutina desatendida no los está instalando.", ahora)
    else:
        # Ninguno pendiente con esa antigüedad: la rutina está avanzando, aunque haya parches recientes.
        estado.cerrar_incidente(db, clave, ahora)


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
                consultadas[clave] = fuentes.registro_oci(registro, repo, pedir, cfg["fuentes"]["destinos"])
        return consultadas[clave]

    db = estado.abrir(cfg["estado_dir"])

    def detalle(registro, repo, tag):
        if not con_fuentes:
            return None
        if registro == "docker.io":
            return fuentes.etiqueta_hub(repo, tag, pedir)
        d = fuentes.detalle_oci(registro, repo, tag, pedir, cfg["fuentes"]["destinos"])
        if d and d.get("indice"):
            # Un registro OCI da la fecha en que se armó la imagen, que puede ser muy anterior a su publicación. La
            # maduración cuenta desde la más tardía entre esa y la primera vez que el vigía vio esa huella.
            visto = estado.desde(db, f"visto:{registro}/{repo}@{d['indice']}", True, ahora)
            vista = analisis._iso(datetime.fromtimestamp(visto, timezone.utc))
            d["publicada"] = max(d["publicada"] or "", vista)
            efectiva = analisis._fecha(d["publicada"])
            if efectiva is not None:
                estado.marcar(db, f"ocifecha:{registro}/{repo}:{tag}", int(efectiva.timestamp()))
        return d

    def fechas(registro, repo):
        """Las fechas de publicación que el vigía ya conoce de las etiquetas de un registro OCI."""
        if registro == "docker.io":
            return {}
        return {t: analisis._iso(datetime.fromtimestamp(v, timezone.utc))
                for t, v in estado.marcas_con(db, f"ocifecha:{registro}/{repo}:").items()}

    kev = fuentes.kev(pedir) if con_fuentes and cfg["fuentes"]["kev"] else {
        "estado": "desconocido", "motivo": "fuente apagada", "cves": set()}
    _durable(db, "fuente-kev", (kev["estado"] != "ok") if con_fuentes and cfg["fuentes"]["kev"] else None, ahora,
             3 * 86400, "fuente sin datos", f"Hace más de tres días que no se puede leer la lista de fallas con "
             f"explotación conocida ({kev.get('motivo', '')}): no se sabe qué es urgente.")
    cambios = {"nuevas": [], "cerradas": [], "descartadas": []}
    servidores = []
    try:
        for servidor in cfg["servidores"]:
            t = tomar(servidor, ahora, cfg["foto_vigencia_s"])
            sin_medir = t["estado"] != "ok" or t["foto"]["contenedores"] is None
            _durable(db, f"sin-medir-{servidor['nombre']}", sin_medir, ahora, SIN_MEDIR_S, "servidor sin medir",
                     f"El vigía no puede medir {servidor['nombre']} desde hace más de un día "
                     f"({t['motivo'] or 'no se pueden listar sus contenedores'}): no sabe qué corre ni qué le falta.")
            if t["estado"] != "ok":
                servidores.append({"nombre": t["nombre"], "estado": "desconocido", "motivo": t["motivo"], "sistema": None,
                                   "piezas": None, "errores": []})
                continue
            foto = t["foto"]
            sis = analisis.sistema(foto)
            _sistema(cfg, db, servidor["nombre"], sis, ahora)
            piezas = analisis.piezas(cfg, servidor["nombre"], foto, consultar, detalle, ahora, fechas)
            if piezas is not None:
                ids = sorted({p["imagen_id"] for p in piezas if p["imagen_id"]})
                if servidor["lector"] == "local":
                    vulnerabilidades = escanear(cfg["escaner"]["comando"], ids, kev["cves"], cfg["escaner"]["tope_s"])
                else:
                    vulnerabilidades = {i: {"estado": "desconocido", "motivo": "el escáner corre en el propio servidor"}
                                        for i in ids}
                escaneo = None
                if servidor["lector"] == "local" and cfg["escaner"]["comando"] and ids:
                    fallidas = [i for i in ids if (vulnerabilidades.get(i) or {}).get("estado") != "ok"]
                    escaneo = bool(fallidas)
                _durable(db, f"escaner-{servidor['nombre']}", escaneo, ahora, SIN_MEDIR_S, "escáner sin resultados",
                         f"En {servidor['nombre']} el escáner no da resultado para alguna imagen hace más de un día: de "
                         "esas no se sabe qué vulnerabilidades hay ni qué es urgente.")
                # Las piezas públicas que hace días no se pueden comparar con lo publicado (salvo las que la
                # configuración deja sin fuente a propósito).
                ciegas = [p["pieza"] for p in piezas if p["estado"] == "desconocido" and not p.get("propia")
                          and not p.get("sistema") and p["pieza"] != "(sin referencia)"
                          and p["motivo"] not in ("registro sin fuente aprobada", "fuente apagada en la configuración")]
                # Y las que tienen una versión nueva sin fecha (el detalle del registro falla): esperarían para siempre sin
                # que nada avise. La que no tiene huella ya la avisa «trabada», si se aplica sola.
                ciegas += [p["pieza"] for p in piezas if p.get("candidata")
                           and p["candidata"]["compuerta_version"] == "espera: sin fecha de publicación"]
                _durable(db, f"fuentes-{servidor['nombre']}", bool(ciegas) if con_fuentes else None, ahora, 3 * 86400,
                         "piezas sin poder saber", f"En {servidor['nombre']} hay piezas que hace más de tres días no se "
                         f"pueden comparar con lo publicado: {', '.join(sorted(set(ciegas))[:10])}.")
                for p in piezas:
                    p["vulnerabilidades"] = vulnerabilidades.get(p["imagen_id"]) if p["imagen_id"] else None
                    urgencia = _urgencia(p) if kev["estado"] == "ok" else []
                    if urgencia and p.get("candidata_urgente"):
                        # Con una falla explotada se propone la más nueva del paso, no la madura: es la que trae el
                        # arreglo, y el escáner lo comprueba antes de aplicar.
                        p["candidata"] = p["candidata_urgente"]
                    if urgencia:
                        p["candidata"]["urgencia"] = urgencia
                c = estado.registrar(db, servidor["nombre"], piezas, ahora)
                for clave in cambios:
                    cambios[clave] += c[clave]
                presentes = set()
                for p in piezas:
                    presentes |= _kev(cfg, db, servidor["nombre"], p, ahora, kev["estado"] == "ok")
                    if p.get("candidata"):
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
                # Solo se cierran acá los de piezas que ya no corren; los demás los abre o cierra su propia medición.
                for prefijo in ("kev:", "kevimg:"):
                    estado.cerrar_por_prefijo(db, f"{prefijo}{servidor['nombre']}:", ahora, salvo=presentes)
                for identificador in c["cerradas"] + c["descartadas"]:
                    for clase in ("plazo", "fallo", "trabada"):
                        estado.cerrar_incidente(db, f"{clase}-{identificador}", ahora)
                    estado.desbloquear(db, identificador)
                # Lo roto o a medias (`roto-`, `ambiguo-`) no lo cierra el observador: ver la versión no prueba que
                # funcione ni que hayan vuelto todos sus servicios. Lo cierra una persona (`resuelto`).
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


def _kev(cfg, db, servidor, p, ahora, kev_ok=True):
    """Los incidentes de fallas con explotación conocida en una pieza expuesta: sin arreglo publicado (en seguida), o con
    arreglo pero sin una imagen nueva que el vigía pueda aplicar (pasado el plazo de urgencia). Con el escaneo
    desconocido, no cambia nada. Devuelve las claves de la pieza."""
    sufijo = analisis.identificador(servidor, p["pieza"], "kev", "")
    sin, img = f"kev:{servidor}:{sufijo}", f"kevimg:{servidor}:{sufijo}"
    v = p.get("vulnerabilidades") or {}
    # Hacen falta las dos mediciones: el escaneo y la lista KEV. Sin la lista, «no hay KEV» no se sabe.
    medido = v.get("estado") == "ok" and p["expuesto"] and kev_ok
    if not p["expuesto"]:
        medido, v = True, {}
    if not medido:
        return {sin, img}
    _durable(db, sin, bool(v.get("kev_sin_arreglo")), ahora, 0, "falla explotada sin arreglo",
             f"En {servidor}, {p['pieza']} da a internet y tiene fallas con explotación conocida sin arreglo publicado "
             f"({', '.join((v.get('kev_sin_arreglo') or [])[:10])}). El vigía no puede arreglarlo: hace falta mitigarlo "
             "a mano.")
    sin_imagen = bool(v.get("kev_con_arreglo")) and not p.get("candidata")
    _durable(db, img, sin_imagen, ahora, cfg["plazos_h"]["kev_expuesto"] * 3600, "falla explotada sin imagen nueva",
             f"En {servidor}, {p['pieza']} da a internet y tiene fallas con explotación conocida "
             f"({', '.join((v.get('kev_con_arreglo') or [])[:10])}) que ya tienen arreglo, pero no hay una imagen nueva "
             "que el vigía pueda aplicar. Hace falta una persona.")
    return {sin, img}


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
