# Contrato de vigia

Versión del contrato: 0.2.0-dev.1. Lo que sigue no cambia sin decirlo en el pedido antes de cortar.

## 1. Reglas que no se negocian

1. **Observar no escribe.** `correr` no ejecuta ninguna escritura en un servidor: los únicos comandos son los de
   `foto.py` (listar e inspeccionar contenedores e imágenes, consultar paquetes, leer cuatro archivos del sistema).
2. **Desconocido no es bueno.** Una foto ausente, vencida o ilegible, una fuente caída o un escaneo cortado dejan el
   dato en «desconocido». No cierran ni descartan candidatas y no se informan como «al día» ni como «cero».
3. **La foto es de otra máquina.** Se reconstruye campo por campo, con tipos y tamaños fijos. Un campo de más no pasa;
   un texto fuera de forma frena la foto entera.
4. **Nada propio sale.** Una imagen cuyo nombre empieza con un prefijo de `propias` no se consulta en ninguna fuente.
   Hacia afuera solo va el nombre público de una imagen, por https y sin credenciales. Un registro que no está en
   `fuentes.registros` no se consulta.
5. **El nivel de fábrica es `nunca`.** Una pieza admite aplicación automática solo si la instancia lo declara. Un salto
   mayor queda siempre afuera, cualquiera sea el nivel.
6. **Sin maduración no pasa.** Una versión sin fecha de publicación conocida espera.
7. **Un KEV no es una alarma por sí solo.** Acorta el plazo solo con aplicabilidad verificada; si no, cuenta como una
   vulnerabilidad alta y se marca para revisar.
8. **Una corrida a la vez.** Un candado de más de dos horas se considera de una corrida muerta.
9. **El ejecutor no improvisa.** Solo llama acciones con nombre de la lista cerrada de §6, definidas por la instancia como
   comandos en lista. Nunca arma una línea de shell. Los valores de los huecos se validan contra un patrón fijo.
10. **La intención va antes que el efecto.** Cada acción que cambia algo deja su entrada en el diario antes de correr, y
    su resultado después.
11. **Que el comando salga bien no alcanza.** Una aplicación vale cuando el servicio corre con la huella pedida, está
    sano y pasó `verificar`.
12. **Un corte no se resuelve repitiendo.** Lo que quedó a medias se mide: si ya corre lo nuevo, hecho; si sigue lo
    anterior, no aplicado; si no se puede saber, se escala y en ese servidor no se aplica nada más.
13. **Sin vuelta atrás exacta no se aplica.** Hace falta la referencia con huella de lo que corre hoy.
14. **El estreno es con el dueño.** La primera aplicación de cada tipo de acción pide `--presente`. Un estreno que falla
    no cuenta.
15. **Nada por silencio.** Sin ventana definida, sin tipo de acción habilitado o con el archivo `PAUSA`, no aplica.
    Una candidata escalada espera a una persona.

## 2. La foto (`esquema: 1`)

```
{"esquema": 1, "tomada": <segundos>,
 "sistema": {"id", "version", "nombre", "kernel_en_uso", "kernels": [..] | null, "reinicio_pedido": bool,
             "paquetes": [{"nombre", "de", "a", "origen", "seguridad": bool}] | null, "listas_al": <segundos> | null,
             "desatendidas": bool | null},
 "docker": {"version": texto | null},
 "contenedores": [{"nombre", "imagen_ref", "imagen_id", "creado", "estado", "salud", "proyecto", "servicio"}] | null,
 "imagenes": {"<imagen_id>": {"tags": [..], "digests": [..], "creada", "etiquetas": {..}}},
 "errores": [texto]}
```

`null` es «no se pudo medir». De los rótulos de una imagen solo viajan cuatro: nombre y huella de la base, versión y
revisión. No viajan variables de entorno, montajes, comandos ni configuración de los servicios.

## 3. Una pieza y su candidata

Una pieza es una imagen distinta que corre en un servidor. Estados: `al día`, `atrasada`, `propia`, `base renovada`,
`desconocido`.

La candidata es el paso más chico disponible dentro de la misma familia de etiquetas (mismo prefijo, misma variante,
misma cantidad de números; las preliminares no cuentan). Los pasos más grandes se informan aparte, sin proponerlos.

```
{"id", "servidor", "pieza", "actual", "propuesta", "salto": "patch" | "minor" | "mayor", "publicada", "madura",
 "nivel", "expuesto", "compuerta_version", "nota", "detectada", "plazo": {"clase", "vence"},
 "indice", "referencia", "anterior"}
```

`referencia` es la imagen propuesta fijada por huella (`registro/repo:versión@sha256:…`); `anterior`, la que corre hoy,
también con huella. Sin alguna de las dos el ejecutor no aplica.

`id` es estable: sale del servidor, la pieza, la variante y la versión propuesta. `compuerta_version` vale `pasaría`,
`espera: …` o `fuera: …`. Es la primera de las compuertas del ejecutor (§6).

## 4. Estados de una candidata

`detectada` → `aplicando` → `cerrada`. Además: `esperando` (un intento falló y se volvió atrás; se reintenta en otra
ventana), `escalada` (dos fallas, una vuelta atrás fallida o algo a medias: espera a una persona) y `descartada` (la
reemplazó otra, o la pieza dejó de correr). El observador cierra o descarta cualquiera de las abiertas cuando mide que
la pieza ya corre esa versión; no toca una que está `aplicando`.

Reservados: `evaluada`, `ensayada`, `programada`. `ensayada` va a significar: aplicada sobre la copia de pruebas y
aprobada por su batería, antes y después.

## 5. Salidas

- `<registro_dir>/<año>/<mes>/<AAMMDD-HHMMSS>.md`: la ficha de la corrida. `<registro_dir>/indice.md`: el índice.
- `<registro_dir>/<año>/<mes>/<AAMMDD-HHMMSS>-ejecutor.md`: el acta de una pasada del ejecutor, con cada compuerta.
- `<estado_dir>/resumen.txt`: el resumen de la última corrida, para una persona.
- `<estado_dir>/avisos/<incidente>.txt`: un archivo por incidente que pide aviso. Uno al abrirse y un recordatorio por día.
- `<estado_dir>/vigia.sqlite3`: el estado, el diario y los incidentes. No se edita a mano.
- `<estado_dir>/PAUSA`: si existe, el ejecutor no hace nada. Lo crea y lo borra una persona.

Códigos de salida de `python -m vigia`: 0 bien, 1 sin corrida previa, 2 configuración inválida, 3 otra corrida en curso.

## 6. El ejecutor

**Tipos de acción.** `imagen`: reemplaza la imagen de un servicio de compose por otra fijada por huella. Su vuelta atrás
es la imagen anterior, así que **no vale para una pieza que migra datos al actualizarse**.

**Acciones con nombre** (por servidor, en `acciones`):

| Acción | Huecos | Contrato |
|---|---|---|
| `respaldo_vigente` | — | Cero solo si hay un respaldo verificado y vigente. No hace el respaldo. |
| `aplicar_imagen` | `{servicio}`, `{referencia}` | Deja el servicio corriendo con esa imagen. Repetirla con los mismos valores no cambia nada. |
| `verificar` | `{servicio}` | Cero solo si el servicio funciona de verdad. |
| `volver_imagen` | `{servicio}`, `{referencia}` | Deja el servicio corriendo con la imagen anterior. |

`{servicio}` es el nombre del servicio de compose (`^[a-z0-9][a-z0-9_.-]{0,62}$`); `{referencia}`, una imagen con huella.
Una acción tiene 15 minutos; pasado ese tiempo cuenta como falla.

**Compuertas**, en este orden; la primera que frena deja la candidata «bloqueada» y el acta dice cuál:

1. Versión: `pasaría`.
2. Tipo de acción: la pieza declara uno y está en `playbooks`.
3. Acciones: el servidor define todas las del tipo.
4. Estreno: el tipo ya se aplicó bien una vez, o la pasada es `--presente`.
5. Imagen fija: se conoce la huella de la versión propuesta.
6. Vuelta atrás: se conoce la huella de la imagen de hoy.
7. Servicios: cada contenedor que usa la imagen es un servicio de compose con nombre único.
8. Sin escalar.
9. Respaldo: `respaldo_vigente` salió con cero. Va última porque es la única que corre un comando.

Además: la ventana (o `--presente`), un servidor por día de ventana, el tope `max_por_ventana` y el archivo `PAUSA`.

**Resultado de una candidata.** `aplicada`; `falló; quedó como estaba` (a la segunda se escala y se avisa); o `falló y la
vuelta atrás también` (se escala, se avisa y la ventana se frena ahí).

**Incidentes que piden a una persona.** Vuelta atrás fallida; una candidata que no entró dos veces; algo a medias tras
un corte; y un plazo vencido. Se cierran solos cuando la candidata se cierra.
