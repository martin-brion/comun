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
| 8 | Media | vigia: avisos de los proyectos como señal que solo frena | Una versión retirada por un error grave o con una instrucción especial de actualización hoy no se ve: el registro y el escáner no la dicen | M `[SUPUESTO]` | idea | revisión independiente de vigia 0.5.0 |
| 9 | Media | vigia: escáner en una hoja remota | El vigía observa una hoja por ssh pero no la escanea: sin escáner no hay urgencia ni KEV allí, salvo que el vigía corra en la hoja | M `[SUPUESTO]` | idea | revisión independiente de vigia 0.5.0 |
| 10 | Media | gastos: borrador también ante un error de imputación o de categoría | Con ante_error=borrador, una imputación que el validador rechaza o una categoría ambigua siguen frenando el lote entero: la factura necesita cuenta y analítica para verificarse | M `[SUPUESTO]` | idea | pedido de borrador ante errores (gastos 0.1.0-dev.19) |

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

### 8 · vigia: avisos de los proyectos como señal que solo frena

- **Problema:** 0.5.0 dejó afuera las publicaciones de los proyectos como fuente: lo que se aplica sale del registro y la
  seguridad del escáner y la lista KEV. Pero un proyecto puede retirar una versión por un error que corrompe datos, o
  pedir un paso especial al actualizar, y ninguna de esas dos fuentes lo dice.
- **Propuesta:** una fuente opcional por componente (las notas de versión de su repositorio) que, si una versión
  candidata aparece retirada o con una instrucción especial, la frena y abre un incidente. Nunca habilita nada.
- **Plan:** el contrato primero (qué palabras o marcas cuentan, y que una fuente caída es «desconocido» y no frena todo);
  pruebas con publicaciones inventadas.

### 9 · vigia: escáner en una hoja remota

- **Problema:** el escáner corre donde corre el vigía. Una hoja leída por ssh queda sin vulnerabilidades, sin carril de
  urgencia y sin incidentes de KEV.
- **Propuesta:** que la hoja deje su resultado de escaneo con forma fija, firmado o por un comando forzado de solo
  lectura, y que el vigía lo lea como lee la foto, con su vigencia. Mientras tanto, la salida documentada es correr un
  vigía en cada hoja.
- **Plan:** esquema del resultado y su vigencia en el contrato; pruebas con un resultado vencido, ilegible o de otra
  imagen.

### 10 · gastos: borrador también ante un error de imputación o de categoría

- **Problema:** el pedido de borrador ante errores incluye «imputación o política que hoy rechazan (categoría ambigua,
  validador de imputación)». La 0.1.0-dev.19 no lo cubre: el imputador recibe todo el lote junto (una excepción no se puede
  atribuir a un comprobante) y la factura estándar se verifica línea por línea con la cuenta de la categoría y la analítica.
  La política de conducta no aplica: en el circuito de facturas ya no bloquea.
- **Propuesta:** con ante_error=borrador, un comprobante sin imputación válida o con una categoría que no resuelve entra con
  una cuenta y una analítica «a regularizar» declaradas por empresa y destino, marcado con el motivo; la verificación acepta
  esa forma solo en una factura marcada.
- **Plan:** decidir con el dueño las claves nuevas (cuenta y analítica a regularizar) y si un imputador que falla con todo el
  lote marca todos los comprobantes; pedir al imputador un resultado por línea (`pendiente` por comprobante) en una versión
  nueva de su contrato.
