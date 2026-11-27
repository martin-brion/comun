"""Toma de un video: corre el guion sobre su superficie, graba y deja las marcas para editar.

Uso:
  python tomar.py <instancia>/guion.yaml                 # toma completa → toma_<n>.mkv, .marcas.json y .guion.yaml
  python tomar.py <instancia>/guion.yaml --sin-grabar    # ensayo: corre los pasos sin grabar
  python tomar.py <instancia>/guion.yaml --desde <escena> --hasta <escena>
  python tomar.py <instancia>/guion.yaml --cuenta 5      # en el escritorio: 5 s para soltar el teclado antes de grabar

Formato del guion y acciones: CONTRATO.md del componente. Lo grabado va a la carpeta `binarios` del guion.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
SUPERFICIES = Path(os.environ.get("VIDEOS_SUPERFICIES", AQUI / "superficies"))
sys.path.insert(0, str(SUPERFICIES))
import entorno  # noqa: E402

# Lo que una toma puede grabar junto: cada grupo es un grabador. Las escenas «de clip» no se graban: las pone la edición.
GRUPOS = ({"android"}, {"web"}, {"escritorio", "terminal"})
DE_CLIP = {"animacion", "filmacion"}


def cargar_guion(ruta: Path) -> dict:
    guion = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    for clave in ("formato", "binarios", "escenas"):
        if clave not in guion:
            raise SystemExit(f"al guion le falta «{clave}» (CONTRATO.md)")
    ids = [e.get("id") for e in guion["escenas"]]
    if len(set(ids)) != len(ids) or None in ids:
        raise SystemExit("cada escena lleva un `id` distinto")
    return guion


def carpeta_binarios(guion: dict) -> Path:
    destino = Path(os.path.expandvars(guion["binarios"]))
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def grupo(guion: dict) -> set[str]:
    """Las superficies que graba esta toma (una de GRUPOS)."""
    sups = {e.get("superficie", "android") for e in guion["escenas"]} - DE_CLIP
    if not sups:
        return set()
    for g in GRUPOS:
        if sups <= g:
            return sups
    raise SystemExit(f"una toma graba un solo grupo de superficies: {[sorted(g) for g in GRUPOS]}; este guion mezcla "
                     f"{sorted(sups)} (partilo en dos guiones con los mismos binarios)")


def resolver_url(arg) -> str:
    """Una URL escrita, o {url_de: comando}: el comando la imprime y no queda anotada en ningún lado."""
    if isinstance(arg, dict) and "url_de" in arg:
        r = subprocess.run(arg["url_de"], shell=True, capture_output=True, text=True, timeout=60)
        url = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        if r.returncode != 0 or not url.startswith("http"):
            raise RuntimeError("el comando de url_de no devolvió una URL (no se muestra la salida)")
        return url
    return str(arg)


class SinGrabar:
    def __init__(self) -> None:
        self.t0 = time.monotonic()

    def __enter__(self) -> "SinGrabar":
        return self

    def __exit__(self, *exc) -> None:
        pass

    def ahora(self) -> float:
        return round(time.monotonic() - self.t0, 2)


class Marcas:
    """Los tramos de la toma: qué escena, qué acción, de qué tipo y entre qué segundos."""

    def __init__(self, reloj) -> None:
        self.reloj = reloj
        self.escena = ""
        self.accion = ""
        self.tramos: list[dict] = []
        self.escenas: list[dict] = []

    def ahora(self) -> float:
        return self.reloj.ahora()

    def tramo(self, tipo: str, t0: float, t1: float | None = None, **extra) -> None:
        """`extra`: datos para la edición (`letras` de un tramo de escribir, `hitos` de un trabajo, `leer`)."""
        t1 = self.ahora() if t1 is None else t1
        if t1 > t0:
            self.tramos.append({"escena": self.escena, "accion": self.accion, "tipo": tipo, "t0": t0, "t1": t1, **extra})


class Superficies:
    """Lo que está abierto en esta toma: `a` y `ex` (celular y Element X), `w` (navegador), `esc` y `term` (PC)."""
    a = ex = w = esc = term = None


def correr_paso(paso: dict, s: Superficies, m: Marcas, binarios: Path) -> None:
    if not isinstance(paso, dict) or len(paso) != 1:
        raise SystemExit(f"paso mal escrito (va «acción: argumento»): {paso!r}")
    accion, arg = next(iter(paso.items()))
    m.accion = accion
    t0 = m.ahora()
    a, ex, w = s.a, s.ex, s.w
    if accion == "pausa":
        time.sleep(float(arg))
        m.tramo("pausa", t0)
    elif accion in ("android.esperar_carga", "web.esperar_carga", "escritorio.esperar_carga"):
        time.sleep(float(arg))   # se espera de verdad; la edición la comprime
        m.tramo("carga", t0)
    elif accion == "captura":
        (w or s.esc or a).captura(binarios / f"{arg}.png")
    # --- web ---
    elif accion == "web.ir":
        if isinstance(arg, dict):   # una dirección reservada: si el navegador falla, su error la nombraría
            try:
                w.ir(resolver_url(arg))
            except Exception as e:
                raise RuntimeError(f"web.ir falló con una dirección reservada ({type(e).__name__}; no se muestra)") from None
        else:
            w.ir(str(arg))
        m.tramo("carga", t0)
    elif accion == "web.tocar":
        w.tocar(arg)
        time.sleep(1.2)
        m.tramo("accion", t0)
    elif accion == "web.escribir":
        w.escribir(str(arg))
        m.tramo("escribir", t0, letras=len(str(arg)))
    elif accion == "web.tecla":               # Tab, Enter, End, Control+A...
        w.p.keyboard.press(str(arg))
        time.sleep(0.4)
        m.tramo("accion", t0)
    elif accion == "web.desplazar":
        w.desplazar(str(arg))
        time.sleep(0.8)
        m.tramo("accion", t0)
    # --- escritorio ---
    elif accion == "escritorio.abrir":        # una dirección o un archivo, con su aplicación; se espera que cargue
        if isinstance(arg, dict):
            try:
                s.esc.abrir(resolver_url(arg))
            except Exception as e:
                raise RuntimeError(f"escritorio.abrir falló con una dirección reservada ({type(e).__name__})") from None
        else:
            s.esc.abrir(str(arg))
        time.sleep(3)
        m.tramo("carga", t0)
    elif accion == "escritorio.tecla":        # «ctrl+l», «enter», «f5»
        s.esc.tecla(str(arg))
        time.sleep(0.5)
        m.tramo("accion", t0)
    elif accion == "escritorio.escribir":
        s.esc.escribir(str(arg))
        m.tramo("escribir", t0, letras=len(str(arg)))
    elif accion == "escritorio.manual":       # lo hace la persona; avisa con la tecla (F9)
        arg = arg if isinstance(arg, dict) else {"aviso": str(arg)}
        tecla = str(arg.get("tecla", "F9"))
        print(f"  → {arg.get('aviso', 'tu turno')} (al terminar, {tecla})")
        s.esc.esperar_tecla(tecla, arg.get("max"))
        m.tramo("manual", t0)
    # --- terminal ---
    elif accion.startswith("terminal."):
        nombre = accion.split(".", 1)[1]
        if nombre not in ("prompt", "escribir", "enviar", "esperar", "tecla", "enfocar"):
            raise SystemExit(f"la terminal no tiene la acción «{accion}» (CONTRATO.md)")
        getattr(s.term, nombre)(arg, m)
    # --- android ---
    elif accion == "android.abrir_app":
        a.abrir_app(arg)
        time.sleep(2)
        m.tramo("accion", t0)
    elif accion == "android.abrir_url":
        if isinstance(arg, dict):
            a.abrir_url_reservada(resolver_url(arg))
        else:
            a.abrir_url(str(arg))
        time.sleep(3)
        m.tramo("accion", t0)
    elif accion == "android.tocar":
        if isinstance(arg, dict) and "x" in arg:
            a.tocar(int(arg["x"]), int(arg["y"]))
        else:
            a.tocar_nodo(a.esperar(texto=str(arg)))
        time.sleep(1.2)
        m.tramo("accion", t0)
    elif accion == "android.escribir":
        a.escribir(str(arg))
        m.tramo("escribir", t0, letras=len(str(arg)))
    elif accion == "android.deslizar":
        if isinstance(arg, dict):          # {x1, y1, x2, y2, ms}: desde un punto (p. ej. el margen, fuera de un dibujo)
            a.deslizar(int(arg["x1"]), int(arg["y1"]), int(arg["x2"]), int(arg["y2"]), ms=int(arg.get("ms", 700)))
        elif arg in ("izquierda", "derecha"):  # de costado, una columna (las de un tablero de tarjetas)
            a.deslizar(*((250, 1400, 800, 1400) if arg == "izquierda" else (800, 1400, 250, 1400)), ms=700)
        else:
            ex.desplazar(str(arg))
        time.sleep(0.8)
        m.tramo("accion", t0)
    elif accion == "android.tecla":           # TAB pasa al campo siguiente; ENTER, DEL...
        a.tecla("KEYCODE_" + str(arg).upper())
        time.sleep(0.4)
        m.tramo("accion", t0)
    elif accion == "android.atras":
        a.atras()
        time.sleep(1.2)
        m.tramo("accion", t0)
    elif accion.startswith("elementx."):
        metodo = getattr(ex, accion.split(".", 1)[1], None)
        if metodo is None or accion.split(".", 1)[1].startswith("_"):
            raise SystemExit(f"Element X no tiene la acción «{accion}» (CONTRATO.md)")
        metodo(arg, m)
    else:
        raise SystemExit(f"acción desconocida «{accion}» (CONTRATO.md)")


def siguiente_toma(binarios: Path) -> int:
    hechas = [int(p.stem.split("_")[1]) for p in binarios.glob("toma_*.mkv") if p.stem.split("_")[1].isdigit()]
    return max(hechas, default=0) + 1


def abrir(guion: dict, sups: set[str]) -> tuple[Superficies, object]:
    """Abre lo que la toma usa y devuelve el grabador de su grupo."""
    s = Superficies()
    if sups == {"web"}:
        import web
        s.w = web.Web()
        return s, s.w
    if sups <= {"escritorio", "terminal"}:
        import escritorio
        s.esc = escritorio.Escritorio(guion.get("escritorio"))
        if "terminal" in sups:
            import terminal
            s.term = terminal.Terminal(guion.get("terminal"), esc=s.esc)
        return s, s.esc
    import android
    import elementx
    android.arrancar()
    s.a = android.Android()
    s.ex = elementx.ElementX(s.a)
    return s, s.a


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("guion", type=Path)
    ap.add_argument("--desde")
    ap.add_argument("--hasta")
    ap.add_argument("--sin-grabar", action="store_true")
    ap.add_argument("--cuenta", type=float, default=0, help="segundos de cuenta regresiva antes de empezar")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    guion = cargar_guion(args.guion)
    entorno.aplicar(guion)
    sups = grupo(guion)
    if not sups:
        raise SystemExit("este guion no tiene escenas para grabar (solo animaciones o filmaciones): pasá a editar.py")
    binarios = carpeta_binarios(guion)
    escenas = [e for e in guion["escenas"] if e.get("superficie", "android") not in DE_CLIP]
    ids = [e["id"] for e in escenas]
    for nombre in (args.desde, args.hasta):
        if nombre and nombre not in ids:
            raise SystemExit(f"no hay una escena para grabar con id «{nombre}»")
    i0 = ids.index(args.desde) if args.desde else 0
    i1 = ids.index(args.hasta) + 1 if args.hasta else len(ids)

    s, superficie = abrir(guion, sups)
    if args.cuenta:
        for k in range(int(args.cuenta), 0, -1):
            print(f"empieza en {k}…", flush=True)
            time.sleep(1)
    n = siguiente_toma(binarios)
    destino = binarios / f"toma_{n}.mkv"
    grabador = SinGrabar() if args.sin_grabar else superficie.grabar(destino)
    inicio = datetime.now().isoformat(timespec="seconds")
    error = None
    with grabador as reloj:
        m = Marcas(reloj)
        for escena in escenas[i0:i1]:
            m.escena = escena["id"]
            t0 = m.ahora()
            print(f"· {escena['id']}", flush=True)
            try:
                for paso in escena.get("pasos", []):
                    correr_paso(paso, s, m, binarios)
            except Exception as e:  # la toma queda guardada hasta donde llegó, con el error anotado
                error = f"{escena['id']}: {e}"
                m.escenas.append({"id": escena["id"], "t0": t0, "t1": m.ahora()})
                break
            m.escenas.append({"id": escena["id"], "t0": t0, "t1": m.ahora()})
        time.sleep(0.5)
    if s.w:
        s.w.cerrar()
    if args.sin_grabar:
        print("ensayo terminado" + (f" con error en {error}" if error else ""))
        return 1 if error else 0
    marcas = {"guion": str(args.guion), "toma": n, "inicio": inicio, "error": error,
              "escenas": m.escenas, "tramos": m.tramos}
    (binarios / f"toma_{n}.marcas.json").write_text(json.dumps(marcas, ensure_ascii=False, indent=1), encoding="utf-8")
    # el guion tal como se corrió, para volver a editar esta toma aunque el de la instancia cambie
    (binarios / f"toma_{n}.guion.yaml").write_text(args.guion.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"toma {n}: {destino}" + (f" (cortada: {error})" if error else ""))
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
