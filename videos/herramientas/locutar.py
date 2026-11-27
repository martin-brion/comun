"""Locución: graba la voz de cada escena con el micrófono de la PC, mirando su texto y cuánto dura en el video.

Uso (en una terminal propia: pide Enter para empezar y para cortar cada toma de voz):
  python locutar.py <guion.yaml> --listar                       # los micrófonos que ve la PC
  python locutar.py <guion.yaml> --microfono "<nombre>"         # todas las escenas que tienen texto, en orden
  python locutar.py <guion.yaml> --microfono "<nombre>" --escena <id> [--escena <id>]   # solo esas

Cada escena queda en `<binarios>/voz/<id>.wav`. Lo que dice cada una es `locucion` de la escena (si falta, su `texto`).
La duración que se muestra es la del último video armado: si la voz es más larga, la edición alarga la escena dejando
quieto su último cuadro, así que no hace falta apurarse. También vale grabar con otro programa y dejar el archivo ahí con
el mismo nombre (wav, m4a, mp3, ogg o flac). Las placas se locutan con los ids `placa_inicio` y `placa_fin`.
El micrófono por defecto puede quedar en VIDEOS_MICROFONO.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI / "superficies"))
import entorno  # noqa: E402


def microfonos() -> list[str]:
    r = subprocess.run([entorno.ffmpeg(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True)
    texto = r.stderr.decode("utf-8", "replace")
    return [m.group(1) for m in re.finditer(r'"([^"]+)"\s*\(audio\)', texto)]


def duraciones(binarios: Path) -> dict[str, float]:
    videos = sorted(binarios.glob("video_*_v*.mp4"), key=lambda p: p.stat().st_mtime)
    if not videos or not videos[-1].with_suffix(".escenas.json").exists():
        return {}
    escenas = json.loads(videos[-1].with_suffix(".escenas.json").read_text(encoding="utf-8"))["escenas"]
    return {e["escena"]: round(e["t1"] - e["t0"], 1) for e in escenas}


def a_locutar(guion: dict) -> list[tuple[str, str]]:
    lista = []
    for clave in ("inicio",):
        p = (guion.get("placas") or {}).get(clave) or {}
        if p.get("locucion"):
            lista.append((f"placa_{clave}", p["locucion"]))
    for e in guion["escenas"]:
        texto = e.get("locucion", e.get("texto", ""))
        if texto:
            lista.append((e["id"], texto))
    p = (guion.get("placas") or {}).get("fin") or {}
    if p.get("locucion"):
        lista.append(("placa_fin", p["locucion"]))
    return lista


def grabar(microfono: str, destino: Path) -> None:
    proc = subprocess.Popen([entorno.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "dshow", "-i",
                             f"audio={microfono}", "-ac", "1", "-ar", "48000", str(destino)],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    input("  ● grabando… Enter para cortar ")
    try:
        proc.communicate(b"q", timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    if proc.returncode not in (0, 255) or not destino.exists():
        raise RuntimeError("no se grabó: " + (proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""))


def escuchar(archivo: Path) -> None:
    subprocess.run([entorno.ffmpeg("ffplay"), "-autoexit", "-nodisp", "-loglevel", "error", str(archivo)])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("guion", type=Path)
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--microfono", default=os.environ.get("VIDEOS_MICROFONO"))
    ap.add_argument("--escena", action="append")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.listar:
        for nombre in microfonos():
            print(nombre)
        return 0
    if not args.microfono:
        ap.error("decí --microfono (los que hay: --listar)")
    guion = yaml.safe_load(args.guion.read_text(encoding="utf-8"))
    binarios = Path(os.path.expandvars(guion["binarios"]))
    carpeta = binarios / "voz"
    carpeta.mkdir(parents=True, exist_ok=True)
    largos = duraciones(binarios)
    lista = [x for x in a_locutar(guion) if not args.escena or x[0] in args.escena]
    for escena, texto in lista:
        destino = carpeta / f"{escena}.wav"
        print(f"\n── {escena}" + (f" · {largos[escena]} s en el video" if escena in largos else "") +
              (" · ya tiene voz" if destino.exists() else ""))
        print(f"  «{texto}»")
        while True:
            orden = input("  Enter graba · s salta · q termina ").strip().lower()
            if orden == "q":
                return 0
            if orden == "s":
                break
            grabar(args.microfono, destino)
            orden = input("  Enter queda · r repite · e escucha ").strip().lower()
            while orden == "e":
                escuchar(destino)
                orden = input("  Enter queda · r repite · e escucha ").strip().lower()
            if orden != "r":
                break
    print(f"\nlisto: {carpeta}. Armá de nuevo con editar.py (la voz entra sola).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
