"""Superficie escritorio: la pantalla de la PC tal cual se ve (un monitor o una región), grabada con ffmpeg (gdigrab).

Uso:
  python escritorio.py monitores           # los monitores, con su número y su tamaño en píxeles
  python escritorio.py captura <png>       # una foto de lo que se grabaría (monitor 1)

Graba lo que se ve: la terminal de un agente, el navegador de la persona, Outlook, cualquier ventana. Por eso, antes de
una toma: «No molestar» de Windows prendido, ninguna ventana con datos ajenos a la vista y el monitor elegido en el
guion (`escritorio: {monitor: 1}` o `{region: {x, y, w, h}}`, en píxeles reales). La grabación entera queda como toma:
si un paso falla, lo grabado hasta ahí sirve de respaldo.

Las acciones manejan la PC como una persona: abrir una dirección o un archivo, teclas, escribir. Lo que no conviene
automatizar lo hace la persona y avisa con una tecla (F9 por defecto): `escritorio.manual`.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno  # noqa: E402

FPS = 30

# Píxeles reales, no los escalados de Windows: sin esto, en una pantalla al 150 % las medidas no coinciden con gdigrab.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

# Teclas virtuales de Windows (las que un guion puede nombrar).
VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "mayus": 0x10, "win": 0x5B,
      "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "espacio": 0x20, "space": 0x20, "borrar": 0x08, "backspace": 0x08,
      "supr": 0x2E, "delete": 0x2E, "inicio": 0x24, "home": 0x24, "fin": 0x23, "end": 0x23,
      "repag": 0x21, "pageup": 0x21, "avpag": 0x22, "pagedown": 0x22,
      "izquierda": 0x25, "left": 0x25, "arriba": 0x26, "up": 0x26, "derecha": 0x27, "right": 0x27,
      "abajo": 0x28, "down": 0x28}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})


def combinacion(texto: str) -> list[int]:
    """«ctrl+l» → [0x11, 0x4C]. Una tecla desconocida frena antes de tocar nada."""
    codigos = []
    for parte in str(texto).lower().replace(" ", "").split("+"):
        if parte not in VK:
            raise SystemExit(f"tecla desconocida «{parte}» (ver escritorio.py, VK)")
        codigos.append(VK[parte])
    return codigos


def monitores() -> list[dict]:
    """Los monitores, en el orden de Windows (el principal primero), con su rectángulo en píxeles reales."""
    lista: list[dict] = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT),
                              ctypes.c_double)

    def uno(_h, _dc, r, _d):
        rc = r.contents
        lista.append({"x": rc.left, "y": rc.top, "w": rc.right - rc.left, "h": rc.bottom - rc.top})
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(uno), 0)
    lista.sort(key=lambda m: (m["x"] != 0 or m["y"] != 0, m["y"], m["x"]))   # el principal arranca en 0,0
    return lista


def region(cfg: dict | None) -> dict:
    """Lo que se graba: `region` explícita o un `monitor` (1 = el principal). Ancho y alto pares (lo pide H.264)."""
    cfg = cfg or {}
    if cfg.get("region"):
        r = {k: int(cfg["region"][k]) for k in ("x", "y", "w", "h")}
    else:
        lista = monitores()
        n = int(cfg.get("monitor", 1))
        if not 1 <= n <= len(lista):
            raise SystemExit(f"monitor {n}: hay {len(lista)} (python escritorio.py monitores)")
        r = dict(lista[n - 1])
    r["w"], r["h"] = r["w"] // 2 * 2, r["h"] // 2 * 2
    return r


def _gdigrab(r: dict, mouse: bool = True) -> list[str]:
    return ["-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1" if mouse else "0",
            "-offset_x", str(r["x"]), "-offset_y", str(r["y"]), "-video_size", f"{r['w']}x{r['h']}", "-i", "desktop"]


class Escritorio:
    """La PC como la usa un guion: abrir, teclas, escribir, esperar a la persona y grabar."""

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self.r = region(self.cfg)

    # --- acciones -------------------------------------------------------------------------------------------------

    def abrir(self, que: str) -> None:
        """Una dirección (en el navegador predeterminado) o un archivo (con su aplicación)."""
        que = os.path.expandvars(str(que))
        if not que.startswith(("http://", "https://")) and not Path(que).exists():
            raise RuntimeError(f"no existe {que}")
        os.startfile(que)   # noqa: S606 (lo pide el guion: es lo que se muestra)

    def tecla(self, texto: str) -> None:
        codigos = combinacion(texto)
        u = ctypes.windll.user32
        for c in codigos:
            u.keybd_event(c, 0, 0, 0)
            time.sleep(0.03)
        for c in reversed(codigos):
            u.keybd_event(c, 0, 2, 0)   # KEYEVENTF_KEYUP
            time.sleep(0.03)

    def escribir(self, texto: str, pausa: float = 0.04) -> None:
        """Letra por letra, como Unicode (tildes y ñ incluidas), en la ventana que tiene el foco."""
        u = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("relleno", ctypes.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        for letra in str(texto):
            datos = letra.encode("utf-16-le")   # una o dos unidades UTF-16
            for k in range(0, len(datos), 2):
                codigo = int.from_bytes(datos[k:k + 2], "little")
                for flags in (0x0004, 0x0004 | 0x0002):   # KEYEVENTF_UNICODE, y al soltar KEYUP
                    i = INPUT(type=1)
                    i.ki = KEYBDINPUT(0, codigo, flags, 0, 0)
                    u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
            time.sleep(pausa)

    @staticmethod
    def apretada(tecla: str) -> bool:
        """Si la tecla está apretada ahora (vale con cualquier ventana al frente)."""
        return bool(ctypes.windll.user32.GetAsyncKeyState(combinacion(tecla)[-1]) & 0x8000)

    def esperar_tecla(self, tecla: str = "F9", maximo: float | None = None) -> None:
        """Hasta que la persona aprieta `tecla` (y la suelta)."""
        fin = None if maximo is None else time.monotonic() + maximo
        while not self.apretada(tecla):
            if fin is not None and time.monotonic() > fin:
                raise TimeoutError(f"nadie apretó {tecla} en {maximo:.0f} s")
            time.sleep(0.05)
        while self.apretada(tecla):
            time.sleep(0.05)

    def captura(self, destino: Path) -> Path:
        r = subprocess.run([entorno.ffmpeg(), "-y", "-loglevel", "error", *_gdigrab(self.r), "-frames:v", "1",
                            str(destino)], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-500:])
        return destino

    def grabar(self, destino: Path) -> "Grabacion":
        return Grabacion(destino, self.r, bool(self.cfg.get("mouse", True)))


class Grabacion:
    """ffmpeg graba la región a un .mkv (aguanta un corte sin cerrar). La hora cero es el primer cuadro."""

    def __init__(self, destino: Path, r: dict, mouse: bool = True) -> None:
        self.destino, self.r, self.mouse = destino, r, mouse
        self.proc: subprocess.Popen | None = None
        self.t0: float | None = None
        self.log: list[str] = []

    def __enter__(self) -> "Grabacion":
        listo = threading.Event()
        cmd = [entorno.ffmpeg(), "-y", "-hide_banner", "-thread_queue_size", "512", *_gdigrab(self.r, self.mouse),
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(self.destino)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def leer() -> None:
            assert self.proc and self.proc.stderr
            resto = ""
            while True:
                bloque = self.proc.stderr.read1(512) if hasattr(self.proc.stderr, "read1") else self.proc.stderr.read(64)
                if not bloque:
                    break
                resto += bloque.decode("utf-8", "replace")
                if self.t0 is None and "frame=" in resto:
                    self.t0 = time.monotonic()
                    listo.set()
                *lineas, resto = resto.replace("\r", "\n").split("\n")
                self.log += [l for l in lineas if l.strip()][-20:]
                self.log = self.log[-40:]

        threading.Thread(target=leer, daemon=True).start()
        if not listo.wait(20):
            self.__exit__(None, None, None)
            raise RuntimeError("ffmpeg no empezó a grabar la pantalla:\n" + "\n".join(self.log[-10:]))
        return self

    def ahora(self) -> float:
        assert self.t0 is not None
        return round(time.monotonic() - self.t0, 2)

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
                self.proc.wait(20)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()


def main(argv: list[str]) -> int:
    if argv[:1] == ["monitores"]:
        for i, m in enumerate(monitores(), 1):
            print(f"{i}: {m['w']}×{m['h']} en ({m['x']}, {m['y']})")
        return 0
    if argv[:1] == ["captura"] and len(argv) == 2:
        print(Escritorio().captura(Path(argv[1])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
