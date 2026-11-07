"""ZIP reproducible del plugin, addons, ejemplo y pruebas. Sólo biblioteca estándar."""
import argparse
import hashlib
from pathlib import Path
import zipfile

RAIZ = Path(__file__).resolve().parent.parent
CARPETAS = {"plugin", "odoo", "ejemplo_capa", "scripts", "tests"}
DOCUMENTOS = {"AGENTS.md", "CONTRATO.md", "FORMATOS.md", "README.md", "VERSION"}


def fuentes():
    archivos = {}
    for inicio in sorted(CARPETAS | DOCUMENTOS):
        ruta = RAIZ / inicio
        if not ruta.exists():
            raise ValueError("Fuente incompleta")
        if ruta.is_symlink():
            raise ValueError("No se empaquetan enlaces")
        candidatos = [ruta] if ruta.is_file() else sorted(ruta.rglob("*"))
        for p in candidatos:
            rel = p.relative_to(RAIZ)
            if "__pycache__" in rel.parts or p.suffix == ".pyc":
                continue
            if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
                raise ValueError("No se empaquetan enlaces")
            if p.is_file():
                archivos[rel.as_posix()] = p.read_bytes()
    return dict(sorted(archivos.items()))


def crear(destino):
    destino = destino.resolve()
    if destino.is_relative_to(RAIZ):
        raise ValueError("El ZIP de salida debe quedar fuera de la fuente")
    archivos = fuentes()
    archivos["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in archivos.items()).encode("utf-8")
    destino.parent.mkdir(parents=True, exist_ok=True)
    # Sin compresión: tampoco depende de la versión de zlib de quien reconstruye.
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_STORED) as z:
        for nombre, contenido in sorted(archivos.items()):
            info = zipfile.ZipInfo(nombre, (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if nombre.endswith(".sh") else 0o100644) << 16
            z.writestr(info, contenido)
    return hashlib.sha256(destino.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, required=True)
    args = parser.parse_args()
    print(crear(args.salida))
