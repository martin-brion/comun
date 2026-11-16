# Backlog de mejoras · comun

<!-- Uno por repo, en la raíz. Lo cura el dueño del repo. Sin fechas a mano ni pie de historial: la historia vive
     en git.
     Este archivo viaja en las entregas a otra empresa: nada de nombres de empresas, clientes ni instalaciones. -->

Ideas de capacidad, workflow o herramientas que todavía no se construyen. Lo de una sola vez no entra.

| # | Prioridad | Mejora | Dolor | Esfuerzo | Estado | Origen |
|---|---|---|---|---|---|---|
| 1 | Media | informe: citar archivos que no son markdown | Una cita de un script o de una configuración hoy no se puede comprobar: va como «fuente», sin documento | S `[SUPUESTO]` | idea | primer informe armado |
| 2 | Baja | informe: señalar el pasaje exacto dentro del bloque | Se señala el párrafo, el ítem o la fila entera; en un párrafo largo hay que buscar la frase | M `[SUPUESTO]` | idea | primer informe armado |
| 3 | Media | informe: prueba automática del visor de diagramas | El dibujo y la lupa se comprueban a mano en un navegador; un cambio puede romperlos sin que una prueba avise | M `[SUPUESTO]` | idea | primer informe armado |
| 4 | Media | atlas: la plantilla de árbol | Una base muy honda (área → subárea → procedimiento) no entra bien en tres columnas | M `[SUPUESTO]` | idea | atlas del conocimiento |
| 5 | Media | atlas: un vínculo entre documentos lleva a su página | Hoy un vínculo de un documento a otro de la base se muestra como texto con su ruta | S `[SUPUESTO]` | idea | atlas del conocimiento |
| 6 | Baja | atlas: versiones vigentes también en las áreas | El índice de vigentes se lee solo en los eslabones de una cadena | S `[SUPUESTO]` | idea | atlas del conocimiento |
| 7 | Baja | atlas: podar la caché de resúmenes | La caché guarda resúmenes de archivos que ya no existen o cambiaron; no molesta, pero crece | S `[SUPUESTO]` | idea | atlas del conocimiento |

Estados: `idea → en evaluación → aprobada → en curso → hecha / descartada`.

## Detalle

### 1 · informe: citar archivos que no son markdown

- **Problema:** el armador convierte todo documento citado como markdown. Un script o un archivo de configuración se vería
  mal, así que hoy esa evidencia va sin cita comprobable.
- **Propuesta:** un archivo que no termina en `.md` se muestra como código, con sus números de línea y las líneas del
  pasaje señaladas.
- **Plan:** elegir el modo por la extensión; prueba con un script inventado; decirlo en el contrato.

### 2 · informe: señalar el pasaje exacto dentro del bloque

- **Problema:** el resaltado cubre el bloque entero que contiene la cita.
- **Propuesta:** marcar además la frase, cuando la cita cae dentro de un solo tramo de texto sin formato en el medio.
- **Plan:** ubicar la cita sobre el texto ya convertido del bloque; si no se puede, queda el bloque como hoy.

### 3 · informe: prueba automática del visor de diagramas

- **Problema:** las pruebas no usan navegador. El visor se comprueba a mano con el ejemplo.
- **Propuesta:** una prueba aparte, que corre solo donde hay un navegador sin ventana, y lee el rastro del visor: cuántos
  diagramas vio y dibujó, también después de abrir un desplegable.
- **Plan:** que no entre en `scripts/test.sh` mientras pida red o un navegador; decidir antes si la librería de
  diagramas viaja dentro del repo.

### 4 · atlas: la plantilla de árbol

- **Problema:** el mapa de la casa dibuja tres columnas (casa, áreas, asistente). Una base con más niveles, o con
  subáreas, se aplana.
- **Propuesta:** un dibujo de jerarquía, hermano del mapa por columnas, que arma el componente sin librería.
- **Plan:** escribirlo primero en el contrato como bloque nuevo; usarlo en el atlas solo cuando la forma declare más de
  un nivel de carpetas.

### 5 · atlas: un vínculo entre documentos lleva a su página

- **Problema:** el conversor muestra un vínculo relativo como texto con su ruta; en el atlas, el documento de destino
  casi siempre tiene página.
- **Propuesta:** si el destino es un documento que el atlas muestra, el vínculo lleva a su página; si no, queda como hoy.
- **Plan:** resolver contra los archivos ya recorridos (como las flechas de la cadena), sin abrir nada; prueba con el
  ejemplo en cadena.

### 6 · atlas: versiones vigentes también en las áreas

- **Problema:** un procedimiento con varias versiones se muestra como varios documentos.
- **Propuesta:** las mismas claves `versiones` e `indice` en `areas`.
- **Plan:** reusar las piezas de la cadena dentro de la carpeta de cada procedimiento.

### 7 · atlas: podar la caché de resúmenes

- **Problema:** `incorporar` suma y reemplaza, pero nunca saca.
- **Propuesta:** un comando que deja en la caché solo los resúmenes vigentes de los archivos que existen.
- **Plan:** mismo cuidado que `incorporar`: se escribe de una vez y dice cuántos sacó.
