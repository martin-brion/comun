# vigia · vigía de parches

Mira qué corre en cada servidor, lo compara con lo publicado, deja registro y, en su ventana, aplica lo que pasa todas las
compuertas. Nace de la especificación del vigía que ya corría de un lado; se escribe de cero para que los dos lados usen
la misma pieza.

Solo biblioteca estándar de Python (3.9 en adelante). No es un plugin del bot: es un servicio del servidor.

## Qué hace

**Observar** (`python -m vigia correr`):

1. **Foto** de cada servidor (`vigia/foto.py`): distribución, kernel, reinicio pedido, paquetes pendientes y su origen,
   contenedores en marcha y sus imágenes. Un solo archivo que corre sin instalar nada: `python3 - < foto.py`.
2. **Fuentes**: el índice público de Docker Hub, cualquier registro OCI aprobado en la configuración y la lista de
   vulnerabilidades explotadas (KEV). Solo https, sin credenciales. Una imagen propia nunca se nombra hacia afuera.
3. **Análisis**: para cada imagen, la versión que corre (aunque esté detrás de una etiqueta flotante), el paso más chico
   disponible, su tamaño (parche, menor, mayor) y si pasaría la compuerta de versión. Para una imagen propia con base
   declarada, si la base se renovó después de armarla.
4. **Estado**: cada candidata guarda cuándo se detectó; el plazo se cuenta desde ahí y sobrevive a los reinicios.
5. **Registro**: una ficha Markdown por corrida, con índice, y un resumen corto para una persona.

**Aplicar** (`python -m vigia aplicar`): una pasada del ejecutor.

1. Si existe `<estado_dir>/PAUSA`, no hace nada.
2. Resuelve lo que un corte dejó a medias, midiendo. No repite ninguna acción.
3. Fuera de la ventana no sigue (salvo `--presente`). Un servidor por ventana.
4. Observa de nuevo y, para cada candidata, pasa las compuertas en orden: versión, tipo de acción habilitado, acciones
   definidas, estreno, imagen fija por huella, vuelta atrás exacta, servicios, sin escalar y respaldo vigente.
5. Para cada servicio que usa la imagen: deja la intención en el diario, llama `aplicar_imagen`, espera a que el servicio
   corra **con esa imagen** y esté sano, y llama `verificar`. Si algo falla, llama `volver_imagen` con la referencia
   exacta que había y verifica de nuevo. Una pieza con datos se copia antes y, si falla, se repone esa copia primero.
6. Deja un acta con cada compuerta y, si hace falta una persona, un aviso.

Lo que no se pudo saber queda «desconocido». Nunca «al día», nunca «cero vulnerabilidades».

## Lo que el ejecutor no sabe hacer por sí mismo

Nada en un servidor. Llama **acciones con nombre**, de una lista cerrada, que la instancia define como comandos en lista:

| Acción | Recibe | Tiene que |
|---|---|---|
| `respaldo_vigente` | nada | salir con cero solo si hay un respaldo verificado del día |
| `aplicar_imagen` | `{servicio}`, `{referencia}` | dejar ese servicio corriendo con esa imagen fijada por huella |
| `verificar` | `{servicio}` | probar que el servicio funciona de verdad, no solo que arrancó |
| `volver_imagen` | `{servicio}`, `{referencia}` | dejarlo corriendo con la imagen anterior |
| `respaldar_datos` | `{servicio}`, `{marca}` | detener el servicio, copiar sus datos bajo esa marca, comprobar la copia y dejarlo detenido |
| `restaurar_datos` | `{servicio}`, `{marca}` | detenerlo, reponer exactamente esa copia y dejarlo detenido |

La salida de una acción no se guarda: solo si salió con cero. Los valores se validan antes de armar el comando.

Dos tipos de acción (`playbook`):

- `imagen`, para un servicio cuya vuelta atrás es volver a la imagen anterior. **No sirve para una pieza que migra datos
  al actualizarse** (una base, un servidor de chat, un forge): volver a la imagen no deshace la migración.
- `datos`, para esas piezas. Copia los datos del servicio justo antes, con una marca de ese intento. Si la versión nueva
  falla, repone esa copia y después levanta la imagen anterior. Usa las seis acciones.

**Dos capas de respaldo.** `respaldo_vigente` mira el respaldo general (la copia de la máquina o del servidor entero): es
la condición para empezar. La copia del tipo `datos` es la vuelta atrás de un solo servicio. El vigía no sabe qué
sistema hay detrás de ninguna de las dos: lo dicen los comandos de la instancia, y cambiar de servicio de respaldo es
cambiar esas líneas.

Odoo pide además su ensayo en la copia de pruebas: todavía no está.

## Lo que todavía no hace

- Odoo: ensayo en la copia de pruebas con batería antes y después.
- Ventanas extraordinarias para una urgencia; reinicio del servidor; paquetes del sistema fuera de la rutina desatendida.
- Mirar los addons de Odoo montados desde un repositorio y las dependencias de las aplicaciones propias.
- Pedir un dictamen al modelo (que solo podría frenar).

## Uso

```
python -m vigia correr --config vigia.json     # observa; imprime el resumen
python -m vigia correr --config vigia.json --sin-fuentes
python -m vigia resumen --config vigia.json    # el de la última corrida
python -m vigia aplicar --config vigia.json    # una pasada del ejecutor, en su ventana
python -m vigia aplicar --config vigia.json --presente   # con el dueño mirando: estrena un tipo de acción
python -m vigia foto                           # la foto de este servidor, en JSON
```

La primera corrida no pide instalar nada en el servidor: desde otra máquina, con un lector por ssh (ver el ejemplo).
Para dejarlo fijo: temporizadores con `OnCalendar` y `Persistent=true`, nunca relativos al último arranque, y comprobar
la próxima corrida después de cada reinicio.

## Configuración

`ejemplo/vigia.json` tiene todas las claves. Lo que nombra a la instancia vive ahí y fuera de este repo.

| Clave | Qué es |
|---|---|
| `servidores` | Lista de `{nombre, lector}`. `lector` es `"local"` o un comando (lista) que recibe `foto.py` por la entrada. |
| `propias` | Prefijos de imágenes propias. No se consultan afuera. |
| `componentes` | Por pieza (`registro/repositorio`): `nivel` (`nunca`, `patch`, `minor`), `expuesto`, `linea`, `base`, `playbook`, `nota`. |
| `por_defecto` | Lo que vale para una pieza sin declarar. De fábrica: `nivel: nunca`, sin tipo de acción. |
| `maduracion_dias` | Días desde la publicación antes de que una versión pase la compuerta. De fábrica, 7. |
| `plazos_h` | Horas de plazo por clase: `kev_expuesto`, `alta_expuesta`, `alta_interna`, `ordinario`. |
| `fuentes` | `docker_hub`, `kev` y la lista de `registros` OCI aprobados. Un registro fuera de la lista no se consulta. |
| `escaner` | `comando` (lista; se le agrega la imagen) y `tope_s` de toda la pasada. Sin comando: «desconocido». |
| `ventana` | `{dias, desde, hasta}`: días 0 (lunes) a 6 y un tramo del mismo día, en la hora del servidor. Sin ventana no aplica solo. |
| `playbooks` | Tipos de acción habilitados. De fábrica, ninguno. |
| `acciones` | Por servidor: las acciones con nombre, cada una un comando en lista con sus huecos. |
| `max_por_ventana` | Cuántas candidatas se aplican como mucho en una ventana. De fábrica, 3. |
| `espera_salud_s` | Cuánto se espera a que un servicio quede sano con la imagen nueva. De fábrica, 180. |
| `aviso` | Comando (lista) que recibe la ruta del archivo de un aviso. El canal no puede depender de lo que el vigía actualiza. |
| `estado_dir`, `registro_dir` | Dónde guarda el estado y dónde publica fichas y actas. |
| `foto_vigencia_s` | Cuánto puede diferir la hora de una foto. De fábrica, 900. |

`linea` dice cuántos números de adelante forman la línea que un parche no cambia. Sirve para una versión por
calendario: con `linea: 1`, de `2026.10.0` a `2026.11.0` es un parche.

## Pruebas

`bash scripts/test.sh`. Datos inventados, sin red y sin tocar ningún servidor. Cubren: versiones y referencias, la foto
y su limpieza, las fuentes con una red de mentira, el análisis, el estado entre corridas, el escáner, la corrida entera y
el ejecutor contra un servidor de mentira: compuertas, intención antes del efecto, falla con vuelta atrás, vuelta atrás
fallida, corte a mitad de camino sin repetir la acción, tope y un servidor por ventana; y el tipo `datos`: copia antes,
restauración de esa copia exacta, copia fallida, restauración fallida y cortes durante la copia y la restauración.
