# Reglas del componente videos

Código genérico, sin datos ni nombres de instalaciones, empresas o personas reales. El ejemplo y las pruebas son
inventados. Python con `pyyaml`, `numpy` y `Pillow`; la superficie web suma `playwright` y `websocket-client`. ffmpeg
hace todo el video.

- **El contrato va primero:** `CONTRATO.md`. Una acción, una superficie o una regla de edición nueva se escribe ahí antes
  de programarla.
- **Ninguna ruta ni puerto de una instalación en el código.** Todo cuelga de `VIDEOS_RAIZ` (`herramientas/superficies/
  entorno.py`) o de una variable propia. Dos instalaciones en la misma PC no comparten raíz, puertos ni perfiles.
- **Lo reservado no se escribe:** claves, enlaces firmados e identificadores de sesión entran por la entrada estándar y no
  se imprimen.
- **Datos reales, solo con `datos_reales: true` en el guion:** la edición por defecto es la de difusión y frena si no hay
  zonas para difuminar. La versión sin difuminar se pide a propósito (`--interno`).
- **Los guiones, las tomas y los videos son de quien los corre.** Acá no entra ninguno real.
- **Las pruebas no usan red.** Las de armado necesitan ffmpeg y se saltean sin él; las superficies que manejan una PC, un
  emulador o un navegador se prueban a mano (README, «Cómo se comprueba»).

Esta carpeta vive en el repo común: cada cambio sigue `proceso/desarrollo.md`.
