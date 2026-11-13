# Reglas del componente vigia

Código genérico, sin datos ni nombres de instalaciones, empresas, servidores o personas reales. Ejemplos y pruebas
exclusivamente inventados. Sin secretos en archivos, logs o commits. Solo biblioteca estándar.

El contrato precede a los cambios: `CONTRATO.md`. Esta versión observa; nada en este componente ejecuta una escritura en
un servidor. Sumar una acción que aplique algo pide antes el OK del dueño escrito en el pedido, su vuelta atrás probada
y su primera corrida con el dueño presente.

Lo que no se pudo medir es «desconocido»: ningún camino de error devuelve cero, vacío o «al día».
Las pruebas no usan red ni tocan un servidor. Todo en verde antes de entregar.
Esta carpeta vive en el repo común: cada cambio sigue proceso/desarrollo.md.
