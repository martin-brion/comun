"""Superficie terminal: un agente de código (o una consola) en un panel de herdr, visto y grabado en el escritorio.

Uso:
  python terminal.py panel <pestaña> [<espacio>]   # qué panel resuelve esa pestaña de herdr (para escribir un guion)
  python terminal.py leer <pestaña> [<espacio>]    # lo que muestra ese panel ahora

El guion nombra el panel por la pestaña de herdr (`terminal: {pestana: "<nombre>", espacio: "<workspace>"}`) o por su
id (`{panel: "<id>"}`). Se resuelve en vivo al empezar la toma: los ids de herdr no se reusan.

El prompt se escribe letra por letra, como una persona, y se manda con Enter. Mientras el agente trabaja, la toma marca
un tramo `trabajo` (la edición lo acelera) y, adentro, un `hito` cada vez que aparece en pantalla un texto esperado
(una expresión regular del guion) o la persona aprieta la tecla de hito (F8): la edición congela ahí un instante para que
se lea el resultado de cada paso. Si el agente pide un permiso, se espera a que alguien lo conteste en la terminal.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FIN = ("idle", "done")          # el agente terminó su turno
TECLA_HITO = "F8"


def herdr(*args: str, timeout: float = 30) -> dict:
    r = subprocess.run(["herdr", *args], capture_output=True, timeout=timeout)
    salida = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        raise RuntimeError(f"herdr {' '.join(args[:2])}: " + (r.stderr.decode("utf-8", "replace").strip() or salida)[-300:])
    try:
        return json.loads(salida).get("result", {}) if salida.startswith("{") else {"texto": salida}
    except json.JSONDecodeError:
        return {"texto": salida}


def resolver(cfg: dict, correr=herdr) -> str:
    """El id del panel: `panel` tal cual, o el (único) panel de la pestaña `pestana` (en el espacio `espacio`)."""
    if cfg.get("panel"):
        return str(cfg["panel"])
    pestana = cfg.get("pestana")
    if not pestana:
        raise SystemExit("terminal: falta `pestana` (el nombre de la pestaña de herdr) o `panel`")
    espacios = correr("workspace", "list").get("workspaces", [])
    if cfg.get("espacio"):
        espacios = [e for e in espacios if e.get("label") == cfg["espacio"]]
    hallados = []
    for e in espacios:
        for t in correr("tab", "list", "--workspace", e["workspace_id"]).get("tabs", []):
            if t.get("label") == pestana:
                hallados.append((e["workspace_id"], t["tab_id"]))
    if len(hallados) != 1:
        raise SystemExit(f"terminal: {len(hallados)} pestañas se llaman «{pestana}»: tiene que ser una sola "
                         "(sumá `espacio:` o renombrá la pestaña)")
    espacio, tab = hallados[0]
    paneles = [p for p in correr("pane", "list", "--workspace", espacio).get("panes", []) if p.get("tab_id") == tab]
    if len(paneles) != 1:
        raise SystemExit(f"terminal: la pestaña «{pestana}» tiene {len(paneles)} paneles: tiene que tener uno")
    return paneles[0]["pane_id"]


class Terminal:
    """El panel como lo usa un guion. `esc`: la superficie escritorio (para la tecla de hito); puede faltar."""

    def __init__(self, cfg: dict | None = None, esc=None, correr=herdr) -> None:
        self.cfg = cfg or {}
        self.correr = correr
        self.panel = resolver(self.cfg, correr)
        self.esc = esc
        self._base: str | None = None   # lo que mostraba el panel antes de escribir: lo viejo no cuenta como resultado
        self._previa: str | None = None  # la pestaña que estaba al frente antes de la toma, para devolverla al final

    def estado(self) -> str:
        return str(self.correr("pane", "get", self.panel).get("pane", {}).get("agent_status", "unknown"))

    def leer(self, lineas: int = 400) -> str:
        r = self.correr("pane", "read", self.panel, "--source", "recent-unwrapped", "--lines", str(lineas))
        return str(r.get("texto") or r.get("text") or r.get("read", {}).get("text", "") or "")

    # --- acciones del guion (argumento, marcas) -------------------------------------------------------------------

    def enfocar(self, arg=None, m=None) -> None:
        """Trae la pestaña al frente en herdr. Con `{esc: true}` (o `esc: true` en `terminal:` del guion), manda además
        un Esc a la ventana del frente, que tiene que ser la de herdr: cierra un menú del cliente abierto (el de
        sesiones, en ancho de celular), que si no queda tapando el panel y se graba con los nombres de otras sesiones."""
        t0 = m.ahora() if m else 0
        if self._previa is None:
            activo = [w for w in self.correr("workspace", "list").get("workspaces", []) if w.get("focused")]
            self._previa = activo[0].get("active_tab_id", "") if activo else ""
        tab = self.correr("pane", "get", self.panel).get("pane", {}).get("tab_id")
        if tab:
            self.correr("tab", "focus", tab)
        opciones = arg if isinstance(arg, dict) else {}
        if opciones.get("esc", self.cfg.get("esc", False)):
            if self.esc is None:
                raise SystemExit("terminal.enfocar con esc necesita la superficie escritorio en la misma toma")
            time.sleep(0.3)
            self.esc.tecla("esc")
        time.sleep(0.8)
        if m:
            m.tramo("accion", t0)

    def restaurar(self) -> None:
        """Al terminar la toma: vuelve al frente la pestaña que estaba antes del primer `enfocar`."""
        if self._previa:
            try:
                self.correr("tab", "focus", self._previa)
            except RuntimeError:
                pass   # la pestaña ya no existe

    def escribir(self, texto, m=None) -> None:
        """De a pocas letras, para que se vea tipear (la edición lo lleva a `letras_s` parejo)."""
        texto = str(texto)
        if self._base is None:
            self._base = self.leer()
        t0 = m.ahora() if m else 0
        for i in range(0, len(texto), 3):
            self.correr("pane", "send-text", self.panel, texto[i:i + 3])
            time.sleep(0.02)
        if m:
            m.tramo("escribir", t0, letras=len(texto))

    def enviar(self, _arg=None, m=None) -> None:
        t0 = m.ahora() if m else 0
        self.correr("pane", "send-keys", self.panel, "enter")
        if m:
            m.tramo("accion", t0)

    def tecla(self, nombre, m=None) -> None:
        t0 = m.ahora() if m else 0
        self.correr("pane", "send-keys", self.panel, *str(nombre).split())
        time.sleep(0.4)
        if m:
            m.tramo("accion", t0)

    def prompt(self, arg, m) -> None:
        """`arg`: el texto, o {texto, hitos, max, leer, comprimir_a}. Escribe, manda y espera a que el agente termine."""
        arg = arg if isinstance(arg, dict) else {"texto": arg}
        if self.estado() == "blocked":
            raise RuntimeError("el agente está esperando una respuesta: contestala antes de seguir")
        self._base = self.leer()   # lo que ya estaba: un hito o un `hasta` cuenta solo si aparece de nuevo
        self.escribir(arg["texto"], m)
        self.enviar(None, m)
        self.esperar({**arg, "_recien_mandado": True}, m)

    def esperar(self, arg, m) -> None:
        """Espera a que el agente termine (o a que aparezca `hasta`, una expresión regular), marcando los hitos."""
        arg = arg if isinstance(arg, dict) else {}
        maximo = float(arg.get("max", 1800))
        hitos = [re.compile(h) for h in arg.get("hitos", [])]
        hasta = re.compile(arg["hasta"]) if arg.get("hasta") else None
        t0 = m.ahora()
        fin = time.monotonic() + maximo
        visto_trabajando = not arg.get("_recien_mandado")
        arranque = time.monotonic()
        marcados: list[dict] = []
        pendientes = list(hitos)
        ultima_lectura = 0.0
        base = self._base if self._base is not None else self.leer()
        self._base = None

        def nuevo(r: re.Pattern, texto: str) -> bool:
            """Si `r` aparece más veces que antes de empezar (lo viejo del historial no cuenta)."""
            return len(r.findall(texto)) > len(r.findall(base))
        while True:
            if time.monotonic() > fin:
                raise TimeoutError(f"el agente no terminó en {maximo:.0f} s")
            if self.esc is not None and self.esc.apretada(TECLA_HITO):
                marcados.append({"t": m.ahora(), "que": TECLA_HITO})
                while self.esc.apretada(TECLA_HITO):
                    time.sleep(0.05)
            ahora = time.monotonic()
            texto = None
            if (pendientes or hasta) and ahora - ultima_lectura >= 1.0:
                ultima_lectura = ahora
                texto = self.leer()
                for h in list(pendientes):
                    if nuevo(h, texto):
                        marcados.append({"t": m.ahora(), "que": h.pattern})
                        pendientes.remove(h)
            estado = self.estado()
            if hasta and texto is not None and estado != "blocked" and nuevo(hasta, texto):
                break
            if estado in ("working", "blocked"):
                visto_trabajando = True
            elif estado in FIN and not hasta:
                # recién mandado, el agente puede tardar en pasar a «working»: hasta 8 s se lo espera
                if visto_trabajando or ahora - arranque > 8:
                    break
            time.sleep(0.3)
        t1 = m.ahora()
        extra = {"hitos": marcados}
        if arg.get("comprimir_a") is not None:
            extra["comprimir_a"] = float(arg["comprimir_a"])
        m.tramo("trabajo", t0, t1, **extra)
        leer = float(arg.get("leer", 2.0))
        time.sleep(max(0.6, leer))   # la pantalla quieta con el resultado: lo que dura de verdad es lo que se puede mostrar
        m.tramo("lectura", t1, None, leer=leer)
        if pendientes:
            print(f"  (no aparecieron: {', '.join(h.pattern for h in pendientes)})")


def main(argv: list[str]) -> int:
    if len(argv) in (2, 3) and argv[0] in ("panel", "leer"):
        cfg = {"pestana": argv[1], **({"espacio": argv[2]} if len(argv) == 3 else {})}
        t = Terminal(cfg)
        print(t.panel if argv[0] == "panel" else t.leer())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
