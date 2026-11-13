"""Datos inventados para las pruebas: un servidor de mentira, un índice de mentira y un registro de mentira."""
import json

AHORA = 1_800_000_000                      # 2027-01-15 08:00 UTC
H = "sha256:" + "a" * 64
H2 = "sha256:" + "b" * 64
H3 = "sha256:" + "c" * 64
H4 = "sha256:" + "d" * 64


def imagen_id(n):
    return "sha256:" + f"{n:064x}"


def config(tmp, **cambios):
    base = {
        "servidores": [{"nombre": "principal"}],
        "propias": ["propio/"],
        "componentes": {
            "docker.io/library/postgres": {"nivel": "patch"},
            "docker.io/library/odoo": {"nivel": "nunca", "expuesto": True},
            "docker.io/ejemplo/chat": {"nivel": "minor", "expuesto": True},
            "registro.ejemplo.test/equipo/forja": {"nivel": "patch"},
        },
        "fuentes": {"registros": ["registro.ejemplo.test"]},
        "estado_dir": f"{tmp}/estado", "registro_dir": f"{tmp}/registro",
    }
    base.update(cambios)
    return base


def foto(contenedores, imagenes=None, paquetes=(), **sistema):
    """contenedores: [(nombre, referencia, n)] con n el número de imagen."""
    s = {"id": "debian", "version": "13", "nombre": "Debian GNU/Linux 13", "kernel_en_uso": "6.12.1-amd64",
         "kernels": ["6.12.1-amd64"], "reinicio_pedido": False, "paquetes": None if paquetes is None else list(paquetes), "listas_al": AHORA - 3600,
         "desatendidas": True}
    s.update(sistema)
    return {"esquema": 1, "tomada": AHORA, "sistema": s, "docker": {"version": "29.0.0"},
            "contenedores": [{"nombre": n, "imagen_ref": r, "imagen_id": imagen_id(i), "creado": "2027-01-01T00:00:00Z",
                              "estado": "running", "salud": "healthy", "proyecto": "pila", "servicio": n}
                             for n, r, i in contenedores],
            "imagenes": {imagen_id(i): v for i, v in (imagenes or {}).items()}, "errores": []}


def tag(nombre, publicada="2027-01-01T00:00:00Z", *digests):
    return {"name": nombre, "last_updated": publicada, "digest": digests[0] if digests else None,
            "images": [{"digest": d} for d in digests[1:]]}


class Red:
    """Un `pedir` de mentira: responde por URL exacta o por prefijo, y anota todo lo que se le pidió."""

    def __init__(self, respuestas=None):
        self.respuestas = dict(respuestas or {})
        self.pedidos = []

    def hub(self, repo, tags, siguiente=None):
        self.respuestas[f"https://hub.docker.com/v2/repositories/{repo}/tags"] = (
            200, {}, json.dumps({"results": tags, "next": siguiente}).encode())
        return self

    def __call__(self, url, cabeceras=None, **_):
        self.pedidos.append((url, dict(cabeceras or {})))
        for clave, valor in self.respuestas.items():
            if url == clave or url.startswith(clave + "?"):
                return valor(url, cabeceras or {}) if callable(valor) else valor
        return 404, {}, b"{}"
