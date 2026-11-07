# Reglas del paquete gastos

Código genérico, sin datos ni nombres de instalaciones, empresas o personas reales.
Ejemplos y pruebas exclusivamente sintéticos. Sin secretos en archivos, logs o
commits. El código del plugin solo usa APIs públicas del motor; broker._ok y
broker._err son las excepciones documentadas de resultado. No registra efectos al
importarse: declara PLUGIN y usa activar dentro del cargador transaccional.

El contrato precede a los cambios. No modificar el motor desde este paquete:
sus APIs se desarrollan y prueban en su propio worktree. Las pruebas del plugin
usan git archive de una ref exacta del motor, entorno aislado y cargador real.
Todo en verde antes de entregar. Ningún despliegue ni push sin autorización.
Esta carpeta es fuente provisional, sin repositorio nuevo hasta decidir destino.
