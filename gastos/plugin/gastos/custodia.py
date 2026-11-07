"""Exclusión de procesos sobre el lote y su índice, sin bloquear el event loop."""
import asyncio
from contextlib import asynccontextmanager
import errno
import fcntl
import hashlib
import os
import stat
from . import estado

TOPE_ESPERA_S = 5.0


def ruta(lote_id, c):
    base = estado.raiz(lote_id) / "candados"
    estado.seguro(base)
    base.mkdir(mode=0o700, exist_ok=True)
    # Más amplio que la clave de factura: protege también el índice compartido.
    clave = hashlib.sha256(lote_id.encode()).hexdigest()
    return estado.seguro(base / f"{clave}.lock")


def abrir(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise estado.EstadoError("El candado del lote no es un archivo seguro")
    return fd


@asynccontextmanager
async def exclusion(lote_id, c):
    fd = abrir(ruta(lote_id, c))
    loop = asyncio.get_running_loop()
    plazo = loop.time() + TOPE_ESPERA_S
    tomado = False
    try:
        while not tomado:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                tomado = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if loop.time() >= plazo:
                    raise estado.EstadoError("Otra operación usa este lote y destino; reintentar cuando termine") from None
                await asyncio.sleep(0.05)
        yield
    finally:
        if tomado:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def ocupado(lote_id, c):
    fd = abrir(ruta(lote_id, c))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return True
            raise
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
