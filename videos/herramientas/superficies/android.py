"""Superficie Android de los videos de uso: el emulador, el teclado, la pantalla y la grabación.

Uso:
  python android.py instalar          # crea el AVD e instala las APK de VIDEOS_APKS (idempotente)
  python android.py arrancar          # levanta el emulador sin ventana, en es-AR, con la barra de estado limpia
  python android.py parar
  python android.py captura <png>     # una foto de la pantalla
  python android.py pantalla          # los textos y botones visibles (para escribir un guion)

Rutas: las de la raíz de esta instalación (`entorno.py`): el SDK, los celulares (avd) y las APK (Element X,
ADBKeyboard.apk). El celular de los videos es VIDEOS_AVD («video») en VIDEOS_PUERTO (5554). Dos instalaciones en la
misma PC usan puertos distintos y la misma versión de adb: uno solo atiende a toda la PC y otra versión lo corta.
Lo que se instala en el emulador queda solo ahí.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno  # noqa: E402

SDK = entorno.ruta("ANDROID_SDK_ROOT", "sdk")
AVD_HOME = entorno.ruta("ANDROID_AVD_HOME", "avd")
APKS = entorno.ruta("VIDEOS_APKS", "descargas", "apk")
AVD = os.environ.get("VIDEOS_AVD", "video")   # otro (un clon), si el original quedó bloqueado (261102)
IMAGEN = "system-images;android-35;google_apis_playstore;x86_64"
ADB = SDK / "platform-tools" / "adb.exe"
EMULADOR = SDK / "emulator" / "emulator.exe"
# Otro puerto (VIDEOS_PUERTO) y banderas de más (VIDEOS_EMULADOR_EXTRA, p. ej. -read-only): para cuando un emulador
# cerrado a la fuerza queda retenido por Windows con sus puertos y su bloqueo (261102)
PUERTO = entorno.numero("VIDEOS_PUERTO", 5554)
SERIAL = f"emulator-{PUERTO}"
EXTRA = os.environ.get("VIDEOS_EMULADOR_EXTRA", "").split()
GRPC = entorno.numero("VIDEOS_GRPC_PUERTO", 8554)  # el control por gRPC (voz.py inyecta el audio del micrófono)
REGISTRO = AVD_HOME.parent / "emulador.log"   # la salida del emulador, para ver por qué se cayó
# Memoria física libre que tiene que haber antes de arrancar un emulador (261114: con tres abiertos y menos de 3 GB
# libres, los emuladores se arrastraban y Playwright se colgaba). VIDEOS_MEMORIA_MIN_GB lo cambia.
MEMORIA_MIN_GB = float(os.environ.get("VIDEOS_MEMORIA_MIN_GB", "4"))
TECLADO = "com.android.adbkeyboard/.AdbIME"
# Idioma por app (Android 13+): la imagen con Play Store no deja cambiar el del sistema por línea de comandos.
APPS_EN_ESPANOL = ("io.element.android.x", "com.android.chrome")


def _scrcpy() -> str:
    hallado = shutil.which("scrcpy")
    if hallado:
        return hallado
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for exe in sorted(winget.glob("Genymobile.scrcpy_*/scrcpy-*/scrcpy.exe")):
        return str(exe)
    raise FileNotFoundError("falta scrcpy: winget install Genymobile.scrcpy")
# El AVD: un celular de 1080×2400 (20:9, como los de hoy), con más memoria que la de fábrica.
AVD_CONFIG = {
    "hw.ramSize": "4096",
    "disk.dataPartition.size": "8G",
    "hw.keyboard": "yes",
    "hw.lcd.width": "1080",
    "hw.lcd.height": "2400",
    "hw.lcd.density": "420",
    "showDeviceFrame": "no",
}


def _entorno() -> dict:
    e = dict(os.environ)
    e["ANDROID_SDK_ROOT"] = str(SDK)
    e["ANDROID_HOME"] = str(SDK)
    e["ANDROID_AVD_HOME"] = str(AVD_HOME)
    e["ADB"] = str(ADB)  # scrcpy usa el mismo adb que el resto (dos versiones de adb se pelean el puerto)
    if "JAVA_HOME" not in e:
        jdks = sorted(Path(r"C:\Program Files\Microsoft").glob("jdk-*"))
        if jdks:
            e["JAVA_HOME"] = str(jdks[-1])
    return e


def adb(*args: str, check: bool = True, binario: bool = False, timeout: float = 60) -> str | bytes:
    r = subprocess.run([str(ADB), "-s", SERIAL, *args], capture_output=True, env=_entorno(), timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args[:3])}…: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout if binario else r.stdout.decode("utf-8", "replace")


def shell(cmd: str, check: bool = True, timeout: float = 60) -> str:
    return adb("shell", cmd, check=check, timeout=timeout)


def _reservado(cmd: str, valor: str) -> None:
    """Corre `cmd` en el celular con `$m` = `valor`, que entra por la entrada estándar (claves, enlaces firmados)."""
    r = subprocess.run([str(ADB), "-s", SERIAL, "shell", f"read -r m; {cmd}"], input=(valor + "\n").encode("utf-8"),
                       capture_output=True, env=_entorno(), timeout=30)
    if r.returncode != 0:
        raise RuntimeError("adb: falló un comando con un valor reservado (no se muestra)")


FIRMADO = re.compile(r"(/t/)[A-Za-z0-9._~%-]{8,}")   # los enlaces de los tableros (/t/<firma>)


@dataclass
class Nodo:
    texto: str
    desc: str
    rid: str
    clase: str
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def centro(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    def __str__(self) -> str:
        etiqueta = FIRMADO.sub(r"\1…", self.texto or self.desc or self.rid)  # un enlace firmado no se imprime
        return f"[{self.x1},{self.y1}-{self.x2},{self.y2}] {self.clase.rsplit('.', 1)[-1]}: {etiqueta}"


class Android:
    """El celular emulado, visto como lo usa un guion: tocar, escribir, leer la pantalla y grabar."""

    # --- pantalla -------------------------------------------------------------------------------------------------

    def pantalla(self, intentos: int = 4) -> list[Nodo]:
        """Los nodos visibles con texto, descripción o id (uiautomator). Reintenta: una animación lo hace fallar."""
        for i in range(intentos):
            salida = shell("uiautomator dump /sdcard/v.xml", check=False, timeout=30)
            if "dumped" in salida:
                xml = adb("exec-out", "cat", "/sdcard/v.xml", binario=True)
                try:
                    raiz = ET.fromstring(xml)
                except ET.ParseError:
                    continue
                nodos = []
                for n in raiz.iter("node"):
                    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
                    if not m:
                        continue
                    texto, desc, rid = n.get("text", ""), n.get("content-desc", ""), n.get("resource-id", "")
                    if texto or desc or rid:
                        nodos.append(Nodo(texto, desc, rid, n.get("class", ""), *map(int, m.groups())))
                return nodos
            time.sleep(0.6 * (i + 1))
        raise RuntimeError("uiautomator no pudo leer la pantalla")

    def buscar(self, texto: str | None = None, contiene: str | None = None, desc: str | None = None,
               rid: str | None = None, nodos: list[Nodo] | None = None) -> list[Nodo]:
        nodos = self.pantalla() if nodos is None else nodos
        res = []
        for n in nodos:
            if texto is not None and n.texto.strip() != texto and n.desc.strip() != texto:
                continue
            if contiene is not None and contiene.lower() not in (n.texto + " " + n.desc).lower():
                continue
            if desc is not None and n.desc.strip() != desc:
                continue
            if rid is not None and not n.rid.endswith(rid):
                continue
            res.append(n)
        return res

    def esperar(self, espera: float = 20, **criterio) -> Nodo:
        fin = time.monotonic() + espera
        while True:
            hallados = self.buscar(**criterio)
            if hallados:
                return hallados[0]
            if time.monotonic() > fin:
                raise TimeoutError(f"no apareció {criterio} en {espera:.0f} s")
            time.sleep(0.8)

    def captura(self, destino: Path) -> Path:
        destino.write_bytes(adb("exec-out", "screencap", "-p", binario=True))
        return destino

    # --- acciones -------------------------------------------------------------------------------------------------

    def tocar(self, x: int, y: int) -> None:
        shell(f"input tap {x} {y}")

    def tocar_nodo(self, nodo: Nodo) -> None:
        self.tocar(*nodo.centro)

    def deslizar(self, x1: int, y1: int, x2: int, y2: int, ms: int = 450) -> None:
        shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")

    def tecla(self, codigo: str) -> None:
        shell(f"input keyevent {codigo}")

    def atras(self) -> None:
        self.tecla("KEYCODE_BACK")

    def escribir(self, texto: str) -> None:
        """Escribe con el teclado ADB (acepta tildes y emojis) letra por letra, como una persona, en una sola sesión de
        adb. El ritmo real lo da el emulador; la edición lo lleva a `letras_s` parejo (`guion.md`, «Edición»)."""
        letras = [base64.b64encode(c.encode("utf-8")).decode("ascii") for c in texto]
        shell("for m in " + " ".join(letras) + "; do am broadcast -a ADB_INPUT_B64 --es msg $m >/dev/null; done",
              timeout=60 + 2 * len(letras))

    def escribir_reservado(self, texto: str) -> None:
        """Como `escribir`, pero el texto (una clave) entra por la entrada estándar de adb: no queda en la línea de
        comandos de la PC ni en un mensaje de error."""
        b64 = base64.b64encode(texto.encode("utf-8")).decode("ascii")
        _reservado('am broadcast -a ADB_INPUT_B64 --es msg "$m"', b64)

    def abrir_url_reservada(self, url: str) -> None:
        """Como `abrir_url`, para un enlace firmado: no queda en la línea de comandos de la PC ni en un error."""
        _reservado('am start -a android.intent.action.VIEW -d "$m"', url)

    def abrir_url(self, url: str) -> None:
        shell(f"am start -a android.intent.action.VIEW -d '{url}'")

    def abrir_app(self, paquete: str) -> None:
        shell(f"monkey -p {paquete} -c android.intent.category.LAUNCHER 1")

    def mostrar_toques(self, si: bool = True) -> None:
        shell(f"settings put system show_touches {1 if si else 0}")

    def barra_limpia(self, hora: str | None = None) -> None:
        """Modo demo de la barra de estado: batería llena, sin notificaciones y la hora fija (por defecto la de ahora,
        para que coincida con la de los mensajes)."""
        hora = hora or time.strftime("%H%M")
        shell("settings put global sysui_demo_allowed 1")
        demo = "am broadcast -a com.android.systemui.demo -e command"
        for c in ("enter", f"clock -e hhmm {hora}", "battery -e level 100 -e plugged false",
                  "network -e wifi show -e level 4 -e mobile show -e level 4 -e datatype none",
                  "notifications -e visible false"):
            shell(f"{demo} {c}")

    # --- grabación ------------------------------------------------------------------------------------------------

    def grabar(self, destino: Path) -> "Grabacion":
        return Grabacion(destino)


class Grabacion:
    """scrcpy graba la pantalla del emulador a un .mkv (aguanta un corte sin cerrar, a diferencia del .mp4)."""

    def __init__(self, destino: Path):
        self.destino = destino
        self.proc: subprocess.Popen | None = None
        self.t0: float | None = None
        self.log: list[str] = []

    def __enter__(self) -> "Grabacion":
        listo = threading.Event()
        cmd = [_scrcpy(), "-s", SERIAL, "--no-window", "--no-audio", "--no-control", "--max-fps=30",
               "--video-bit-rate=12M", f"--record={self.destino}", "--record-format=mkv"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_entorno(),
                                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

        def leer() -> None:
            assert self.proc and self.proc.stdout
            for linea in iter(self.proc.stdout.readline, b""):
                t = linea.decode("utf-8", "replace").rstrip()
                self.log.append(t)
                if "Recording started" in t or "Texture:" in t:
                    if self.t0 is None:
                        self.t0 = time.monotonic()
                    listo.set()

        threading.Thread(target=leer, daemon=True).start()
        if not listo.wait(20):
            self.__exit__(None, None, None)
            raise RuntimeError("scrcpy no empezó a grabar:\n" + "\n".join(self.log[-10:]))
        time.sleep(0.5)
        return self

    def ahora(self) -> float:
        """Segundos desde que empezó la grabación (para las marcas de la edición)."""
        assert self.t0 is not None
        return round(time.monotonic() - self.t0, 2)

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(getattr(__import__("signal"), "CTRL_BREAK_EVENT", 15))
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# --- emulador ---------------------------------------------------------------------------------------------------------

def _avd_existe() -> bool:
    return (AVD_HOME / f"{AVD}.ini").exists()


def instalar() -> None:
    AVD_HOME.mkdir(parents=True, exist_ok=True)
    if not _avd_existe():
        avdmanager = SDK / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat"
        r = subprocess.run([str(avdmanager), "create", "avd", "-n", AVD, "-k", IMAGEN, "-d", "pixel_7"],
                           input=b"no\n", capture_output=True, env=_entorno())
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace") + r.stdout.decode("utf-8", "replace"))
    cfg = AVD_HOME / f"{AVD}.avd" / "config.ini"
    lineas = [l for l in cfg.read_text(encoding="utf-8").splitlines() if l.split("=", 1)[0].strip() not in AVD_CONFIG]
    lineas += [f"{k}={v}" for k, v in AVD_CONFIG.items()]
    cfg.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"AVD {AVD} listo en {AVD_HOME}")
    if not _corriendo():
        arrancar()
    instalados = shell("pm list packages")
    for apk in sorted(APKS.glob("*.apk")):
        print(f"instalando {apk.name}")
        adb("install", "-r", str(apk), timeout=300)
    if "com.android.adbkeyboard" in shell("pm list packages") or "adbkeyboard" in instalados:
        shell(f"ime enable {TECLADO}")
        shell(f"ime set {TECLADO}")
    print("listo")


def _corriendo() -> bool:
    r = subprocess.run([str(ADB), "devices"], capture_output=True, env=_entorno())
    return f"{SERIAL}\tdevice" in r.stdout.decode()


def _proceso_del_avd() -> bool:
    """Si el emulador de ESTE AVD sigue vivo como proceso (aunque adb ya no lo vea, o todavía no)."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name like 'qemu-system%'\" | "
                        "ForEach-Object { $_.CommandLine }"], capture_output=True, text=True, errors="replace")
    return any(re.search(rf"-avd\s+{re.escape(AVD)}(\s|$)", linea) for linea in r.stdout.splitlines())


def memoria_libre_gb() -> float | None:
    """La memoria física libre de la PC, en GB (None si no se puede medir)."""
    try:
        import ctypes

        class Estado(ctypes.Structure):
            _fields_ = [("largo", ctypes.c_ulong), ("carga", ctypes.c_ulong)] + [
                (nombre, ctypes.c_ulonglong) for nombre in ("total", "libre", "pag_total", "pag_libre", "virt_total",
                                                             "virt_libre", "virt_ext")]

        e = Estado()
        e.largo = ctypes.sizeof(Estado)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e)):
            return None
        return e.libre / 2 ** 30
    except Exception:
        return None


def arrancar(espera: float = 240, audio: bool = False) -> None:
    """`audio`: con micrófono para la voz de voz.py (gRPC local con clave, que el emulador deja en su archivo de
    descubrimiento). EN PRUEBA (261031): por Escritorio remoto, con audio el emulador se cayó al abrir una
    sala de Element X; por eso, sin audio salvo que se pida. Sin -allow-host-audio, el micrófono de la PC no entra."""
    if _corriendo():
        print("ya corre")
    else:
        libre = memoria_libre_gb()
        if libre is not None and libre < MEMORIA_MIN_GB:
            raise RuntimeError(f"quedan {libre:.1f} GB de memoria libre y un emulador pide {MEMORIA_MIN_GB:g}: cerrá otro "
                               "emulador o lo que sobre (con poca memoria se arrastran o se caen)")
        if not _proceso_del_avd():
            # un emulador que se cerró a la fuerza deja sus bloqueos, y el siguiente se cae solo al rato de arrancar.
            # 261114: se mira el proceso de ESTE AVD, no si hay algún emulador: con otro celular abierto, los bloqueos
            # viejos no se limpiaban.
            avd = AVD_HOME / f"{AVD}.avd"
            (avd / "multiinstance.lock").unlink(missing_ok=True)
            shutil.rmtree(avd / "hardware-qemu.ini.lock", ignore_errors=True)
        sonido =["-grpc", str(GRPC), "-grpc-use-token"] if audio else ["-no-audio"]
        subprocess.Popen([str(EMULADOR), "-avd", AVD, "-no-window", "-no-boot-anim", *sonido,
                          "-gpu", "swiftshader_indirect", "-change-locale", "es-AR",
                          "-timezone", "America/Argentina/Buenos_Aires",
                          *(["-port", str(PUERTO)] if PUERTO != 5554 else []), *EXTRA],
                         stdout=REGISTRO.open("w"), stderr=subprocess.STDOUT, env=_entorno(),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        subprocess.run([str(ADB), "start-server"], capture_output=True, env=_entorno())
        fin = time.monotonic() + espera
        while time.monotonic() < fin:
            if _corriendo() and shell("getprop sys.boot_completed", check=False).strip() == "1":
                break
            time.sleep(3)
        else:
            raise TimeoutError("el emulador no terminó de arrancar")
    shell("service call alarm 3 s16 America/Argentina/Buenos_Aires", check=False)   # -timezone no siempre alcanza
    a = Android()
    a.barra_limpia()
    a.mostrar_toques(True)
    paquetes = shell("pm list packages")
    if "adbkeyboard" in paquetes:
        shell(f"ime set {TECLADO}", check=False)
    for p in APPS_EN_ESPANOL:
        if p in paquetes:
            shell(f"cmd locale set-app-locales {p} --locales es-AR", check=False)
    if not _corriendo():
        raise RuntimeError(f"el emulador {AVD} se cerró apenas arrancó: mirá {REGISTRO}")
    print("emulador listo")


def parar(espera: float = 120) -> None:
    """Apaga el emulador y espera a que termine de guardar. Uno cerrado de golpe (o matado desde el Administrador de
    tareas) puede dejar una app sin poder enviar (Element X, 261114) y sus bloqueos en el AVD."""
    adb("emu", "kill", check=False)
    fin = time.monotonic() + espera
    while time.monotonic() < fin:
        if not _corriendo() and not _proceso_del_avd():
            print(f"emulador {AVD} apagado")
            return
        time.sleep(2)
    print(f"el emulador {AVD} no terminó de apagarse en {espera:.0f} s: no lo mates a la fuerza; repetí `parar`",
          file=sys.stderr)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    orden = argv[0]
    if orden == "instalar":
        instalar()
    elif orden == "arrancar":
        arrancar(audio="--audio" in argv)
    elif orden == "parar":
        parar()
    elif orden == "captura":
        print(Android().captura(Path(argv[1])))
    elif orden == "pantalla":
        for n in Android().pantalla():
            print(n)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
