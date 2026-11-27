"""Dónde vive cada cosa en esta PC: una raíz por instalación y lo que cuelga de ella.

Cada instalación declara su raíz en `VIDEOS_RAIZ` (o en `entorno:` del guion). De ella salen, si no se dicen aparte:

  <raíz>/sdk             ANDROID_SDK_ROOT     el Android SDK (adb, emulador, cmdline-tools)
  <raíz>/avd             ANDROID_AVD_HOME     los celulares emulados
  <raíz>/descargas/apk   VIDEOS_APKS          las APK que se instalan en el celular
  <raíz>/fuentes         VIDEOS_FUENTES       las tipografías de las marcas, como TTF sin espacios
  <raíz>/web/perfil      VIDEOS_WEB_PERFIL    el perfil de Chrome de la superficie web
  <raíz>/grpc            VIDEOS_GRPC          el cliente gRPC del emulador (voz.py)

Dos instalaciones en la misma PC no comparten raíz, ni puertos (VIDEOS_PUERTO, VIDEOS_GRPC_PUERTO, VIDEOS_WEB_CDP).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# Lo que un guion puede fijar en `entorno:` (lo demás del entorno de la PC no lo toca un guion).
CLAVES = ("ANDROID_SDK_ROOT", "ANDROID_AVD_HOME")


def raiz() -> Path:
    valor = os.environ.get("VIDEOS_RAIZ", "").strip()
    if not valor:
        raise SystemExit("falta VIDEOS_RAIZ: la carpeta propia de esta instalación para los videos (SDK, celulares, "
                         "perfil de Chrome, fuentes). Se dice en el entorno o en `entorno:` del guion")
    return Path(os.path.expandvars(valor))


def ruta(variable: str, *debajo: str) -> Path:
    """La ruta de `variable` si está en el entorno; si no, la que cuelga de la raíz."""
    valor = os.environ.get(variable, "").strip()
    return Path(os.path.expandvars(valor)) if valor else raiz().joinpath(*debajo)


def numero(variable: str, defecto: int) -> int:
    return int(os.environ.get(variable, "").strip() or defecto)


def aplicar(guion: dict) -> None:
    """Pasa al entorno lo que el guion fija en `entorno:` (solo VIDEOS_* y las rutas de Android). Va antes de importar
    una superficie: cada una lee sus rutas al importarse."""
    for clave, valor in (guion.get("entorno") or {}).items():
        if not (str(clave).startswith("VIDEOS_") or clave in CLAVES):
            raise SystemExit(f"entorno: «{clave}» no es una clave de los videos (VIDEOS_* o {', '.join(CLAVES)})")
        os.environ[str(clave)] = os.path.expandvars(str(valor))


def ffmpeg(nombre: str = "ffmpeg") -> str:
    """ffmpeg o ffprobe: del PATH o de la instalación de winget."""
    hallado = shutil.which(nombre)
    if hallado:
        return hallado
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for exe in sorted(winget.glob(f"Gyan.FFmpeg_*/ffmpeg-*/bin/{nombre}.exe")):
        return str(exe)
    raise FileNotFoundError(f"falta {nombre}: winget install Gyan.FFmpeg")
