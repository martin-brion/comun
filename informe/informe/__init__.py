"""Arma un informe navegable a partir de un archivo de datos y de los documentos que cita."""
from pathlib import Path

VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
