"""La voz de la persona ficticia: texto → audio sintético → micrófono del emulador.

La voz sale de `edge-tts` (voces neuronales de Microsoft, un servicio en línea: el texto del guion viaja a Microsoft) y
entra al micrófono del emulador por su gRPC (`injectAudio`), sin cable de audio ni micrófono en la PC. El emulador tiene
que haber arrancado con `android.py arrancar --audio` (audio y gRPC local con clave). EN PRUEBA (261031): la voz sale
bien, pero por Escritorio remoto el emulador con audio se cayó al abrir una sala de Element X. El cliente gRPC se genera
una vez, fuera del repo, en la raíz de la instalación (`entorno.py`):
  python -m grpc_tools.protoc -I<sdk>/emulator/lib --python_out=<raíz>/grpc --grpc_python_out=<raíz>/grpc emulator_controller.proto

Prueba: python voz.py decir "texto"    (lo dice en el micrófono; no toca ninguna app)
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import android  # noqa: E402
import entorno  # noqa: E402

GRPC_PY = entorno.ruta("VIDEOS_GRPC", "grpc")
VOZ = "es-AR-ElenaNeural"
TASA = 48000        # muestras por segundo, mono, 16 bits
PAQUETE_S = 0.02    # 20 ms por paquete: el emulador guarda ~300 ms


def sintetizar(texto: str, voz: str = VOZ) -> bytes:
    """El texto dicho por `voz`, como PCM crudo (mono, 16 bits, TASA)."""
    import edge_tts
    with tempfile.TemporaryDirectory() as d:
        mp3 = Path(d) / "voz.mp3"
        asyncio.run(edge_tts.Communicate(texto, voz).save(str(mp3)))
        r = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(TASA),
                            "-f", "s16le", "-"], capture_output=True, check=True)
    return r.stdout


def duracion(pcm: bytes) -> float:
    return len(pcm) / (2 * TASA)


def inyectar(pcm: bytes, silencio: float = 0.3) -> None:
    """Pasa el audio al micrófono del emulador. Bloquea hasta que Android lo consumió (lo pide un grabador activo)."""
    sys.path.insert(0, str(GRPC_PY))
    import grpc
    import emulator_controller_pb2 as pb
    import emulator_controller_pb2_grpc as pb_grpc

    formato = pb.AudioFormat(samplingRate=TASA, channels=pb.AudioFormat.Mono, format=pb.AudioFormat.AUD_FMT_S16)
    relleno = bytes(int(2 * TASA * silencio))
    datos = relleno + pcm + relleno
    paso = int(2 * TASA * PAQUETE_S)

    def paquetes():
        for i in range(0, len(datos), paso):
            yield pb.AudioPacket(format=formato, timestamp=int(time.time() * 1e6), audio=datos[i:i + paso])

    with grpc.insecure_channel(f"127.0.0.1:{android.GRPC}") as canal:  # local; la clave va en cada llamada
        pb_grpc.EmulatorControllerStub(canal).injectAudio(paquetes(), timeout=60 + duracion(datos) * 3,
                                                          metadata=[("authorization", f"Bearer {_clave()}")])


def _clave() -> str:
    """La clave del gRPC del emulador que corre en GRPC: está en su archivo de descubrimiento. No se imprime."""
    for ini in (Path(os.environ.get("TEMP", "")) / "avd" / "running").glob("pid_*.ini"):
        datos = dict(l.split("=", 1) for l in ini.read_text(encoding="utf-8").splitlines() if "=" in l)
        if datos.get("grpc.port") == str(android.GRPC) and datos.get("grpc.token"):
            return datos["grpc.token"]
    raise RuntimeError("no encuentro el emulador con gRPC: arrancalo con android.py arrancar")


def _ffmpeg() -> str:
    return entorno.ffmpeg()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "decir":
        pcm = sintetizar(sys.argv[2])
        print(f"voz de {duracion(pcm):.1f} s; inyectando…")
        inyectar(pcm)
        print("listo")
    else:
        print(__doc__)
        sys.exit(2)
