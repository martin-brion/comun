"""Verifica todos los contenidos del ZIP sin extraer ni ejecutar su código."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import zipfile


def verificar(ruta):
    with zipfile.ZipFile(ruta) as z:
        nombres = z.namelist()
        if len(set(nombres)) != len(nombres):
            raise ValueError("Entradas duplicadas")
        for n in nombres:
            p = PurePosixPath(n)
            if p.is_absolute() or ".." in p.parts or "\\" in n or ":" in n or n.endswith("/"):
                raise ValueError("Ruta no válida en el paquete")
        esperados = {}
        for linea in z.read("SHA256SUMS.txt").decode("utf-8").splitlines():
            digest, nombre = linea.split("  ", 1)
            if nombre in esperados or len(digest) != 64:
                raise ValueError("Manifiesto no válido")
            esperados[nombre] = digest
        if set(esperados) != set(nombres) - {"SHA256SUMS.txt"}:
            raise ValueError("Inventario diferente del manifiesto")
        for nombre, digest in esperados.items():
            if hashlib.sha256(z.read(nombre)).hexdigest() != digest:
                raise ValueError("Contenido diferente del manifiesto")
        return len(esperados)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()
    print(f"Paquete verificado: {verificar(args.zip)} archivos")
