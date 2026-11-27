"""Superficie web de los videos de uso: Chrome de la PC (sin ventana, con Playwright), la grabación y un cursor visible.

Uso:
  python web.py abrir <url>          # abre el perfil de los videos en esa página y saca una captura (para escribir un guion)
  python web.py donde <url> "<texto>"   # dónde está un texto en la página (para escribir un guion)
  python web.py iniciar <url>        # el mismo perfil CON ventana: una persona inicia sesión a mano, una vez, y cierra

El perfil de Chrome de los videos es el de la raíz de esta instalación (`entorno.py`, VIDEOS_WEB_PERFIL): las sesiones
quedan ahí, como en el AVD del emulador. La pantalla es de 1440×810 (a escala 4/3 para las capturas; la
grabación de Chrome sin ventana sale igual en 1440×810). La grabación usa DevTools (Page.startScreencast) en un hilo
propio: Chrome manda un cuadro cuando la página cambia, con su hora, y al cerrar se arma un .mkv de cuadros variables,
como el de scrcpy.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import websocket

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno  # noqa: E402

PERFIL = entorno.ruta("VIDEOS_WEB_PERFIL", "web", "perfil")
PUERTO = entorno.numero("VIDEOS_WEB_CDP", 9333)   # DevTools de este Chrome: la grabación se conecta por acá
CLIC = os.environ.get("VIDEOS_CLIC_RGB", "37,99,235")   # el color del anillo que marca un clic (R,G,B)
PANTALLA = {"width": 1440, "height": 810}
ESCALA = 4 / 3             # capturas de 1920×1080

# Un cursor dibujado en la página: Chrome sin ventana no tiene puntero y los clics se verían como magia.
CURSOR_JS = r"""
(() => {
  if (window.__cursorVideo) return;
  window.__cursorVideo = true;
  const poner = () => {
    if (document.getElementById('cursor-video')) return;
    const c = document.createElement('div');
    c.id = 'cursor-video';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.5 15 L11.5 21.5 L14 20.3 L11 14 L17 14 Z" fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    c.style.cssText = 'position:fixed;left:720px;top:420px;width:26px;height:26px;z-index:2147483647;' +
      'pointer-events:none;transition:left .45s ease-in-out,top .45s ease-in-out;filter:drop-shadow(0 1px 2px rgba(0,0,0,.4))';
    document.documentElement.appendChild(c);
    const x = sessionStorage.getItem('cursorX'), y = sessionStorage.getItem('cursorY');
    if (x) { c.style.transition = 'none'; c.style.left = x; c.style.top = y; requestAnimationFrame(() => c.style.transition = ''); }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', poner); else poner();
  window.__moverCursor = (x, y) => {
    poner();
    const c = document.getElementById('cursor-video');
    c.style.left = x + 'px'; c.style.top = y + 'px';
    sessionStorage.setItem('cursorX', c.style.left); sessionStorage.setItem('cursorY', c.style.top);
  };
  window.__clicCursor = () => {
    const c = document.getElementById('cursor-video'); if (!c) return;
    const r = document.createElement('div');
    r.style.cssText = 'position:fixed;width:34px;height:34px;border-radius:50%;border:3px solid rgba(__CLIC__,.9);' +
      'left:' + (parseFloat(c.style.left) - 16) + 'px;top:' + (parseFloat(c.style.top) - 16) + 'px;z-index:2147483646;' +
      'pointer-events:none;transition:transform .35s ease-out,opacity .35s ease-out;transform:scale(.4);opacity:1';
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.3)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 450);
  };
})();
"""


def _ffmpeg() -> str:
    hallado = shutil.which("ffmpeg")
    if hallado:
        return hallado
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for exe in sorted(winget.glob("Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe")):
        return str(exe)
    raise FileNotFoundError("falta ffmpeg: winget install Gyan.FFmpeg")


class Web:
    """El navegador como lo usa un guion: ir, tocar, escribir, desplazar y grabar."""

    def __init__(self, con_ventana: bool = False) -> None:
        from playwright.sync_api import sync_playwright
        PERFIL.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(PERFIL), channel="chrome", headless=not con_ventana, viewport=PANTALLA, device_scale_factor=ESCALA,
            locale="es-AR", timezone_id="America/Argentina/Buenos_Aires", color_scheme="light",
            args=[f"--remote-debugging-port={PUERTO}", "--hide-scrollbars"])
        self.ctx.add_init_script(CURSOR_JS.replace("__CLIC__", CLIC))
        self.p = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        for otra in self.ctx.pages[1:]:
            otra.close()

    def cerrar(self) -> None:
        self.ctx.close()
        self._pw.stop()

    # --- acciones -------------------------------------------------------------------------------------------------

    def ir(self, url: str) -> None:
        self.p.goto(url, wait_until="load")

    def _ubicar(self, que):
        """Un texto visible, o {selector, texto} (el elemento del selector que contiene ese texto)."""
        if isinstance(que, dict):
            loc = self.p.locator(que["selector"])
            if que.get("texto"):
                loc = loc.filter(has_text=que["texto"])
            return loc.nth(int(que.get("n", 0)))
        exacto = self.p.get_by_text(str(que), exact=True)
        return exacto.first if exacto.count() else self.p.get_by_text(str(que)).first

    def tocar(self, que) -> None:
        loc = self._ubicar(que)
        loc.wait_for(state="visible", timeout=20000)
        loc.scroll_into_view_if_needed()
        caja = loc.bounding_box()
        x, y = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
        self.p.evaluate("([x, y]) => window.__moverCursor && window.__moverCursor(x, y)", [x, y])
        time.sleep(0.6)
        self.p.evaluate("() => window.__clicCursor && window.__clicCursor()")
        self.p.mouse.click(x, y)

    def escribir(self, texto: str) -> None:
        """Letra por letra en el campo que tiene el foco (la edición lleva el ritmo a `letras_s`)."""
        self.p.keyboard.type(texto, delay=35)

    def desplazar(self, sentido: str) -> None:
        self.p.mouse.wheel(0, 420 if sentido == "abajo" else -420)

    def captura(self, destino: Path) -> Path:
        self.p.screenshot(path=str(destino))
        return destino

    def grabar(self, destino: Path) -> "Grabacion":
        return Grabacion(destino)


class Grabacion:
    """Los cuadros de DevTools a un .mkv: cada cuadro dura hasta el siguiente (la página manda solo cuando cambia)."""

    def __init__(self, destino: Path) -> None:
        self.destino = destino
        self.cuadros = destino.with_name(destino.stem + "_cuadros")
        self.lista: list[tuple[float, Path]] = []
        self.t0: float | None = None
        self._fin = threading.Event()
        self._listo = threading.Event()
        self._error: str | None = None

    def __enter__(self) -> "Grabacion":
        if self.cuadros.exists():
            shutil.rmtree(self.cuadros)
        self.cuadros.mkdir(parents=True)
        threading.Thread(target=self._recibir, daemon=True).start()
        if not self._listo.wait(20):
            raise RuntimeError("DevTools no mandó cuadros: " + (self._error or "sin respuesta"))
        time.sleep(0.5)
        return self

    def _recibir(self) -> None:
        try:
            paginas = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PUERTO}/json/list", timeout=10))
            pagina = next(p for p in paginas if p.get("type") == "page")
            ws = websocket.create_connection(pagina["webSocketDebuggerUrl"], timeout=2, suppress_origin=True)
            ws.send(json.dumps({"id": 1, "method": "Page.startScreencast", "params": {
                "format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1}}))
            n = 0
            while not self._fin.is_set():
                try:
                    msj = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                if msj.get("method") != "Page.screencastFrame":
                    continue
                p = msj["params"]
                ws.send(json.dumps({"id": 100 + n, "method": "Page.screencastFrameAck",
                                    "params": {"sessionId": p["sessionId"]}}))
                t = p["metadata"].get("timestamp") or time.time()
                archivo = self.cuadros / f"{n:06d}.jpg"
                archivo.write_bytes(base64.b64decode(p["data"]))
                self.lista.append((t, archivo))
                if self.t0 is None:
                    self.t0 = t
                    self._listo.set()
                n += 1
            ws.send(json.dumps({"id": 2, "method": "Page.stopScreencast"}))
            ws.close()
        except Exception as e:  # el error se ve en __enter__ o al armar
            self._error = f"{type(e).__name__}: {e}"
            self._listo.set()

    def ahora(self) -> float:
        """Segundos desde el primer cuadro (la misma hora que la de los cuadros: la de la PC)."""
        assert self.t0 is not None
        return round(time.time() - self.t0, 2)

    def __exit__(self, *exc) -> None:
        fin = time.time()
        self._fin.set()
        time.sleep(2.5)
        if not self.lista:
            raise RuntimeError("no se grabó ningún cuadro: " + (self._error or ""))
        # concat de ffmpeg: cada cuadro con su duración; el último, hasta el final de la toma
        lineas = []
        for (t, archivo), siguiente in zip(self.lista, [x[0] for x in self.lista[1:]] + [fin]):
            lineas += [f"file '{archivo.as_posix()}'", f"duration {max(siguiente - t, 0.001):.4f}"]
        lineas.append(f"file '{self.lista[-1][1].as_posix()}'")
        concat = self.cuadros / "cuadros.txt"
        concat.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        r = subprocess.run([_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-fps_mode", "vfr",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
                            str(self.destino)], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-800:])
        shutil.rmtree(self.cuadros, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("abrir", "donde", "iniciar"):
        print(__doc__)
        return 2
    if argv[0] == "iniciar":   # la sesión la inicia una persona; la herramienta no ve ni guarda la clave
        w = Web(con_ventana=True)
        try:
            w.ir(argv[1])
            print("iniciá sesión en la ventana y cerrala cuando termines")
            w.p.wait_for_event("close", timeout=0)
        finally:
            w.cerrar()
        return 0
    w = Web()
    try:
        w.ir(argv[1])
        time.sleep(4)
        if argv[0] == "abrir":
            destino = Path(os.environ.get("TEMP", ".")) / "web_abrir.png"
            print(w.captura(destino))
        else:
            loc = w._ubicar(argv[2])
            print(loc.bounding_box())
    finally:
        w.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
