"""Pide la foto de cada servidor y la deja limpia. La foto llega de otra máquina: se reconstruye campo por campo, con tipos
y tamaños fijos, y lo que no se conoce no pasa. Una foto ausente, ilegible o vieja deja al servidor en «desconocido»."""
import json
import os
import re
import subprocess

from . import foto as _foto

MAX_BYTES = 4 << 20
_TEXTO = re.compile(r"^[\w .:/@+~()\[\],=-]{0,300}$")
_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class FotoError(Exception):
    pass


def _t(valor, largo=300):
    """Texto corto de caracteres llanos, o None."""
    if valor is None:
        return None
    if not isinstance(valor, str) or len(valor) > largo or not _TEXTO.match(valor):
        raise FotoError("texto fuera de forma")
    return valor


def _lista(valor, tope):
    if not isinstance(valor, list) or len(valor) > tope:
        raise FotoError("lista fuera de forma")
    return valor


def limpiar(cruda, ahora, vigencia_s):
    """La foto, reconstruida. FotoError si no tiene la forma esperada o no es de ahora."""
    if not isinstance(cruda, dict) or cruda.get("esquema") != _foto.ESQUEMA:
        raise FotoError("esquema desconocido")
    tomada = cruda.get("tomada")
    if type(tomada) is not int or abs(ahora - tomada) > vigencia_s:
        raise FotoError("foto vencida")
    s = cruda.get("sistema")
    if not isinstance(s, dict):
        raise FotoError("falta el sistema")
    paquetes = None
    if s.get("paquetes") is not None:
        paquetes = [{"nombre": _t(p.get("nombre"), 120), "origen": _t(p.get("origen"), 200), "a": _t(p.get("a"), 120),
                     "de": _t(p.get("de"), 120), "seguridad": p.get("seguridad") is True}
                    for p in _lista(s["paquetes"], _foto.MAX_PAQUETES) if isinstance(p, dict)]
    sistema = {"id": _t(s.get("id"), 60), "version": _t(s.get("version"), 60), "nombre": _t(s.get("nombre"), 120),
               "kernel_en_uso": _t(s.get("kernel_en_uso"), 120),
               "kernels": None if s.get("kernels") is None else [_t(k, 120) for k in _lista(s["kernels"], 50)],
               "reinicio_pedido": s.get("reinicio_pedido") is True, "paquetes": paquetes,
               "listas_al": s["listas_al"] if type(s.get("listas_al")) is int else None,
               "desatendidas": s["desatendidas"] if isinstance(s.get("desatendidas"), bool) else None}
    contenedores = None
    if cruda.get("contenedores") is not None:
        contenedores = []
        for c in _lista(cruda["contenedores"], _foto.MAX_CONTENEDORES):
            if not isinstance(c, dict) or not _ID.match(str(c.get("imagen_id"))):
                raise FotoError("contenedor fuera de forma")
            contenedores.append({k: _t(c.get(k)) or "" for k in
                                 ("nombre", "imagen_ref", "imagen_id", "creado", "estado", "salud", "proyecto", "servicio")})
    imagenes = {}
    crudas = cruda.get("imagenes") or {}
    if not isinstance(crudas, dict) or len(crudas) > _foto.MAX_CONTENEDORES:
        raise FotoError("imágenes fuera de forma")
    for clave, i in crudas.items():
        if not _ID.match(str(clave)) or not isinstance(i, dict):
            raise FotoError("imagen fuera de forma")
        rotulos = i.get("etiquetas") or {}
        if not isinstance(rotulos, dict):
            raise FotoError("imagen fuera de forma")
        imagenes[clave] = {"tags": [_t(t) for t in _lista(i.get("tags") or [], 20)],
                           "digests": [_t(d) for d in _lista(i.get("digests") or [], 20)],
                           "creada": _t(i.get("creada"), 40) or "",
                           "etiquetas": {k: _t(rotulos[k]) for k in _foto.ETIQUETAS if rotulos.get(k) is not None}}
    docker = cruda.get("docker") if isinstance(cruda.get("docker"), dict) else {}
    errores = [_t(e) for e in _lista(cruda.get("errores") or [], 20)]
    return {"tomada": tomada, "sistema": sistema, "docker": {"version": _t(docker.get("version"), 60)},
            "contenedores": contenedores, "imagenes": imagenes, "errores": errores}


def _remota(lector, tope=180):
    """Corre el lector (por ejemplo, un ssh a la hoja) dándole el archivo de la foto por la entrada."""
    with open(_foto.__file__, "rb") as f:
        fuente = f.read()
    try:
        p = subprocess.run(lector, input=fuente, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=tope,
                           env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        raise FotoError("el lector no respondió") from None
    if p.returncode != 0 or len(p.stdout) > MAX_BYTES:
        raise FotoError("el lector falló")
    try:
        return json.loads(p.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FotoError("foto ilegible") from None


def tomar(servidor, ahora, vigencia_s, remota=_remota, local=_foto.tomar):
    """{"nombre", "estado": "ok" | "desconocido", "motivo", "foto"} de un servidor de la configuración."""
    try:
        cruda = local() if servidor["lector"] == "local" else remota(servidor["lector"])
        return {"nombre": servidor["nombre"], "estado": "ok", "motivo": "", "foto": limpiar(cruda, ahora, vigencia_s)}
    except FotoError as e:
        return {"nombre": servidor["nombre"], "estado": "desconocido", "motivo": str(e), "foto": None}
