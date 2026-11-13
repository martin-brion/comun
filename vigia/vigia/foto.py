"""Foto de un servidor: mediciones saneadas, solo lectura.

Un solo archivo y solo biblioteca estándar: corre en una hoja sin instalar nada (`python3 - < foto.py`) o como comando
forzado de una llave. Imprime un JSON. No lee variables de entorno de los contenedores, ni montajes, ni archivos de
configuración de los servicios: solo qué corre, con qué imagen, y qué tiene pendiente el sistema.
"""
import json
import os
import re
import subprocess
import sys
import time

ESQUEMA = 1
TOPE_S = 60
MAX_PAQUETES = 500
MAX_CONTENEDORES = 300
ETIQUETAS = ("org.opencontainers.image.base.name", "org.opencontainers.image.base.digest",
             "org.opencontainers.image.version", "org.opencontainers.image.revision")
_APT = re.compile(r"^([^/\s]+)/(\S+)\s+(\S+)\s+\S+(?:\s+\[upgradable from: ([^\]]+)\])?")


def _correr(argv, tope=TOPE_S):
    """(código, salida) de un comando; (None, "") si no existe o no terminó a tiempo."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=tope,
                           env=dict(os.environ, LC_ALL="C", LANG="C"))
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def _leer(ruta):
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            return f.read(65536)
    except OSError:
        return None


def _listar(ruta):
    try:
        return sorted(os.listdir(ruta))
    except OSError:
        return None


def _mtime(ruta):
    try:
        return int(os.stat(ruta).st_mtime)
    except OSError:
        return None


def sistema(correr=_correr, leer=_leer, listar=_listar, mtime=_mtime):
    """Distribución, kernel, reinicio pedido y paquetes pendientes. Lo que no se pudo medir queda en None y en `errores`:
    nunca en cero."""
    errores = []
    dist = {}
    for linea in (leer("/etc/os-release") or "").splitlines():
        clave, _, valor = linea.partition("=")
        if clave in ("ID", "VERSION_ID", "PRETTY_NAME"):
            dist[clave] = valor.strip().strip('"')[:120]
    rc, kernel = correr(["uname", "-r"])
    nucleos = listar("/boot")
    paquetes = None
    rc_apt, salida = correr(["apt", "list", "--upgradable"])
    if rc_apt == 0:
        paquetes = []
        for linea in salida.splitlines():
            m = _APT.match(linea)
            if m and len(paquetes) < MAX_PAQUETES:
                paquetes.append({"nombre": m.group(1)[:120], "origen": m.group(2)[:200], "a": m.group(3)[:120],
                                 "de": (m.group(4) or "")[:120],
                                 "seguridad": any("security" in s for s in m.group(2).split(","))})
    else:
        errores.append("paquetes: no se pudo consultar el gestor de paquetes")
    auto = leer("/etc/apt/apt.conf.d/20auto-upgrades")
    return {
        "id": dist.get("ID"), "version": dist.get("VERSION_ID"), "nombre": dist.get("PRETTY_NAME"),
        "kernel_en_uso": kernel.strip()[:120] if rc == 0 else None,
        "kernels": None if nucleos is None else [n[len("vmlinuz-"):][:120] for n in nucleos if n.startswith("vmlinuz-")],
        "reinicio_pedido": leer("/var/run/reboot-required") is not None,
        "paquetes": paquetes,
        "listas_al": mtime("/var/lib/apt/periodic/update-success-stamp") or mtime("/var/lib/apt/lists"),
        "desatendidas": None if auto is None else bool(re.search(r'Unattended-Upgrade\s+"1"', auto)),
    }, errores


def docker(correr=_correr):
    """Contenedores en marcha y sus imágenes. Sin Docker devuelve None, y lo dice."""
    rc, version = correr(["docker", "version", "--format", "{{.Server.Version}}"])
    if rc != 0:
        return None, None, {}, ["docker: no responde"]
    errores = []
    rc, ids = correr(["docker", "ps", "-q", "--no-trunc"])
    if rc != 0:
        return version.strip()[:60], None, {}, ["docker: no se pudo listar los contenedores"]
    ids = ids.split()[:MAX_CONTENEDORES]
    contenedores, imagenes = [], {}
    if ids:
        rc, crudo = correr(["docker", "inspect"] + ids)
        try:
            datos = json.loads(crudo) if rc == 0 else None
        except ValueError:
            datos = None
        if not isinstance(datos, list):
            return version.strip()[:60], None, {}, ["docker: no se pudo inspeccionar los contenedores"]
        for c in datos:
            cfg, estado = c.get("Config") or {}, c.get("State") or {}
            rotulos = cfg.get("Labels") or {}
            contenedores.append({
                "nombre": str(c.get("Name") or "").lstrip("/")[:120], "imagen_ref": str(cfg.get("Image") or "")[:300],
                "imagen_id": str(c.get("Image") or "")[:100], "creado": str(c.get("Created") or "")[:40],
                "estado": str(estado.get("Status") or "")[:30], "salud": str((estado.get("Health") or {}).get("Status") or "")[:30],
                "proyecto": str(rotulos.get("com.docker.compose.project") or "")[:80],
                "servicio": str(rotulos.get("com.docker.compose.service") or "")[:80]})
        unicas = sorted({c["imagen_id"] for c in contenedores if c["imagen_id"]})
        rc, crudo = correr(["docker", "image", "inspect"] + unicas) if unicas else (0, "[]")
        try:
            datos = json.loads(crudo) if rc == 0 else None
        except ValueError:
            datos = None
        if not isinstance(datos, list):
            errores.append("docker: no se pudo inspeccionar las imágenes")
            datos = []
        for i in datos:
            rotulos = (i.get("Config") or {}).get("Labels") or {}
            imagenes[str(i.get("Id") or "")[:100]] = {
                "tags": [str(t)[:300] for t in (i.get("RepoTags") or [])][:20],
                "digests": [str(d)[:300] for d in (i.get("RepoDigests") or [])][:20],
                "creada": str(i.get("Created") or "")[:40],
                "etiquetas": {k: str(rotulos[k])[:300] for k in ETIQUETAS if k in rotulos}}
    return version.strip()[:60], contenedores, imagenes, errores


def tomar(correr=_correr, leer=_leer, listar=_listar, mtime=_mtime, ahora=None):
    s, e1 = sistema(correr, leer, listar, mtime)
    version, contenedores, imagenes, e2 = docker(correr)
    return {"esquema": ESQUEMA, "tomada": int(ahora if ahora is not None else time.time()), "sistema": s,
            "docker": {"version": version}, "contenedores": contenedores, "imagenes": imagenes, "errores": e1 + e2}


if __name__ == "__main__":
    json.dump(tomar(), sys.stdout)
    sys.stdout.write("\n")
