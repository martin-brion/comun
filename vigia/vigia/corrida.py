"""Una corrida completa: foto de cada servidor, fuentes, análisis, estado y registro. Solo observa."""
import os
import time

from . import analisis, censo, escaner, estado, fuentes, informe

CANDADO_VIEJO_S = 2 * 3600


class Ocupado(Exception):
    pass


def _candado(directorio, ahora):
    """Una corrida a la vez. Un candado de más de dos horas es de una corrida que murió: se toma."""
    os.makedirs(directorio, exist_ok=True)
    ruta = os.path.join(directorio, "corrida.candado")
    try:
        if ahora - os.stat(ruta).st_mtime > CANDADO_VIEJO_S:
            os.unlink(ruta)
    except OSError:
        pass
    try:
        os.close(os.open(ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise Ocupado("hay otra corrida en curso") from None
    return ruta


def correr(cfg, ahora=None, tomar=censo.tomar, pedir=fuentes.pedir, escanear=escaner.escanear, con_fuentes=True):
    ahora = int(ahora if ahora is not None else time.time())
    candado = _candado(cfg["estado_dir"], ahora)
    try:
        return _correr(cfg, ahora, tomar, pedir, escanear, con_fuentes)
    finally:
        try:
            os.unlink(candado)
        except OSError:
            pass


def _correr(cfg, ahora, tomar, pedir, escanear, con_fuentes):
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
            if t["estado"] != "ok":
                servidores.append({"nombre": t["nombre"], "estado": "desconocido", "motivo": t["motivo"], "sistema": None,
                                   "piezas": None, "errores": []})
                continue
            foto = t["foto"]
            piezas = analisis.piezas(cfg, servidor["nombre"], foto, consultar, detalle, ahora)
            if piezas is not None:
                ids = sorted({p["imagen_id"] for p in piezas})
                if servidor["lector"] == "local":
                    vulnerabilidades = escanear(cfg["escaner"]["comando"], ids, kev["cves"], cfg["escaner"]["tope_s"])
                else:
                    vulnerabilidades = {i: {"estado": "desconocido", "motivo": "el escáner corre en el propio servidor"}
                                        for i in ids}
                c = estado.registrar(db, servidor["nombre"], piezas, ahora)
                for clave in cambios:
                    cambios[clave] += c[clave]
                for p in piezas:
                    p["vulnerabilidades"] = vulnerabilidades.get(p["imagen_id"])
                    if p.get("candidata"):
                        desde = estado.detectada(db, p["candidata"]["id"]) or ahora
                        p["candidata"]["detectada"] = desde
                        p["candidata"]["plazo"] = analisis.plazo(cfg, p["candidata"], p["vulnerabilidades"], desde)
            servidores.append({"nombre": servidor["nombre"], "estado": "ok", "motivo": "",
                               "sistema": analisis.sistema(foto), "piezas": piezas, "errores": foto["errores"]})
        corrida = {"ts": ahora, "servidores": servidores, "cambios": cambios,
                   "kev": {"estado": kev["estado"], "motivo": kev.get("motivo", "")},
                   "fuentes": {k: {"estado": v["estado"], "motivo": v["motivo"]} for k, v in consultadas.items()}}
        corrida["ficha"] = informe.publicar(cfg["registro_dir"], corrida)
        texto = informe.resumen(corrida)
        informe._escribir(os.path.join(cfg["estado_dir"], "resumen.txt"), texto)
        estado.anotar_corrida(db, ahora, {
            "servidores": {s["nombre"]: s["estado"] for s in servidores},
            "nuevas": len(cambios["nuevas"]), "cerradas": len(cambios["cerradas"]), "descartadas": len(cambios["descartadas"])})
        corrida["resumen"] = texto
        return corrida
    finally:
        db.close()
