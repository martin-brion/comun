# Contrato de vigia

Versión del contrato: 0.1.0-dev.1. Lo que sigue no cambia sin decirlo en el pedido antes de cortar.

## 1. Reglas que no se negocian

1. **Observa.** Esta versión no ejecuta ninguna escritura en un servidor: los únicos comandos son los de `foto.py`
   (listar e inspeccionar contenedores e imágenes, consultar paquetes, leer cuatro archivos del sistema).
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
 "nivel", "expuesto", "compuerta_version", "nota", "detectada", "plazo": {"clase", "vence"}}
```

`id` es estable: sale del servidor, la pieza, la variante y la versión propuesta. `compuerta_version` vale `pasaría`,
`espera: …` o `fuera: …`. Es una sola de las compuertas del ejecutor futuro; las demás (respaldo verificado, ensayo,
ventana, dictamen) todavía no existen y por eso nada se aplica.

## 4. Estados de una candidata

Hoy: `detectada` → `cerrada` (la pieza ya corre esa versión o está al día) o `descartada` (la reemplazó otra, o la pieza
dejó de correr). Reservados para el ejecutor, en este orden: `evaluada`, `ensayada`, `programada`, `aplicando`,
`verificada`; y `esperando`, `recuperando`, `escalada`.

`ensayada` va a significar: aplicada sobre la copia de pruebas y aprobada por su batería, antes y después.

## 5. Salidas

- `<registro_dir>/<año>/<mes>/<AAMMDD-HHMMSS>.md`: la ficha de la corrida. `<registro_dir>/indice.md`: el índice.
- `<estado_dir>/resumen.txt`: el resumen de la última corrida, para una persona.
- `<estado_dir>/vigia.sqlite3`: el estado. No se edita a mano.

Códigos de salida de `python -m vigia`: 0 bien, 1 sin corrida previa, 2 configuración inválida, 3 otra corrida en curso.
