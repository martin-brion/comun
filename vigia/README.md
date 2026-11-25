# vigia · vigía de parches

Mira qué corre en cada servidor, lo compara con lo publicado, deja registro y, en su ventana, aplica lo que pasa todas las
compuertas. Nace de la especificación del vigía que ya corría de un lado; se escribe de cero para que los dos lados usen
la misma pieza. Desde 0.5.0 cubre también lo que ese vigía miraba y este no (máquinas virtuales, repositorios montados,
KEV sin arreglo, el vigía caído), así que alcanza con uno solo.

La meta es que trabaje sin supervisión: llama a una persona solo por lo que no puede resolver solo (CONTRATO §7), y lo
que se arregla solo no lo avisa.

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
5. **Sistema**: el reinicio que pide la rutina desatendida de la distribución es una candidata más (pieza `sistema`).
6. **Urgencia**: un KEV con arreglo en una pieza `expuesto` acorta el plazo a un día y la manda al carril de urgencia.
7. **Incidentes** de lo que dura y solo una persona resuelve: servidor sin medir, parches de seguridad que no entran,
   KEV sin arreglo en algo expuesto, máquina virtual ausente, pausa de más.
8. **Registro**: una ficha Markdown por corrida, con índice, un resumen corto para una persona y un latido para un
   monitor de afuera (`python -m vigia salud`).

**Aplicar** (`python -m vigia aplicar`): una pasada del ejecutor.

1. Si existe `<estado_dir>/PAUSA`, no hace nada.
2. Resuelve lo que un corte dejó a medias, midiendo. No repite ninguna acción.
3. Fuera de la ventana no sigue (salvo `--presente`). Un servidor por ventana. En la ventana de urgencia solo toma lo
   urgente, sin esperar la maduración y después de comprobar con el escáner que la versión nueva ya no tiene la falla.
4. Observa de nuevo y, para cada candidata, pasa las compuertas en orden: versión, tipo de acción habilitado, acciones
   definidas, estreno, imagen fija por huella, vuelta atrás exacta, servicios, sin escalar y respaldo vigente.
5. Para cada servicio que usa la imagen: deja la intención en el diario, llama `aplicar_imagen`, espera a que el servicio
   corra **con esa imagen** y esté sano, y llama `verificar`. Si algo falla, llama `volver_imagen` con la referencia
   exacta que había y verifica de nuevo. Una pieza con datos se copia antes y, si falla, se repone esa copia primero.
6. El reinicio del servidor va último: mide el arranque, reinicia, espera que vuelva todo y verifica.
7. Deja un acta con cada compuerta y, si hace falta una persona, un aviso. Un estreno pendiente o una candidata trabada
   dos ventanas seguidas también se avisan: nada queda frenado en silencio.

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
| `construir_propia` | `{servicio}`, `{base}` | armar la imagen propia de ese servicio, la misma versión, sobre esa base, con la base en sus rótulos, y probarla aislada; sin tocar lo que corre y conservando la de hoy |
| `aplicar_propia` | `{servicio}`, `{base}` | dejar el servicio corriendo con la imagen armada sobre esa base |
| `volver_propia` | `{servicio}`, `{base}` | dejarlo corriendo con la imagen armada sobre la base anterior |
| `reiniciar` | nada | reiniciar el servidor |
| `verificar_servidor` | nada | probar que el servidor quedó bien después de arrancar |

Toda acción de un servicio puede usar también `{proyecto}`, el proyecto de compose: hace falta cuando dos proyectos tienen
un servicio con el mismo nombre.

La salida de una acción no se guarda: solo si salió con cero. Los valores se validan antes de armar el comando.

Cuatro tipos de acción (`playbook`):

- `imagen`, para un servicio cuya vuelta atrás es volver a la imagen anterior. **No sirve para una pieza que migra datos
  al actualizarse** (una base, un servidor de chat, un forge): volver a la imagen no deshace la migración.
- `datos`, para esas piezas. Copia los datos del servicio justo antes, con una marca de ese intento. Si la versión nueva
  falla, repone esa copia y después levanta la imagen anterior. Usa las seis acciones.
- `propia`, para una imagen que arma la instancia. Cuando su base tiene una versión nueva, la rehace igual sobre esa base,
  la prueba aislada, la pone a correr, la verifica y, si algo falla, vuelve a la que había. Para saber qué base tiene, la
  imagen tiene que decirlo en sus rótulos (`org.opencontainers.image.base.name` y `.base.digest`): la instancia los pone
  al armarla.

- `reinicio`, para la pieza `sistema`. **No tiene vuelta atrás**: va último en la pasada, nunca después de una falla, y
  si el servidor no vuelve o no vuelve todo lo que corría, escala. Si el vigía corre en el mismo servidor, el reinicio lo
  corta: la instancia corre una pasada de `aplicar` al arrancar y esa pasada lo resuelve midiendo.

**El aviso.** Cada incidente deja un archivo y, si la instancia definió `aviso`, se lo pasa a ese comando. Una instancia
avisa por chat y otra por mail: el vigía no sabe cuál, y el canal no puede depender de lo que el vigía actualiza.

**Dos capas de respaldo.** `respaldo_vigente` mira el respaldo general (la copia de la máquina o del servidor entero): es
la condición para empezar. La copia del tipo `datos` es la vuelta atrás de un solo servicio. El vigía no sabe qué
sistema hay detrás de ninguna de las dos: lo dicen los comandos de la instancia, y cambiar de servicio de respaldo es
cambiar esas líneas.

Odoo pide además su ensayo en la copia de pruebas: todavía no está.

## Lo que todavía no hace

- Odoo: ensayo en la copia de pruebas con batería antes y después. Hasta entonces, Odoo queda con nivel `nunca`.
- Instalar paquetes del sistema: los instala la rutina desatendida de la distribución; el vigía avisa si no entran.
- Actualizar un addon montado: solo informa si su commit es distinto del remoto.
- Las dependencias de las aplicaciones propias: entran al rehacer la imagen (`propia`).

**Lo que se dejó afuera a propósito.** El dictamen de un modelo (solo podía frenar, y un «sin objeciones» nunca fue
permiso: las compuertas ya frenan con datos) y las publicaciones de proyectos como fuente (lo que se puede aplicar es lo
que está en el registro de imágenes; el escáner y la lista de explotadas cubren la seguridad).

## Uso

```
python -m vigia correr --config vigia.json     # observa; imprime el resumen
python -m vigia correr --config vigia.json --sin-fuentes
python -m vigia resumen --config vigia.json    # el de la última corrida
python -m vigia aplicar --config vigia.json    # una pasada del ejecutor, en su ventana
python -m vigia aplicar --config vigia.json --presente   # con el dueño mirando: estrena un tipo de acción
python -m vigia salud --config vigia.json      # para un monitor de afuera: 0 bien, 4 si no corre o está en pausa de más
python -m vigia resuelto --config vigia.json <incidente>   # una persona: «esto ya está», levanta el bloqueo
python -m vigia foto                           # la foto de este servidor, en JSON
```

La primera corrida no pide instalar nada en el servidor: desde otra máquina, con un lector por ssh (ver el ejemplo).
Para dejarlo fijo: temporizadores con `OnCalendar` y `Persistent=true`, nunca relativos al último arranque, y comprobar
la próxima corrida después de cada reinicio (`verificar_servidor`). Con lo mínimo de supervisión:

- `correr` todos los días (de noche, por el escáner);
- `aplicar` todos los días dentro de la ventana de urgencia y en la ventana ordinaria; y una vez al arrancar el servidor,
  para resolver un reinicio que cortó al propio vigía;
- `salud` desde un monitor que no vive en lo que el vigía vigila, con aviso si da distinto de cero.

**Una hoja remota** (lector por ssh) se observa, pero su escáner no: el escáner corre donde corre el vigía. Para que una
hoja tenga vulnerabilidades, urgencia y KEV, el vigía corre en la hoja misma, con su propia configuración.

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
| `ventana_urgente` | `{desde, hasta}` y, opcional, `dias` (de fábrica, todos). Solo toma candidatas urgentes. Sin ella, la urgencia solo acorta el plazo. |
| `espera_reinicio_s` | Cuánto se espera a que el servidor arranque de nuevo. De fábrica, 1200. |
| `sistema_plazo_h` | Horas con parches de seguridad del sistema pendientes antes de avisar. De fábrica, 72. |
| `pausa_max_dias` | Días con el archivo `PAUSA` antes de avisar. De fábrica, 7. |
| `maquinas` | `{archivo, vigencia_s, esperadas: {nombre: {servidor, apagada, sin_vigia}}}`: el censo de máquinas virtuales (CONTRATO §8). |
| `repos` | Lista de `{nombre, servidor, local, remoto, rama}`: repositorios montados, por commit (CONTRATO §8). |

La pieza `sistema` se declara como cualquier componente: `"sistema": {"nivel": "patch", "playbook": "reinicio"}`. De
fábrica tiene nivel `nunca`: el vigía solo informa que el servidor pide reinicio.
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
`tests/test_unico.py` cubre lo de 0.5.0 (y, en `Revision`, `RecuperarTrasUnCorte` y `ReinicioRoto`, un caso por cada
hallazgo de la revisión independiente): contenedor desde un ID, proyecto de compose, aviso de cierre y noticias, estreno
pendiente y candidata trabada, urgencia (aplica sin madurar solo con el arreglo comprobado), reinicio (bien, sin volver,
sin reiniciar, después de una falla, cortando al propio vigía), servidor sin medir, pausa de más, latido y salud,
parches del sistema, censo de máquinas y repositorios.
