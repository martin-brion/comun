"""Repositorios montados en un servidor (por ejemplo, los addons de un Odoo): el commit que corre contra la punta de su
rama en el remoto. Solo informa. No es un aviso de seguridad ni propone nada: un addon se actualiza por su carril.

El commit local lo da un comando de lectura que define la instancia (por ejemplo, un `git rev-parse HEAD` por ssh). El
remoto se consulta por https, sin credenciales, con el protocolo de git (`info/refs`). Lo que no se pudo medir queda
«desconocido», nunca «al día».
"""
import re
import subprocess

from . import fuentes

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _correr(argv, tope=60):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=tope)
        return p.returncode, p.stdout[:4096]
    except (OSError, subprocess.TimeoutExpired):
        return None, b""


def local(argv, correr=_correr):
    rc, salida = correr(list(argv), 60)
    texto = salida.decode("ascii", "replace").strip() if rc == 0 else ""
    return texto if _COMMIT.match(texto) else None


def punta(remoto, rama, pedir):
    """El commit de la punta de la rama en el remoto, o None si no se pudo saber."""
    try:
        estado, _, cuerpo = pedir(remoto.rstrip("/") + "/info/refs?service=git-upload-pack", {"Accept": "*/*"})
    except fuentes.FuenteError:
        return None
    if estado != 200:
        return None
    i, buscada = 0, f"refs/heads/{rama}".encode()
    while i + 4 <= len(cuerpo):
        try:
            largo = int(cuerpo[i:i + 4], 16)
        except ValueError:
            return None
        if largo == 0:
            i += 4
            continue
        if largo < 4:
            return None
        linea = cuerpo[i + 4:i + largo].split(b"\0")[0].rstrip(b"\n")
        i += largo
        commit, _, ref = linea.partition(b" ")
        if ref == buscada and _COMMIT.match(commit.decode("ascii", "replace")):
            return commit.decode("ascii")
    return None


def revisar(cfg, pedir, correr=_correr, con_fuentes=True):
    """[{"nombre", "servidor", "rama", "local", "remoto", "estado", "motivo"}], uno por repositorio declarado."""
    r = []
    for repo in cfg["repos"]:
        aca = local(repo["local"], correr)
        alla = punta(repo["remoto"], repo["rama"], pedir) if con_fuentes else None
        fila = {"nombre": repo["nombre"], "servidor": repo["servidor"], "rama": repo["rama"], "local": aca,
                "remoto": alla, "estado": "desconocido", "motivo": ""}
        if aca is None:
            fila["motivo"] = "no se pudo leer el commit local"
        elif alla is None:
            fila["motivo"] = "corrida sin fuentes" if not con_fuentes else "no se pudo leer la rama remota"
        else:
            fila["estado"] = "al día" if aca == alla else "distinto del remoto"
        r.append(fila)
    return r
