# Reglas del componente vigia

Código genérico, sin datos ni nombres de instalaciones, empresas, servidores o personas reales. Ejemplos y pruebas
exclusivamente inventados. Sin secretos en archivos, logs o commits. Solo biblioteca estándar.

El contrato precede a los cambios: `CONTRATO.md`. El componente no ejecuta por sí mismo ninguna escritura en un
servidor: el ejecutor llama acciones con nombre que define cada instancia. Sumar un tipo de acción o una acción a la
lista cerrada pide antes el OK del dueño escrito en el pedido, su vuelta atrás probada y su estreno con el dueño presente.

Lo que no se pudo medir es «desconocido»: ningún camino de error devuelve cero, vacío o «al día».
Las pruebas no usan red ni tocan un servidor. Todo en verde antes de entregar.
Esta carpeta vive en el repo común: cada cambio sigue proceso/desarrollo.md.
