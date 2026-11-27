"""Edición de un video: de las tomas y sus marcas a un mp4 con textos, placas, voz y música.

Uso:
  python editar.py <guion.yaml> --toma <n>                 # → video_<AAMMDD>_v<k>.mp4 (con las zonas de `tapar` difuminadas)
  python editar.py <guion.yaml> --toma <n> --interno       # → video_<AAMMDD>_v<k>_interno.mp4 (sin difuminar)
  python editar.py <guion.yaml> --toma 7 --toma 8          # la 8 regrabó unas escenas (`tomar.py --desde`)
  python editar.py <guion.yaml> --toma <n> --sin-voz       # sin la locución aunque esté grabada
  python editar.py <guion.yaml> --cuadros [--cada 0.5] [--video <mp4>]   # PNG por escena (o cada 0,5 s) para revisar
  python editar.py <guion.yaml> --muestras <n> [--cada 1]  # PNG de la toma n cada 1 s, con su segundo y su tamaño:
                                                           # para medir las zonas de `tapar` y de `recorte`

Reglas (tramos, velocidades, diseño, voz): CONTRATO.md, «Edición». Se ajustan con `edicion:` en el guion.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(os.environ.get("VIDEOS_SUPERFICIES", AQUI / "superficies"))))
import entorno  # noqa: E402

FPS = 30
_ffmpeg = entorno.ffmpeg   # nombre viejo, para los scripts de una instancia que lo usan
DEFECTO = {
    "letras_s": 15.0, "vel_escribir": 2.0, "espera_max": 3.0, "carga_max": 0.5, "vel_respuesta": 2.0, "leer": 2.0,
    "accion_max": 2.5, "hueco_max": 0.5, "placa_inicio": 2.5, "placa_fin": 3.0,
    "trabajo_max": 4.0,    # lo que trabaja un agente (terminal), comprimido a esto por paso
    "leer_hito": 1.2,      # cuadro congelado en cada hito del trabajo: el resultado de ese paso, para leerlo
    "vel_manual": 1.5,     # lo que hace la persona a mano (escritorio.manual)
    "musica_lufs": -16,    # la toma no trae sonido: sin voz, la música es lo único que suena
    "musica_lufs_con_voz": -27,   # con voz, la música va de fondo y además baja cuando se habla
    "voz_lufs": -16, "voz_retardo": 0.3, "voz_margen": 0.6,
    "transicion": "fade", "transicion_s": 0.5,   # de la toma a una escena de animación y de vuelta (xfade de ffmpeg)
}
# Formato celular: el logo arriba, la pantalla con esquinas redondeadas y el texto abajo, pegado, como un subtítulo
# (con el texto arriba no se podía mirar y leer a la vez). El logo va por ancho (logo_w), con un tope de alto.
CELULAR = {"w": 1080, "h": 1920, "logo_area": 130, "pantalla_y": 130, "pantalla_h": 1500, "pantalla_w": 1040,
           "banda_y": 1630, "banda_h": 290, "radio": 44, "logo_w": 230, "logo_h_max": 80, "texto": 64,
           "texto_ancho": 940, "sub_texto": 74, "sub_ancho": 960,
           "placa_y": 640, "placa_logo": 150, "placa_titulo": 76, "placa_sub": 46}
PC = {"w": 1920, "h": 1080, "logo_area": 66, "pantalla_y": 66, "pantalla_h": 864, "pantalla_w": 1840, "banda_y": 930,
      "banda_h": 150, "radio": 16, "logo_w": 150, "logo_h_max": 40, "texto": 50, "texto_ancho": 1700, "sub_texto": 60,
      "sub_ancho": 1500, "placa_y": 300, "placa_logo": 120, "placa_titulo": 72, "placa_sub": 40}
DISENO = {"celular": CELULAR, "pc": PC}
# `subtitulos: superpuestos`: sin banda, la pantalla más grande y el texto encima (como los Reels).
SUPERPUESTOS = {"celular": {"pantalla_h": 1730, "banda_h": 0}, "pc": {"pantalla_h": 990, "banda_h": 0}}
# Del celular emulado (1080×2400) se muestra hasta la barra de navegación. Con el teclado ADB a la vista (su cartel), se
# corta arriba del cartel y se completa con blanco, el fondo de Element X.
ALTO_UTIL, CORTE_TECLADO = 2336, 2222
DE_CLIP = {"animacion", "filmacion"}
HOLGURA = 0.2   # segundos de más a cada lado del lapso de una zona a difuminar
AUDIOS = (".wav", ".m4a", ".mp3", ".ogg", ".flac")


def correr(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-1500:])


def par(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def duracion(ruta: Path) -> float:
    r = subprocess.run([entorno.ffmpeg("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=nw=1:nk=1", str(ruta)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def tamano(ruta: Path) -> tuple[int, int]:
    """Ancho y alto como se ven (con la rotación del celular ya aplicada): del primer cuadro."""
    r = subprocess.run([entorno.ffmpeg(), "-v", "error", "-i", str(ruta), "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"no se pudo leer {ruta.name}: " + r.stderr.decode("utf-8", "replace")[-300:])
    return Image.open(io.BytesIO(r.stdout)).size


def base_de(guion_ruta: Path) -> Path:
    """Contra qué se resuelven las rutas relativas del guion (`marca`, `con.marca`): VIDEOS_BASE, la raíz del repo del
    guion o, fuera de un repo, su carpeta."""
    if os.environ.get("VIDEOS_BASE"):
        return Path(os.path.expandvars(os.environ["VIDEOS_BASE"]))
    r = subprocess.run(["git", "-C", str(guion_ruta.resolve().parent), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else guion_ruta.resolve().parent


def ruta_relativa(ruta: str, base: Path, guion_ruta: Path) -> Path:
    """Una ruta del guion: absoluta, relativa a `base` o, si ahí no está, a la carpeta del guion."""
    p = Path(os.path.expandvars(str(ruta)))
    if p.is_absolute():
        return p
    for candidata in (base / p, guion_ruta.resolve().parent / p):
        if candidata.exists():
            return candidata
    raise SystemExit(f"no encuentro {ruta} (ni en {base} ni al lado del guion)")


def con_teclado(ff: str, cfr: Path, s0: float, s1: float) -> bool:
    """Si en algún momento del tramo se ve el cartel del teclado ADB (una franja gris oscura pareja bajo el compositor)."""
    for t in (s0 + 0.05, (s0 + s1) / 2, max(s0, s1 - 0.05)):
        r = subprocess.run([ff, "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(cfr), "-frames:v", "1", "-vf",
                            f"crop=200:30:0:{CORTE_TECLADO + 10}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True)
        if r.stdout and 50 <= sum(r.stdout) / len(r.stdout) <= 85:
            return True
    return False


# --- marca y dibujos ----------------------------------------------------------------------------------------------

def _fuentes() -> Path | None:
    if os.environ.get("VIDEOS_FUENTES") or os.environ.get("VIDEOS_RAIZ"):
        return entorno.ruta("VIDEOS_FUENTES", "fuentes")
    return None


class Marca:
    """Colores, letras y logos de `marca.json`. Sin marca, una neutra sin logo."""

    def __init__(self, ruta: Path | None):
        datos = json.loads(ruta.read_text(encoding="utf-8")) if ruta else {}
        self.dir = ruta.parent if ruta else Path(".")
        self.verde = datos.get("color_marca", "#2563EB")          # el acento (nombre histórico)
        self.fondo = datos.get("color_texto_marca", "#1F2328")    # el fondo de placas y escenas
        self.familias = (datos.get("tipografia") or "Outfit", datos.get("tipografia_texto") or "Instrument Sans")
        self.logo_ancho = datos.get("logo_ancho_video")
        self.logo = self._png("logo_invertido.b64") if ruta else None
        self.logo_placa = (self._png("logo_placa.b64") if ruta else None) or self.logo
        self.logo_placa_alto = datos.get("logo_placa_alto_video")

    def _png(self, nombre: str) -> Image.Image | None:
        b64 = self.dir / nombre
        return Image.open(io.BytesIO(base64.b64decode(b64.read_text()))).convert("RGBA") if b64.exists() else None

    def fuente(self, titulo: bool, tam: int, peso: int = 600) -> ImageFont.FreeTypeFont:
        carpeta = _fuentes()
        archivo = None
        if carpeta:
            archivo = carpeta / (self.familias[0 if titulo else 1].replace(" ", "") + ".ttf")
            if not archivo.exists():
                archivo = carpeta / ("Outfit.ttf" if titulo else "InstrumentSans.ttf")
        if archivo is None or not archivo.exists():
            try:
                return ImageFont.truetype("segoeuib.ttf" if titulo else "segoeui.ttf", tam)
            except OSError:   # fuera de Windows
                return ImageFont.load_default(tam)
        f = ImageFont.truetype(str(archivo), tam)
        try:
            ejes = f.get_variation_axes()
            f.set_variation_by_axes([peso if b"Weight" in e.get("name", b"") else e.get("default", 100) for e in ejes])
        except (OSError, AttributeError):
            pass
        return f


def _logo(m: Marca, alto: int, imagen: Image.Image | None = None) -> Image.Image | None:
    imagen = imagen or m.logo
    if imagen is None:
        return None
    ancho = round(imagen.width * alto / imagen.height)
    return imagen.resize((ancho, alto), Image.LANCZOS)


def _renglones(d: ImageDraw.ImageDraw, texto: str, fuente, ancho_max: int) -> list[str]:
    renglones, actual = [], ""
    for p in texto.split():
        prueba = f"{actual} {p}".strip()
        if d.textlength(prueba, font=fuente) <= ancho_max or not actual:
            actual = prueba
        else:
            renglones.append(actual)
            actual = p
    return renglones + ([actual] if actual else [])


def _texto_centrado(d: ImageDraw.ImageDraw, w: int, y: int, texto: str, fuente, color, ancho_max: int,
                    interlineado: float = 1.18) -> int:
    """Escribe centrado, partiendo en renglones que entren en ancho_max. Devuelve la y de abajo."""
    alto = round(fuente.size * interlineado)
    for r in _renglones(d, texto, fuente, ancho_max):
        d.text(((w - d.textlength(r, font=fuente)) / 2, y), r, font=fuente, fill=color)
        y += alto
    return y


def fondo_escena(m: Marca, texto: str, destino: Path, c: dict = CELULAR) -> None:
    """El fondo de una escena: el logo arriba y, si el diseño tiene banda, el texto abajo de la pantalla."""
    img = Image.new("RGB", (c["w"], c["h"]), m.fondo)
    d = ImageDraw.Draw(img)
    ancho = m.logo_ancho or c["logo_w"]
    logo = m.logo and _logo(m, min(c["logo_h_max"], round(ancho * m.logo.height / m.logo.width)))
    if logo:
        img.paste(logo, ((c["w"] - logo.width) // 2, (c["logo_area"] - logo.height) // 2), logo)
    if texto and c["banda_h"]:
        f = m.fuente(True, c["texto"])
        alto_txt = len(_renglones(d, texto, f, c["texto_ancho"])) * round(c["texto"] * 1.18)
        y = c["banda_y"] + max(34, (c["banda_h"] - alto_txt) // 2 + 6)
        d.rounded_rectangle(((c["w"] - 90) // 2, y - 30, (c["w"] + 90) // 2, y - 22), radius=4, fill=m.verde)
        _texto_centrado(d, c["w"], y, texto, f, "white", c["texto_ancho"])
    img.save(destino)


def capa_texto(m: Marca, texto: str, destino: Path, c: dict, posicion: str = "abajo") -> None:
    """El texto sobre la pantalla (subtítulos superpuestos). Sin texto, una capa transparente."""
    img = Image.new("RGBA", (c["w"], c["h"]), (0, 0, 0, 0))
    if texto:
        d = ImageDraw.Draw(img)
        f = m.fuente(True, c["sub_texto"], 700)
        renglones = _renglones(d, texto, f, c["sub_ancho"])
        alto = round(c["sub_texto"] * 1.2)
        borde = max(4, round(c["sub_texto"] * 0.1))
        if posicion == "arriba":
            y = c["pantalla_y"] + round(c["pantalla_h"] * 0.08)
        else:
            y = c["pantalla_y"] + round(c["pantalla_h"] * 0.9) - alto * len(renglones)
        for r in renglones:
            x = (c["w"] - d.textlength(r, font=f)) / 2
            d.text((x, y), r, font=f, fill="white", stroke_width=borde, stroke_fill=(0, 0, 0, 235))
            y += alto
    img.save(destino)


def mascara(destino: Path, w: int, h: int, radio: int) -> None:
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, h - 1), radius=radio, fill=255)
    img.save(destino)


def _logo_con(con: dict, base: Path) -> Image.Image | None:
    """El logo de la otra marca de una placa compartida: `marca` es su carpeta (con logo_invertido.b64)."""
    ruta = Path(con["marca"])
    b64 = (ruta if ruta.is_absolute() else base / ruta) / "logo_invertido.b64"
    if not b64.exists():
        return None
    im = Image.open(io.BytesIO(base64.b64decode(b64.read_text()))).convert("RGBA")
    ancho = int(con.get("ancho", 520))
    return im.resize((ancho, round(im.height * ancho / im.width)), Image.LANCZOS)


def placa(m: Marca, titulo: str, subtitulo: str, destino: Path, c: dict = CELULAR, con: dict | None = None,
          base: Path = Path(".")) -> None:
    """La placa con el logo. `con` suma otra marca: arriba, unida con «×» (`posicion: arriba`), o abajo como firma."""
    img = Image.new("RGB", (c["w"], c["h"]), m.fondo)
    d = ImageDraw.Draw(img)
    otro = _logo_con(con, base) if con else None
    if otro and con.get("posicion", "arriba") == "arriba":
        y0 = int(con.get("y", 170))
        img.paste(otro, ((c["w"] - otro.width) // 2, y0), otro)
        _texto_centrado(d, c["w"], y0 + otro.height + 18, "×", m.fuente(False, 54, 400), "#9AA0A2", c["w"] - 160)
    alto = m.logo_placa_alto or c["placa_logo"]
    logo = _logo(m, alto, m.logo_placa)
    y = c["placa_y"] - max(0, alto - c["placa_logo"]) // 2
    if logo:
        img.paste(logo, ((c["w"] - logo.width) // 2, y), logo)
        y += logo.height + 110
    d.rounded_rectangle(((c["w"] - 120) // 2, y - 50, (c["w"] + 120) // 2, y - 40), radius=5, fill=m.verde)
    y = _texto_centrado(d, c["w"], y, titulo, m.fuente(True, c["placa_titulo"]), "white", c["w"] - 160)
    if subtitulo:
        y = _texto_centrado(d, c["w"], y + 24, subtitulo, m.fuente(False, c["placa_sub"], 500), "#C9CCCD", c["w"] - 180)
    if otro and con.get("posicion") == "abajo":
        y = max(y + 110, int(con.get("y", 0)))
        if con.get("texto"):
            y = _texto_centrado(d, c["w"], y, con["texto"], m.fuente(False, 40, 500), "#9AA0A2", c["w"] - 180) + 18
        img.paste(otro, ((c["w"] - otro.width) // 2, y), otro)
        y += otro.height + 20
        if con.get("pie"):
            _texto_centrado(d, c["w"], y, con["pie"], m.fuente(False, c["placa_sub"], 500), "#C9CCCD", c["w"] - 180)
    img.save(destino)


# --- tramos -------------------------------------------------------------------------------------------------------

def segmentos(marcas: dict, aj: dict) -> list[dict]:
    """De los tramos de la toma a segmentos de edición: fuente [s0, s1], velocidad y escena. Un segmento con `congelar`
    es un cuadro quieto (el de s0) durante esos segundos."""
    tramos = sorted(marcas["tramos"], key=lambda t: t["t0"])
    escenas = marcas["escenas"]

    def escena_de(t: float) -> str:
        for e in escenas:
            if e["t0"] <= t < e["t1"]:
                return e["id"]
        return escenas[-1]["id"] if escenas else ""

    segs, cursor = [], escenas[0]["t0"] if escenas else 0.0
    for t in tramos:
        if t["t0"] > cursor + 0.05:  # hueco: lo que tarda la herramienta entre un paso y otro
            segs.append({"escena": escena_de(cursor), "s0": cursor, "s1": min(t["t0"], cursor + aj["hueco_max"]),
                         "vel": 1.0})
        s0, s1, d, vel = t["t0"], t["t1"], t["t1"] - t["t0"], 1.0
        tipo = t["tipo"]
        if tipo == "trabajo":   # el agente trabajando: acelerado, con un cuadro quieto en cada hito
            vel = max(1.0, d / float(t.get("comprimir_a", aj["trabajo_max"])))
            puntos = sorted(h["t"] for h in t.get("hitos", []) if s0 < h["t"] < s1)
            desde = s0
            for p in puntos:
                segs.append({"escena": t["escena"], "s0": desde, "s1": p, "vel": vel})
                segs.append({"escena": t["escena"], "s0": p, "s1": p, "vel": 1.0, "congelar": aj["leer_hito"]})
                desde = p
            segs.append({"escena": t["escena"], "s0": desde, "s1": s1, "vel": vel})
            cursor = max(cursor, s1)
            continue
        if tipo == "escribir":  # con `letras`, siempre al mismo ritmo; una toma vieja no las tiene
            vel = min(8.0, max(0.5, d * aj["letras_s"] / t["letras"])) if t.get("letras") else aj["vel_escribir"]
        elif tipo == "carga" or t.get("accion") == "android.esperar_carga":
            vel = max(1.0, d / aj["carga_max"])
        elif tipo == "espera":
            vel = max(1.0, d / aj["espera_max"])
        elif tipo == "respuesta":
            vel = aj["vel_respuesta"]
        elif tipo == "manual":
            vel = aj["vel_manual"]
        elif tipo == "lectura":
            s1 = s0 + min(d, float(t.get("leer", aj["leer"])))
        elif tipo == "accion":
            s1 = s0 + min(d, aj["accion_max"])
        segs.append({"escena": t["escena"], "s0": s0, "s1": s1, "vel": vel})
        cursor = max(cursor, t["t1"])
    # se juntan los vecinos de la misma escena y velocidad 1 que son continuos en la fuente
    juntos: list[dict] = []
    for s in segs:
        if s.get("congelar"):
            juntos.append(dict(s))
            continue
        if s["s1"] - s["s0"] < 0.04:
            continue
        p = juntos[-1] if juntos else None
        if (p and not p.get("congelar") and p["escena"] == s["escena"] and p["vel"] == s["vel"] == 1.0
                and abs(p["s1"] - s["s0"]) < 0.05):
            p["s1"] = s["s1"]
        else:
            juntos.append(dict(s))
    return juntos


def zonas_de(guion: dict, escena: dict, toma: int | None, s0: float, s1: float) -> list[dict]:
    """Las zonas a difuminar que tocan el tramo [s0, s1] de esa toma: las del guion y las de la escena. Una zona puede
    limitarse a una toma (`toma`) y a un lapso (`desde`, `hasta`, en segundos de la toma)."""
    zonas = []
    for z in list(guion.get("tapar") or []) + list(escena.get("tapar") or []):
        if toma is not None and z.get("toma") is not None and int(z["toma"]) != toma:
            continue
        desde, hasta = float(z.get("desde", -1e9)) - HOLGURA, float(z.get("hasta", 1e9)) + HOLGURA
        if hasta < s0 or desde > s1:
            continue
        zonas.append(z)
    return zonas


def escenas_sin_zona(guion: dict, duenia: dict[str, int]) -> list[str]:
    """Con datos reales: las escenas que salen de una toma o de una filmación y no tienen ninguna zona que las alcance
    (en la toma de la que salen), salvo que digan `sin_datos: true`."""
    faltan = []
    for e in guion["escenas"]:
        if e.get("sin_datos") or e.get("superficie") == "animacion":
            continue
        if e.get("superficie") == "filmacion":
            toma = None
        elif e["id"] in duenia:
            toma = duenia[e["id"]]
        else:
            continue   # no está en estas tomas
        if not zonas_de(guion, e, toma, -1e9, 1e9):
            faltan.append(e["id"])
    return faltan


def rectangulo(z: dict, ancho: int | None = None, alto: int | None = None) -> tuple[int, int, int, int] | None:
    """Un rectángulo del guion en píxeles pares (se agranda: tapa de más, nunca de menos), recortado al cuadro de
    ancho×alto. None si queda afuera."""
    x0, y0 = max(0, int(z["x"])), max(0, int(z["y"]))
    x1, y1 = int(z["x"]) + int(z["w"]), int(z["y"]) + int(z["h"])
    if ancho is not None:
        x1 = min(x1, ancho)
    if alto is not None:
        y1 = min(y1, alto)
    x, y = x0 // 2 * 2, y0 // 2 * 2
    w, h = -(-(x1 - x) // 2) * 2, -(-(y1 - y) // 2) * 2
    if ancho is not None and x + w > ancho:
        w = (ancho - x) // 2 * 2
    if alto is not None and y + h > alto:
        h = (alto - y) // 2 * 2
    return (x, y, w, h) if w >= 2 and h >= 2 else None


def filtro_tapar(zonas: list[dict], s0: float, entrada: str, salida: str, ancho: int | None = None,
                 alto: int | None = None) -> str:
    """Filtro de ffmpeg que difumina cada zona (x, y, w, h en píxeles de la toma, recortada a ancho×alto) hasta que no
    se lea: la reduce hasta que su lado corto queda en unos 3 píxeles (al menos a un doceavo) y la vuelve a agrandar;
    un texto del alto de la zona queda en manchas. El tiempo `t` del filtro empieza en s0 de la toma; el lapso de una
    zona se agranda HOLGURA de cada lado, para que el primer cuadro de un corte no quede afuera."""
    partes, actual, i = [], entrada, 0
    for z in zonas:
        r = rectangulo(z, ancho, alto)
        if r is None:
            continue
        x, y, w, h = r
        i += 1
        cuando = ""
        if z.get("desde") is not None or z.get("hasta") is not None:
            a = float(z.get("desde", -1e9)) - s0 - HOLGURA
            b = float(z.get("hasta", 1e9)) - s0 + HOLGURA
            cuando = f":enable='between(t\\,{a:.3f}\\,{b:.3f})'"
        factor = max(12, min(w, h) // 3)
        partes.append(f"{actual}split[tb{i}][tz{i}];[tz{i}]crop={w}:{h}:{x}:{y},scale={max(2, w // factor)}:"
                      f"{max(2, h // factor)}:flags=area,scale={w}:{h}:flags=bicubic[tf{i}];"
                      f"[tb{i}][tf{i}]overlay={x}:{y}{cuando}[to{i}]")
        actual = f"[to{i}]"
    return ";".join(partes + [f"{actual}null{salida}"])


def encaje(sw: int, sh: int, c: dict) -> tuple[int, int, int, int]:
    """Dónde va la pantalla de la toma (sw×sh) en el video: el mayor tamaño que entra en la caja de la pantalla."""
    escala = min(c["pantalla_w"] / sw, c["pantalla_h"] / sh)
    pw, ph = par(sw * escala), par(sh * escala)
    return pw, ph, (c["w"] - pw) // 2, c["pantalla_y"] + (c["pantalla_h"] - ph) // 2


# --- música y voz -------------------------------------------------------------------------------------------------

def _pcm(ff: str, ruta: str, sr: int) -> np.ndarray:
    r = subprocess.run([ff, "-v", "error", "-i", ruta, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768


def salto_musica(x: np.ndarray, sr: int, alargar: float, cola: float = 8.0, ventana: float = 1.5,
                 holgura: float = 6.0) -> tuple[float, float]:
    """Dónde saltar hacia atrás para que el tema dure `alargar` s más: el par (desde, hasta) donde el sonido alrededor
    de los dos puntos más se parece (coincide el pulso) y el volumen de la parte es parecido."""
    n, hop = 2048, 512
    cuadros = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    esp = np.log1p(np.abs(np.fft.rfft(cuadros, axis=1)))
    bordes = np.unique(np.geomspace(1, esp.shape[1], 65).astype(int))
    f = np.add.reduceat(esp, bordes[:-1], axis=1)
    f -= f.mean(axis=0)
    f /= np.linalg.norm(f, axis=1, keepdims=True) + 1e-9
    fps = sr / hop
    w = int(ventana * fps)
    W = int(5 * fps)
    rms = np.sqrt((cuadros ** 2).mean(axis=1))
    nivel = 20 * np.log10(np.convolve(rms, np.ones(2 * W + 1) / (2 * W + 1), "same") + 1e-9)
    mejor = (-np.inf, 0, 0)
    for d in range(int(alargar * fps), int((alargar + holgura) * fps) + 1):
        if len(f) - d < 2 * w + 1:
            break
        sim = (f[d:] * f[:-d]).sum(axis=1)
        media = np.convolve(sim, np.ones(2 * w + 1) / (2 * w + 1), "valid")
        hasta = np.arange(len(media)) + w
        puntaje = media - np.abs(nivel[hasta + d] - nivel[hasta]) / 20
        ok = (hasta >= 2 * w) & (hasta + d <= len(f) - cola * fps)
        if ok.any():
            j = int(np.argmax(np.where(ok, puntaje, -np.inf)))
            if puntaje[j] > mejor[0]:
                mejor = (float(puntaje[j]), j + w + d, j + w)
    if mejor[0] == -np.inf:
        raise ValueError(f"no entra un salto de {alargar:.1f} s en el tema")
    desde, hasta, m = mejor[1] * hop + n // 2, mejor[2] * hop + n // 2, int(ventana * sr)
    a = x[desde - m:desde + m]
    k = max(range(-hop, hop + 1), key=lambda k: float(np.dot(a, x[hasta + k - m:hasta + k + m])))
    return desde / sr, (hasta + k) / sr


def filtro_musica(ff: str, musica: str, largo: float, lufs: float, cruce: float = 0.3) -> str:
    """El filtro (sobre [1:a]) que deja la música del largo del video, a `lufs`, con fundido de salida, en [a]."""
    sr = 22050
    x = _pcm(ff, musica, sr)
    dur = len(x) / sr
    fin = (f"atrim=0:{largo:.3f},loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo,"
           f"afade=t=out:st={max(0, largo - 2):.2f}:d=2[a]")
    if dur >= largo:
        return f"[1:a]{fin}"
    falta = largo - dur
    for saltos in range(1, 10):
        try:
            desde, hasta = salto_musica(x, sr, falta / saltos)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"el tema ({dur:.0f} s) es demasiado corto para un video de {largo:.0f} s")
    print(f"música: {dur:.1f} s; salta de {desde:.2f} s a {hasta:.2f} s ({saltos} {'vez' if saltos == 1 else 'veces'})")
    tramos = [(0, desde + cruce)] + [(hasta - cruce, desde + cruce)] * (saltos - 1) + [(hasta - cruce, dur)]
    filtro = f"[1:a]asplit={len(tramos)}" + "".join(f"[m{i}]" for i in range(len(tramos))) + ";"
    filtro += "".join(f"[m{i}]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[t{i}];" for i, (a, b) in enumerate(tramos))
    previo = "t0"
    for i in range(1, len(tramos)):
        filtro += f"[{previo}][t{i}]acrossfade=d={2 * cruce}[x{i}];"
        previo = f"x{i}"
    return filtro + f"[{previo}]{fin}"


def filtro_audio(voces: list[tuple[int, float]], con_musica: bool, largo: float, aj: dict) -> str:
    """Las voces (índice de entrada, segundo en que empieza) mezcladas en [sal]; con música (ya en [a]), la música baja
    sola mientras se habla. Devuelve la parte del filtro que sigue a la de la música."""
    partes = []
    for j, (idx, t) in enumerate(voces):
        ms = int(round(t * 1000))
        partes.append(f"[{idx}:a]aresample=48000,loudnorm=I={aj['voz_lufs']}:TP=-1.5:LRA=11,aresample=48000,"
                      f"aformat=channel_layouts=stereo,adelay=delays={ms}:all=1[v{j}]")
    if len(voces) == 1:
        partes.append("[v0]anull[voz]")
    else:
        partes.append("".join(f"[v{j}]" for j in range(len(voces))) +
                      f"amix=inputs={len(voces)}:normalize=0:duration=longest[voz]")
    partes[-1] = partes[-1][:-len("[voz]")] + "[vc]"
    partes.append(f"[vc]apad=whole_dur={largo:.3f}[voz]")   # hasta el final: si no, la música se corta con la última voz
    if con_musica:
        partes.append("[voz]asplit=2[vz1][vz2];[a][vz1]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400"
                      "[md];[md][vz2]amix=inputs=2:normalize=0:duration=first[sal]")
    else:
        partes.append("[voz]anull[sal]")
    return ";".join(partes)


def voz_de(binarios: Path, escena: str) -> Path | None:
    for ext in AUDIOS:
        p = binarios / "voz" / f"{escena}{ext}"
        if p.exists():
            return p
    return None


# --- armado -------------------------------------------------------------------------------------------------------

def nombre_video(binarios: Path, interno: bool, hoy: str, reservar: bool = False) -> Path:
    """El próximo `video_<hoy>_v<k>[_interno].mp4`. Con `reservar`, lo toma creando su `.escenas.json` vacío: dos
    armados a la vez no eligen el mismo nombre."""
    patron = re.compile(rf"video_{hoy}_v(\d+)(_interno)?$")
    while True:
        hechos = [int(mt.group(1)) for p in list(binarios.glob(f"video_{hoy}_v*.mp4")) +
                  [q.with_suffix("") for q in binarios.glob(f"video_{hoy}_v*.escenas.json")]
                  if (mt := patron.match(p.stem))]
        final = binarios / f"video_{hoy}_v{max(hechos, default=0) + 1}{'_interno' if interno else ''}.mp4"
        if not reservar:
            return final
        try:
            with open(final.with_suffix(".escenas.json"), "x", encoding="utf-8"):
                return final
        except FileExistsError:
            continue


def armar(guion_ruta: Path, tomas: list[int], interno: bool = False, con_voz: bool = True) -> Path:
    """Con varias tomas (una regrabación con `tomar.py --desde`), cada escena sale de la última toma que la tiene."""
    guion = yaml.safe_load(guion_ruta.read_text(encoding="utf-8"))
    entorno.aplicar(guion)
    formato = guion.get("formato", "celular")
    if formato not in DISENO:
        raise SystemExit(f"formato desconocido «{formato}»: celular o pc (CONTRATO.md)")
    aj = {**DEFECTO, **(guion.get("edicion") or {})}
    binarios = Path(os.path.expandvars(guion["binarios"]))
    base = base_de(guion_ruta)
    marcas = {n: json.loads((binarios / f"toma_{n}.marcas.json").read_text(encoding="utf-8")) for n in tomas}
    duenia = {e["id"]: n for n in tomas for e in marcas[n]["escenas"]}
    if guion.get("datos_reales") and not interno:
        faltan = escenas_sin_zona(guion, duenia)
        if faltan:
            raise SystemExit(f"`datos_reales: true` y estas escenas no tienen ninguna zona en `tapar` que las alcance en "
                             f"su toma: {', '.join(faltan)}. Medí las zonas (--muestras), declará `sin_datos: true` en "
                             "la escena que no muestra datos, o armá la interna (--interno)")
    m = Marca(ruta_relativa(guion.get("marca"), base, guion_ruta) if guion.get("marca") else None)
    final = nombre_video(binarios, interno, date.today().strftime("%y%m%d"), reservar=True)
    try:
        trabajo = binarios / f"_trabajo_{final.stem}"
        trabajo.mkdir(exist_ok=True)
        ff = entorno.ffmpeg()
        subtitulos = guion.get("subtitulos", "abajo")
        if subtitulos not in ("abajo", "superpuestos"):
            raise SystemExit(f"subtitulos: «{subtitulos}» no existe: abajo o superpuestos (CONTRATO.md)")
        c = {**DISENO[formato], **(SUPERPUESTOS[formato] if subtitulos == "superpuestos" else {})}
        por_id = {e["id"]: e for e in guion["escenas"]}

        # 1. cada toma a 30 cuadros fijos (scrcpy y el navegador solo mandan cuadros cuando la pantalla cambia)
        cfr: dict[int, Path] = {}
        medida: dict[int, tuple[int, int]] = {}
        for n in tomas:
            toma, cfr[n] = binarios / f"toma_{n}.mkv", binarios / f"_trabajo_toma_{n}" / "cfr.mp4"
            cfr[n].parent.mkdir(exist_ok=True)
            if not cfr[n].exists() or cfr[n].stat().st_mtime < toma.stat().st_mtime:
                correr([ff, "-y", "-i", str(toma), "-vf", f"fps={FPS},tpad=stop_mode=clone:stop_duration=30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-an", str(cfr[n])])
            medida[n] = tamano(cfr[n])

        # 2. dibujos: fondo y capa de texto por escena
        for e in guion["escenas"]:
            texto = e.get("texto", "")
            fondo_escena(m, texto, trabajo / f"fondo_{e['id']}.png", c)
            capa_texto(m, texto if subtitulos == "superpuestos" else "", trabajo / f"capa_{e['id']}.png", c,
                       e.get("texto_posicion", "abajo"))

        def la_mascara(pw: int, ph: int) -> Path:
            ruta = trabajo / f"mascara_{pw}x{ph}.png"
            if not ruta.exists():
                mascara(ruta, pw, ph, c["radio"])
            return ruta

        def geometria(e: dict, sw: int, sh: int, celular_emulado: bool) -> tuple[str, tuple[int, int, int, int]]:
            """El recorte de la fuente (filtro) y dónde va en el video."""
            if e.get("recorte"):
                r = rectangulo(e["recorte"], sw, sh)
                if r is None:
                    raise SystemExit(f"escena «{e['id']}»: el recorte queda fuera de la toma ({sw}×{sh})")
                x, y, w, h = r
                return f"crop={w}:{h}:{x}:{y}", encaje(w, h, c)
            if celular_emulado:
                return f"crop=1080:{ALTO_UTIL}:0:0", encaje(1080, ALTO_UTIL, c)
            return "null", encaje(sw, sh, c)

        def componer(fuente: str, e_id: str, pw: int, ph: int, px: int, py: int) -> str:
            """De [fuente] (la pantalla ya recortada y a su velocidad) al cuadro del video, sobre el fondo de la escena."""
            return (f"{fuente}scale={pw}:{ph},format=rgba[p];[2:v]format=gray,scale={pw}:{ph}[k];[p][k]alphamerge[r];"
                    f"[1:v][r]overlay={px}:{py}[f];[f][3:v]overlay=0:0,fps={FPS},format=yuv420p")

        # 3. los clips de cada escena: los segmentos de la toma y las filmaciones
        clips: dict[str, list[tuple[Path, float]]] = {}
        orden = {e["id"]: i for i, e in enumerate(guion["escenas"])}
        segs = [{**s, "toma": n} for n in tomas for s in segmentos(marcas[n], aj) if duenia.get(s["escena"]) == n]
        segs.sort(key=lambda s: orden.get(s["escena"], len(orden)))   # estable: dentro de una escena, el orden de la toma
        for i, s in enumerate(segs):
            e = por_id.get(s["escena"], {"id": s["escena"]})
            congelar = s.get("congelar")
            dur_out = float(congelar) if congelar else (s["s1"] - s["s0"]) / s["vel"]
            if dur_out < 1 / FPS:
                continue
            sw, sh = medida[s["toma"]]
            emulado = formato == "celular" and e.get("superficie", "android") == "android"
            recorte, (pw, ph, px, py) = geometria(e, sw, sh, emulado)
            if emulado and not e.get("recorte") and con_teclado(ff, cfr[s["toma"]], s["s0"], max(s["s0"], s["s1"])):
                recorte = f"crop=1080:{CORTE_TECLADO}:0:0,pad=1080:{ALTO_UTIL}:0:0:color=white"
            zonas = [] if interno else zonas_de(guion, e, s["toma"], s["s0"], max(s["s1"], s["s0"] + 0.05))
            tiempo = ("trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=" + f"{dur_out:.3f}"
                      if congelar else f"setpts=(PTS-STARTPTS)/{s['vel']:.4f}")
            filtro = (filtro_tapar(zonas, s["s0"], "[0:v]", "[t]", sw, sh) + f";[t]{recorte},{tiempo}[s];" +
                      componer("[s]", e["id"], pw, ph, px, py))
            out = trabajo / f"s_{i:03d}.mp4"
            entrada = ["-ss", f"{s['s0']:.3f}", *([] if congelar else ["-to", f"{s['s1']:.3f}"]), "-i", str(cfr[s["toma"]])]
            correr([ff, "-y", *entrada,
                    "-loop", "1", "-i", str(trabajo / f"fondo_{e['id']}.png"),
                    "-loop", "1", "-i", str(la_mascara(pw, ph)),
                    "-loop", "1", "-i", str(trabajo / f"capa_{e['id']}.png"),
                    "-filter_complex", filtro, "-t", f"{dur_out:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
            clips.setdefault(s["escena"], []).append((out, dur_out))

        for e in guion["escenas"]:
            if e.get("superficie") == "filmacion":
                clips[e["id"]] = [clip_filmacion(e, guion, binarios, trabajo, c, aj, interno, geometria, componer,
                                                 la_mascara)]

        # 4. en el orden del guion: placas, clips, animaciones y, donde haya voz, lo que haga falta para que entre
        piezas: list[Path] = []
        tiempos: list[dict] = []
        voces: list[tuple[Path, float]] = []
        t_salida = 0.0

        def sumar(archivo: Path, dur: float, escena: str) -> None:
            nonlocal t_salida
            piezas.append(archivo)
            if tiempos and tiempos[-1]["escena"] == escena:
                tiempos[-1]["t1"] = round(t_salida + dur, 2)
            else:
                tiempos.append({"escena": escena, "t0": round(t_salida, 2), "t1": round(t_salida + dur, 2)})
            t_salida += dur

        def cuadro(video: Path, destino: Path, ultimo: bool) -> Path:
            if ultimo:   # el último cuadro de verdad: se escribe cada uno de los del final y queda el último
                correr([ff, "-y", "-sseof", "-0.5", "-i", str(video), "-update", "1", str(destino)])
            else:
                correr([ff, "-y", "-i", str(video), "-frames:v", "1", str(destino)])
            return destino

        def quieto(png: Path, dur: float, destino: Path, fundido: bool = False) -> Path:
            fades = f",fade=t=in:st=0:d=0.4,fade=t=out:st={dur - 0.4:.3f}:d=0.4" if fundido else ""
            correr([ff, "-y", "-loop", "1", "-i", str(png), "-t", f"{dur:.3f}", "-vf", f"fps={FPS}{fades},format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(destino)])
            return destino

        def con_voz_de(escena: str, t0: float) -> float:
            """La voz de la escena, si hay: anotada para la mezcla; devuelve cuánto tiene que durar la escena como mínimo."""
            if not con_voz:
                return 0.0
            v = voz_de(binarios, escena)
            if v is None:
                return 0.0
            voces.append((v, t0 + aj["voz_retardo"]))
            return duracion(v) + aj["voz_retardo"] + aj["voz_margen"]

        pl = guion.get("placas") or {}

        def clip_placa(clave: str, dur: float) -> None:
            datos = pl.get(clave)
            if not datos:
                return
            dur = max(dur, con_voz_de(f"placa_{clave}", t_salida))
            png = trabajo / f"placa_{clave}.png"
            placa(m, datos.get("titulo", ""), datos.get("subtitulo", ""), png, c, datos.get("con"), base)
            sumar(quieto(png, dur, trabajo / f"p_{clave}.mp4", fundido=True), dur, f"placa_{clave}")

        def clip_animacion(e: dict, hacia: Path | None) -> tuple[Path, float]:
            dur, tr = float(e.get("segundos", 5)), float(aj["transicion_s"])
            desde = cuadro(piezas[-1], trabajo / f"a_{e['id']}_desde.png", True)
            q = f"fps={FPS},format=yuv420p,settb=AVTB"
            py = c["pantalla_y"]
            entradas = ["-loop", "1", "-t", f"{tr}", "-i", str(desde),
                        "-loop", "1", "-i", str(trabajo / f"fondo_{e['id']}.png"),
                        "-stream_loop", "-1", "-i", str(binarios / e["archivo"]),
                        "-loop", "1", "-i", str(trabajo / f"capa_{e['id']}.png")]
            filtro = (f"[2:v]scale={c['w'] - 80}:-2,setpts=PTS-STARTPTS[an];"
                      f"[1:v][an]overlay=(W-w)/2:{py}+({c['pantalla_h']}-h)/2[fa];[fa][3:v]overlay=0:0,"
                      f"trim=duration={dur},setpts=PTS-STARTPTS,{q}[b];[0:v]{q}[a];"
                      f"[a][b]xfade=transition={aj['transicion']}:duration={tr}:offset=0")
            if hacia is not None:
                entradas += ["-loop", "1", "-t", f"{tr}", "-i", str(hacia)]
                filtro += f"[ab];[4:v]{q}[c];[ab][c]xfade=transition={aj['transicion']}:duration={tr}:offset={dur - tr}"
            out = trabajo / f"a_{e['id']}.mp4"
            # yuv420p explícito: si no, sale en 4:4:4 y, pegado sin recodificar, muchos reproductores cortan ahí
            correr([ff, "-y", *entradas, "-filter_complex", filtro, "-t", f"{dur}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
            return out, dur

        clip_placa("inicio", aj["placa_inicio"])
        escenas = guion["escenas"]
        for k, e in enumerate(escenas):
            t0 = t_salida
            if e.get("superficie") == "animacion":
                if not piezas:
                    raise SystemExit(f"la animación «{e['id']}» no puede ir primera: necesita algo antes (una placa)")
                siguiente = next((clips[x["id"]][0][0] for x in escenas[k + 1:] if clips.get(x["id"])), None)
                hacia = cuadro(siguiente, trabajo / f"a_{e['id']}_hacia.png", False) if siguiente else None
                sumar(*clip_animacion(e, hacia), e["id"])
            else:
                for archivo, dur in clips.get(e["id"], []):
                    sumar(archivo, dur, e["id"])
            if t_salida == t0:
                continue   # la escena no está en estas tomas
            falta = con_voz_de(e["id"], t0) - (t_salida - t0)
            if falta > 1 / FPS:   # la voz es más larga que la escena: se queda quieta en su último cuadro
                png = cuadro(piezas[-1], trabajo / f"q_{e['id']}.png", True)
                sumar(quieto(png, falta, trabajo / f"q_{e['id']}.mp4"), falta, e["id"])
        clip_placa("fin", aj["placa_fin"])
        if not piezas:
            raise SystemExit("no quedó nada para armar: ¿las tomas tienen las escenas del guion?")

        # 5. todo junto, con música y voz si las hay
        lista = trabajo / "lista.txt"
        lista.write_text("".join(f"file '{p.as_posix()}'\n" for p in piezas), encoding="utf-8")
        musica = guion.get("musica")
        cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lista)]
        filtro = ""
        if musica:
            musica = os.path.expandvars(musica)
            cmd += ["-i", musica]
            filtro = filtro_musica(ff, musica, t_salida, aj["musica_lufs_con_voz"] if voces else aj["musica_lufs"])
        if voces:
            primero = 2 if musica else 1
            for v, _ in voces:
                cmd += ["-i", str(v)]
            filtro = ";".join(x for x in (filtro, filtro_audio([(primero + j, t) for j, (_, t) in enumerate(voces)],
                                                                 bool(musica), t_salida, aj)) if x)
        if filtro:
            cmd += ["-filter_complex", filtro, "-map", "0:v", "-map", "[sal]" if voces else "[a]",
                    "-c:a", "aac", "-b:a", "160k", "-t", f"{t_salida:.3f}"]
        cmd += ["-c:v", "copy", "-movflags", "+faststart", str(final)]
        correr(cmd)
        final.with_suffix(".escenas.json").write_text(json.dumps(
            {"tomas": tomas, "interno": interno, "voces": [{"archivo": v.name, "t": round(t, 2)} for v, t in voces],
             "escenas": tiempos}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{final} ({t_salida:.1f} s)")
        return final
    except BaseException:
        reserva = final.with_suffix(".escenas.json")   # el nombre reservado vuelve a quedar libre
        if reserva.exists() and reserva.stat().st_size == 0:
            reserva.unlink()
        raise


def clip_filmacion(e: dict, guion: dict, binarios: Path, trabajo: Path, c: dict, aj: dict, interno: bool,
                   geometria, componer, la_mascara) -> tuple[Path, float]:
    """Una escena filmada aparte (con un celular): sus tramos, cada uno a su velocidad, sobre el fondo de la escena."""
    ff = entorno.ffmpeg()
    video = binarios / e["archivo"]
    if not video.exists():
        raise SystemExit(f"la filmación «{e['id']}» no está: {video}")
    tramos = e.get("tramos") or [{"desde": e.get("desde", 0), "hasta": e.get("hasta"), "vel": e.get("vel", 1)}]
    total = duracion(video)
    partes, dur = [], 0.0
    for t in tramos:
        a, b, vel = float(t.get("desde") or 0), float(t["hasta"] if t.get("hasta") is not None else total), \
            float(t.get("vel") or 1)
        if b <= a or vel <= 0 or (b - a) / vel < 2 / FPS:
            raise SystemExit(f"filmación «{e['id']}»: tramo {t} vacío, de menos de dos cuadros o con velocidad inválida")
        partes.append((a, b, vel))
        dur += (b - a) / vel
    sw, sh = tamano(video)
    recorte, (pw, ph, px, py) = geometria(e, sw, sh, False)
    zonas = [] if interno else zonas_de(guion, {"tapar": e.get("tapar")}, None, 0, total)
    filtro = filtro_tapar(zonas, 0.0, "[0:v]", "[t]", sw, sh) + ";"
    filtro += f"[t]{recorte},split={len(partes)}" + "".join(f"[f{i}]" for i in range(len(partes))) + ";"
    filtro += "".join(f"[f{i}]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{vel:.4f},fps={FPS}[g{i}];"
                      for i, (a, b, vel) in enumerate(partes))
    filtro += "".join(f"[g{i}]" for i in range(len(partes))) + f"concat=n={len(partes)}:v=1:a=0[s];"
    filtro += componer("[s]", e["id"], pw, ph, px, py)
    out = trabajo / f"f_{e['id']}.mp4"
    correr([ff, "-y", "-i", str(video),
            "-loop", "1", "-i", str(trabajo / f"fondo_{e['id']}.png"),
            "-loop", "1", "-i", str(la_mascara(pw, ph)),
            "-loop", "1", "-i", str(trabajo / f"capa_{e['id']}.png"),
            "-filter_complex", filtro, "-t", f"{dur:.3f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
    return out, dur


def instantes(largo: float, cada: float) -> list[float]:
    n = int(largo / cada) + 1
    return [round(k * cada, 3) for k in range(n) if k * cada < largo]


def cuadro_en(video: Path, t: float, destino: Path) -> None:
    """El cuadro que se ve en el segundo t (búsqueda exacta: el nombre dice el segundo de verdad)."""
    correr([entorno.ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(destino)])


def cuadros(guion_ruta: Path, cada: float | None = None, video: Path | None = None) -> Path:
    """Un PNG por escena del último video (o del que se diga), o uno cada `cada` segundos para revisar todo."""
    guion = yaml.safe_load(guion_ruta.read_text(encoding="utf-8"))
    binarios = Path(os.path.expandvars(guion["binarios"]))
    if video is None:   # el último de difusión: es el que se revisa antes de mostrar; la interna se pide con --video
        videos = sorted((p for p in binarios.glob("video_*_v*.mp4") if not p.stem.endswith("_interno")),
                        key=lambda p: p.stat().st_mtime)
        if not videos:
            raise SystemExit("todavía no hay video de difusión (para la interna: --video <mp4>)")
        video = videos[-1]
        print(f"reviso {video.name}")
    carpeta = binarios / f"cuadros_{video.stem}"
    carpeta.mkdir(exist_ok=True)
    ff = entorno.ffmpeg()
    if cada:
        for t in instantes(duracion(video), cada):
            cuadro_en(video, t, carpeta / f"{t:07.2f}.png")
    else:
        escenas = json.loads(video.with_suffix(".escenas.json").read_text(encoding="utf-8"))["escenas"]
        for e in escenas:
            medio = (e["t0"] + e["t1"]) / 2
            correr([ff, "-y", "-ss", f"{medio:.2f}", "-i", str(video), "-frames:v", "1",
                    str(carpeta / f"{e['t0']:06.1f}_{e['escena']}.png")])
    print(carpeta)
    return carpeta


def muestras(guion_ruta: Path, toma: int, cada: float = 1.0) -> Path:
    """Cuadros de la toma, con el segundo de la toma y la escena en el nombre, para medir `tapar` y `recorte`."""
    guion = yaml.safe_load(guion_ruta.read_text(encoding="utf-8"))
    binarios = Path(os.path.expandvars(guion["binarios"]))
    marcas = json.loads((binarios / f"toma_{toma}.marcas.json").read_text(encoding="utf-8"))
    video = binarios / f"toma_{toma}.mkv"
    carpeta = binarios / f"muestras_toma_{toma}"
    carpeta.mkdir(exist_ok=True)
    for t in instantes(duracion(video), cada):
        escena = next((e["id"] for e in marcas["escenas"] if e["t0"] <= t < e["t1"]), "fuera")
        cuadro_en(video, t, carpeta / f"{t:07.2f}_{escena}.png")
    w, h = tamano(video)
    print(f"{carpeta} (la toma mide {w}×{h}: las zonas van en esos píxeles y en segundos de la toma)")
    return carpeta


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("guion", type=Path)
    ap.add_argument("--toma", type=int, action="append")   # repetido: la regrabación de unas escenas va después
    ap.add_argument("--interno", action="store_true", help="sin difuminar las zonas de `tapar`")
    ap.add_argument("--sin-voz", action="store_true")
    ap.add_argument("--cuadros", action="store_true")
    ap.add_argument("--muestras", type=int)
    ap.add_argument("--cada", type=float)
    ap.add_argument("--video", type=Path)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cuadros:
        cuadros(args.guion, args.cada, args.video)
    elif args.muestras:
        muestras(args.guion, args.muestras, args.cada or 1.0)
    elif args.toma is not None:
        armar(args.guion, args.toma, interno=args.interno, con_voz=not args.sin_voz)
    else:
        guion = yaml.safe_load(args.guion.read_text(encoding="utf-8"))
        if all(e.get("superficie") in DE_CLIP for e in guion["escenas"]):
            armar(args.guion, [], interno=args.interno, con_voz=not args.sin_voz)
        else:
            ap.error("decí --toma <n>, --cuadros o --muestras <n>")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
