"""Propuestas y recibos separados; los marcadores del ERP permanecen intactos."""
import json
import os
import tempfile
import time
from pathlib import Path
from bot import config


class EstadoError(ValueError):
    pass


def seguro(path):
    base = config.DATA_DIR / "gastos"
    if base.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
        raise EstadoError("Estado de gastos fuera de su custodia")
    for p in (path, *path.parents):
        if p == base.parent:
            break
        if p.is_symlink():
            raise EstadoError("Estado de gastos inseguro")
    return path


def raiz(lote_id):
    if (not isinstance(lote_id, str) or len(lote_id) != 7 or lote_id[0] != "L"
            or not lote_id[1:].isascii() or not lote_id[1:].isdigit()):
        raise EstadoError("ID de lote inválido")
    p = config.DATA_DIR / "gastos"
    seguro(p)
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def leer(path, defecto=None):
    seguro(path)
    if not path.exists():
        return defecto
    if path.is_symlink() or not path.is_file():
        raise EstadoError("Estado de gastos inseguro")
    try:
        valor = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(valor, dict):
            raise ValueError()
        return valor
    except (ValueError, OSError):
        raise EstadoError("Estado de gastos inválido; se conserva para revisión") from None


def guardar(path, valor):
    seguro(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise EstadoError("Estado de gastos inseguro")
    contenido = json.dumps(valor, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    fd, nombre = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    tmp = Path(nombre)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        tmp.replace(path)
        if os.name != "nt":
            # Incluye las entradas de directorios recién creados bajo DATA_DIR.
            for directorio in (path.parent, *path.parent.parents):
                if directorio == config.DATA_DIR.parent:
                    break
                dfd = os.open(directorio, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)


def propuesta(lote_id, c=None):
    p = raiz(lote_id)
    if c is None or c["legacy"]:
        return seguro(p / f"{lote_id}.json")
    return seguro(p / lote_id / c["empresa"] / f"{c['erp_id']}.json")


def otras_propuestas(lote_id, c=None):
    """(lote, ruta) de las propuestas de OTROS lotes para la misma empresa y ERP que `c` (o heredadas sin `c`)."""
    base = config.DATA_DIR / "gastos"
    if not base.is_dir():
        return []
    heredada = c is None or c["legacy"]
    patron = "L*.json" if heredada else f"L*/{c['empresa']}/{c['erp_id']}.json"
    salida = []
    for ruta in sorted(base.glob(patron)):
        otro = ruta.name[:-len(".json")] if heredada else ruta.parent.parent.name
        try:
            raiz(otro)
        except EstadoError:
            continue
        if otro != lote_id and ruta.is_file() and not ruta.is_symlink():
            salida.append((otro, seguro(ruta)))
    return salida


def indice_path(lote_id):
    return raiz(lote_id) / f"{lote_id}.indice.json"


def recibo_path(lote_id, c):
    return raiz(lote_id) / lote_id / c["empresa"] / f"{c['erp_id']}.recibo.json"


def comprobar(lote, c, *, persistir=False):
    p = indice_path(lote["lote_id"])
    indice = leer(p)
    if indice is None:
        indice = {"version": 1, "login": lote["login"], "empresa": c["empresa"],
                  "destinos": {}, "retener_hasta": lote.get("creado", time.time()) + c["retencion_dias"] * 86400}
    if indice["login"] != lote["login"] or indice["empresa"] != c["empresa"]:
        raise EstadoError("Un lote pertenece a una sola persona y empresa; usá otro lote")
    for alias, canon in c["declarados"].items():
        if alias in indice["destinos"] and indice["destinos"][alias] != canon:
            raise EstadoError("Cambió el ERP de un destino del lote; conservar su identidad y recibos")
        indice["destinos"][alias] = canon
    if persistir:
        guardar(p, indice)
    return indice


def recibo(lote_id, c):
    return leer(recibo_path(lote_id, c))


def cargados(lote_ids):
    """Los lotes de la lista con alguna corrida cargada (un recibo en estado «cargado», de cualquier empresa y destino), en
    el mismo orden. Un lote ilegible o inválido no cuenta."""
    salida = []
    for lote_id in lote_ids:
        try:
            carpeta = raiz(lote_id) / lote_id
            rutas = sorted(carpeta.glob("*/*.recibo.json")) if carpeta.is_dir() and not carpeta.is_symlink() else []
            if any((leer(seguro(r)) or {}).get("estado") == "cargado" for r in rutas):
                salida.append(lote_id)
        except EstadoError:
            continue
    return salida


def iniciar(lote, c, huella):
    comprobar(lote, c, persistir=True)
    anterior = recibo(lote["lote_id"], c)
    if anterior and anterior["huella"] != huella:
        raise EstadoError("Esta corrida ya tiene efectos de otra propuesta; retomá la propuesta original")
    if anterior is None:
        anterior = {"version": 1, "login": lote["login"], "empresa": c["empresa"], "destino": c["destino"],
                    "erp": c["erp"], "huella": huella, "estado": "parcial", "efectos": []}
        guardar(recibo_path(lote["lote_id"], c), anterior)
    return anterior


def efecto(lote_id, c, modelo, ident):
    r = recibo(lote_id, c)
    if r is None:
        raise EstadoError("Falta el recibo de la corrida confirmada")
    entrada = {"modelo": modelo, "id": ident}
    if entrada not in r["efectos"]:
        r["efectos"].append(entrada)
        guardar(recibo_path(lote_id, c), r)


def completar(lote_id, c, resultado):
    r = recibo(lote_id, c)
    if r is None:
        raise EstadoError("Falta el recibo de la corrida confirmada")
    r.update(estado="cargado", resultado=resultado)
    guardar(recibo_path(lote_id, c), r)


def retener(lote, ts, cfg):
    from . import configuracion
    from bot import odoo
    indice = leer(indice_path(lote["lote_id"]))
    plazo = indice["retener_hasta"] if indice else lote.get("creado", time.time()) + cfg["retencion_dias"] * 86400
    if ts >= plazo:
        return False
    if indice is None:
        return True
    c = cfg["empresas"].get(indice["empresa"])
    if c is None:
        return True
    declarados = dict(indice["destinos"])
    for alias, d in c["destinos"].items():
        if d is None:
            canon = configuracion.firma(list(configuracion.identidad_heredada()))
        else:
            canon = configuracion.firma(list(odoo.identidad_destino(d)))
        if alias in declarados and declarados[alias] != canon:
            return True
        declarados[alias] = canon
    for alias, canon in declarados.items():
        seleccion = {"empresa": indice["empresa"], "erp_id": canon}
        r = recibo(lote["lote_id"], seleccion)
        if not r or r.get("estado") != "cargado":
            return True
    return False
