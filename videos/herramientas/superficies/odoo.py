"""Odoo en los videos: poner o borrar la cookie de sesión de una base en el navegador de una superficie.

Uso:
  <comando que imprime el session_id> | python odoo.py entrar <url> --android     # en el Chrome del emulador
  <comando que imprime el session_id> | python odoo.py entrar <url> --web         # en el perfil de web.py
  <comando que imprime el session_id> | python odoo.py entrar <url> --cdp <P>     # en un Chrome con DevTools en P
  python odoo.py salir <url> --android | --web | --cdp <P>                        # borra esa cookie

El identificador de sesión entra por la entrada estándar: no queda en la línea de comandos, ni se imprime, ni se escribe
en un guion. Quién crea la sesión (con una clave, o una temporal que da el servidor de una base de demo) es de cada
instalación: este archivo solo la pone en el navegador. Para entrar a mano, una vez, alcanza con `web.py iniciar <url>`.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
VIDA = 7 * 86400


def _cookie_cdp(puerto: int, dominio: str, sid: str | None) -> None:
    """Por DevTools de un Chrome con alguna página abierta: el del emulador o uno de la PC."""
    import websocket
    paginas = json.load(urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json/list", timeout=10))
    pagina = next((p for p in paginas if p.get("type") == "page"), None)
    if pagina is None:
        raise SystemExit(f"el Chrome del puerto {puerto} no tiene ninguna página abierta: abrí una antes")
    ws = websocket.create_connection(pagina["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
    try:
        if sid:
            pedido = {"method": "Network.setCookie", "params": {
                "name": "session_id", "value": sid, "domain": dominio, "path": "/", "secure": True,
                "httpOnly": True, "sameSite": "Lax", "expires": time.time() + VIDA}}
        else:
            pedido = {"method": "Network.deleteCookies", "params": {"name": "session_id", "domain": dominio}}
        ws.send(json.dumps({"id": 1, **pedido}))
        while json.loads(ws.recv()).get("id") != 1:
            pass
    finally:
        ws.close()


def _cookie_android(dominio: str, sid: str | None) -> None:
    """Por DevTools del Chrome del emulador (adb forward al socket chrome_devtools_remote)."""
    sys.path.insert(0, str(AQUI))
    import android
    android.adb("forward", "tcp:9222", "localabstract:chrome_devtools_remote")
    _cookie_cdp(9222, dominio, sid)


def _cookie_web(dominio: str, sid: str | None) -> None:
    sys.path.insert(0, str(AQUI))
    import web
    w = web.Web()
    try:
        w.ctx.clear_cookies(name="session_id", domain=dominio)
        if sid:
            w.ctx.add_cookies([{"name": "session_id", "value": sid, "domain": dominio, "path": "/", "secure": True,
                                "httpOnly": True, "sameSite": "Lax", "expires": time.time() + VIDA}])
    finally:
        w.cerrar()


def cookie(superficie: str, dominio: str, sid: str | None) -> None:
    """`superficie`: android, web o cdp:<puerto>. Con `sid` None, la borra."""
    if superficie.startswith("cdp:"):
        _cookie_cdp(int(superficie[4:]), dominio, sid)
    elif superficie == "android":
        _cookie_android(dominio, sid)
    elif superficie == "web":
        _cookie_web(dominio, sid)
    else:
        raise SystemExit(f"superficie «{superficie}»: android, web o cdp:<puerto>")


def _superficie(argv: list[str]) -> str | None:
    if len(argv) == 1 and argv[0] in ("--android", "--web"):
        return argv[0][2:]
    if len(argv) == 2 and argv[0] == "--cdp" and argv[1].isdigit():
        return "cdp:" + argv[1]
    return None


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sup = _superficie(argv[2:]) if len(argv) >= 3 else None
    if argv[:1] not in (["entrar"], ["salir"]) or sup is None:
        print(__doc__)
        return 2
    dominio = urllib.parse.urlparse(argv[1]).hostname
    if not dominio:
        raise SystemExit("la url no tiene dominio")
    if argv[0] == "entrar":
        sid = sys.stdin.readline().strip()
        if not sid:
            raise SystemExit("el session_id tiene que llegar por la entrada estándar")
        cookie(sup, dominio, sid)
        print(f"sesión puesta en {sup} para {dominio}")
    else:
        cookie(sup, dominio, None)
        print(f"sesión borrada de {sup} para {dominio}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
