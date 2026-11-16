# Contrato de informe

Versión del contrato: 0.2.0-dev.1. Lo que sigue no cambia sin decirlo en el pedido antes de cortar.

## 1. Qué garantiza

1. **Una cita que no está, frena.** Cada `cita` se busca en su `archivo`. Si no aparece, no se arma nada y el error dice
   en qué hallazgo y con qué empieza la cita.
2. **Se compone entero antes de escribir.** Un error de datos o de cita no deja un sitio a medias.
3. **Nada de los datos se ejecuta.** Textos, citas y documentos salen escapados. El diagrama viaja como texto.
4. **Lee solo dentro de las raíces.** Una ruta con `..`, absoluta o que pasa por un enlace se rechaza.
5. **Escribe solo en la salida.** La carpeta tiene que estar vacía o ser de un informe anterior (lleva `.informe.json`).
   Al rearmar se borran solo los archivos que el armado anterior anotó.
6. **Sale igual dos veces.** Los mismos datos y documentos dan los mismos bytes. No hay fecha ni hora de armado.
7. **Sin red al armar.** La única dirección externa es la de la librería de diagramas, que carga el navegador de quien
   lee. Con `--mermaid <archivo>` viaja dentro del sitio.

## 2. Uso

```
python -m informe validar <datos.json> --raiz <nombre>=<carpeta> ...
python -m informe armar   <datos.json> --raiz <nombre>=<carpeta> ... --salida <carpeta> [--mermaid <archivo|https>]
```

Código 0: armado o válido. 1: datos o citas con error, dicho en una línea. 2: mal uso.

Salida: `index.html`, `doc/<raíz>-<ruta>.html` por cada documento citado, `estatico/` con los recursos (cada uno con la
huella de su contenido en el nombre) y `.informe.json`.

## 3. Los datos

Un objeto JSON. Una clave que no figura acá es un error.

| Clave | Qué es |
|---|---|
| `titulo` | Nombre del informe. |
| `antetitulo`, `bajada`, `fuentes` | Opcionales: línea de arriba, frase de apertura y de dónde sale lo dicho. |
| `tema` | Opcional: `acento`, `acento_oscuro` (colores `#RRGGBB`), `fuente_titulos`, `fuente_texto`, `fuentes_url`. |
| `secciones` | Lista. Cada una: `id`, `titulo`, `bloques`; opcionales `menu` (nombre corto) e `intro`. |

En todo texto vale el formato de línea: `` `código` ``, `**negrita**`, `*cursiva*` y enlaces `https`.

### Bloques

| `tipo` | Campos |
|---|---|
| `veredicto` | `columnas`: lista de `{titulo, items}`. |
| `cifras` | `items`: lista de `{valor, texto}`. |
| `mapa` | El dibujo de la casa: columnas, cajas, flechas y vistas. Ver «El mapa». |
| `diagrama` | `mermaid`; opcionales `titulo` y `pie`. Es el respaldo, para lo que no entra en un mapa. |
| `matriz` | `columnas`: `{id, nombre, nota?}`. `filas`: `{pieza, regla?, celdas}`, con una celda `[estado, texto]` por columna. |
| `hallazgos` | `items`: `{severidad, tema, titulo, texto, evidencia}`. |
| `desplegable` | `titulo` y `bloques` (cualquiera, salvo `hallazgos` y otro `desplegable`). |
| `tarjetas` | `items`: `{titulo, texto}`. |
| `pasos` | `items`: textos, en orden. |
| `fases` | `items`: `{titulo, cuando?, texto?, items?}`. |
| `decisiones` | `items`: `{id, decision, recomendacion}`. |
| `texto` | `parrafos`. |
| `subtitulo`, `nota`, `codigo` | `texto`. |

Estados de una celda: `ok` (sigue la regla), `exc` (distinto, pero válido en su etapa), `dif` (distinto y conviene
corregir), `na` (no le corresponde). Severidades: `alta`, `media`, `baja`.

### Evidencia

Cada elemento lleva `parafrasis` y una de dos formas:

- **Con documento:** `cita` y `archivo`, siempre juntos. `archivo` va como `raiz:ruta/relativa.md`.
- **Sin documento** (una medición, una consulta): `fuente`, que dice de dónde sale.

La cita se compara ignorando saltos de línea, espacios repetidos, los `*` y los acentos graves del formato, y el `>` de
una cita al empezar la línea. Lo demás tiene que coincidir. Largo: entre 12 y 600 caracteres. Si aparece más de una vez,
se señala la primera.

### El mapa

Un dibujo por columnas que arma el propio componente, sin librería. La posición la da el dato: cada cosa dice en qué
columna y en qué fila va, y por eso el mismo dato da siempre el mismo dibujo.

| Clave | Qué es |
|---|---|
| `columnas` | De una a cinco. Cada una: `titulo`, `items` y, opcional, `fondo` (verdadero dibuja el marco de la columna). |
| `flechas` | Opcional. Cada una: `de`, `a` (ids de cajas) y, opcionales, `texto` (hasta 18 letras) y `vistas`. |
| `vistas` | Opcional. Lista de `{id, nombre}`. |
| `fuera_de_vista` | `atenuar` (por defecto) u `ocultar`: qué pasa con lo que no es de la vista elegida. |
| `titulo`, `pie` | Opcionales: una banda arriba del dibujo y la leyenda de abajo. |

Un ítem de una columna es una caja o una nota, y siempre lleva `fila` (desde 1):

- **Caja:** `id`, `titulo`; opcionales `texto`, `estado` (`normal`, `acento`, `aviso`, `apagado`), `vistas`, `filas`
  (cuántas filas ocupa, hacia abajo), `enlace` y `dentro`, una lista de fichas `{titulo, estado?, vistas?, enlace?}` que
  se dibujan adentro.
- **`enlace`:** una página del mismo sitio, relativa a su raíz (`area/altas.html`, `doc/x.html#y`). La caja o la ficha
  se vuelve un vínculo. Si la página no está entre las que se arman, el armado frena.
- **Nota:** `nota`, un texto suelto; opcional `vistas`.

Reglas del dibujo:

- Las cosas de la misma columna y la misma fila se apilan en el orden en que están escritas. Una fila mide lo que mide
  la pila más alta: así las filas quedan alineadas entre columnas.
- Una caja de varias filas no comparte sus filas con otra cosa de su columna.
- Una flecha une cajas de la misma columna o de columnas vecinas. Si las dos cajas se enfrentan, va derecha; si no,
  hace un codo en el espacio entre columnas.

Vistas:

- **Siempre hay una vista «Todas», va primera y es la que se ve al abrir.** No se declara.
- Una cosa sin `vistas` está en todas. Con `vistas`, solo en esas.
- Una ficha sin `vistas` toma las de su caja. Una flecha sin `vistas` toma las que comparten sus dos cajas.
- Con `atenuar`, lo que no es de la vista elegida queda tenue: sirve para resaltar a quién le pertenece cada cosa. Con
  `ocultar`, desaparece: sirve para mostrar dos estados, como «hoy» y «propuesto», sobre el mismo dibujo.
- Sin el script de la página se ve «Todas».

## 4. El documento con el pasaje señalado

El markdown se convierte con un conversor propio: títulos, párrafos, listas, tablas, citas, bloques de código, reglas y el
formato de línea. Lo que no reconoce queda como texto. Un comentario HTML no se muestra, y el texto que lo rodea sí. Se
señala el bloque que contiene el pasaje: el párrafo, el ítem de lista, la fila de tabla o el diagrama. Varias citas del
mismo bloque tienen cada una su destino. Una cita que cae dentro de un comentario frena el armado. Un bloque `mermaid`
del documento se dibuja con el visor.

## 5. El visor de diagramas

- Le pone la lupa a los dos tipos de dibujo: el mapa, que ya viene hecho, y el diagrama escrito como texto, que se
  dibuja en el navegador con los colores del sitio en claro y en oscuro.
- La librería de diagramas se pide solo en las páginas que traen un diagrama escrito como texto.
- Tamaño inicial: entero si entra en la columna; si no, achicado hasta el 55 % y el resto se recorre arrastrando. Alto
  máximo: 70 % de la ventana.
- **Un mapa que entra entero en la columna no lleva lupa**: queda fijo, como una imagen. La recibe solo si no entra
  (una pantalla angosta o un mapa de muchas columnas). El diagrama escrito como texto la lleva siempre.
- Lupa: `−`, porcentaje, `+`, pantalla completa y volver al encuadre. Paso de ×1,25, entre 40 % y 800 %.
- **Volver al encuadre.** Apenas alguien acerca, aleja o mueve el dibujo, la barra se pone opaca y el botón de volver
  se destaca con su texto. Un clic, o la tecla `0`, devuelve el encuadre de partida: el tamaño inicial o, en pantalla
  completa, el ajuste a la pantalla.
- La rueda sola mueve la página; con Ctrl (o Cmd) hace zoom en el punto del cursor. En pantalla completa hace zoom sola.
- Arrastre con mouse o dedo; pellizco con dos dedos; doble clic para pantalla completa.
- Teclado, con foco en el diagrama: `+`, `-`, `0`, `f`, `Esc`.
- Un diagrama dentro de un desplegable cerrado se dibuja al abrirlo.
- En un diagrama de flujo escrito como texto se pueden usar tres clases sin definirlas: `cambia`, `nuevo` y `apagado`.
- Si la librería no cargó o un diagrama falla, queda su texto a la vista. Nunca un hueco.
- Al imprimir: sin controles y sin recorte. La página sale en esquema claro.
- Rastro: `window.__visor_diagramas` es una línea de texto con la versión, si corrió y cuántos diagramas vio y dibujó.

## 6. El atlas de una base de conocimiento

Un sitio por base, que se lee de arriba hacia abajo: el mapa de la casa, una página por área (o por eslabón), una por
procedimiento y el documento. Lo arma el componente a partir de la estructura de la base, sin modelo y sin red.

### Uso

```
python -m informe atlas validar    <forma.json> --base <carpeta> [--recorte <nombre>]
python -m informe atlas armar      <forma.json> --base <carpeta> --salida <carpeta> [--recorte <nombre>]
                                   [--resumenes <archivo>] [--mermaid <archivo|https>]
python -m informe atlas pendientes <forma.json> --base <carpeta> --resumenes <archivo> [--recorte <nombre>] [--json]
python -m informe atlas incorporar <nuevos.json> --base <carpeta> --resumenes <archivo>
```

Código 0: hecho. 1: la declaración, la base o los resúmenes tienen un error, dicho en una línea. 2: mal uso.

### Garantías

1. **Una corrida, una base, una salida.** Lee una sola carpeta `--base` y escribe una sola `--salida`, con un solo
   recorte. Valen las garantías 2, 3, 5, 6 y 7 del informe.
2. **La forma se declara, no se adivina.** Sin el archivo de la forma, o con una clave que no figura acá, no se arma
   nada. Si a la base le falta una carpeta declarada, el error dice cuáles faltan, todas juntas.
3. **Lee solo dentro de la base.** Toda ruta de la declaración es relativa a la base, sin `..`, sin `.` (salvo la carpeta
   de áreas, que puede ser `.`) y sin unidad ni barra inicial. Un enlace o un punto de unión encontrado al recorrer la
   base frena el armado: se oculta con `ocultar` o se saca. Los nombres que empiezan con `.` no se recorren nunca.
4. **Solo markdown.** Se leen los archivos `.md`, de hasta 2 MB y en UTF-8. Uno que no cumple frena y se dice cuál.
   Los demás archivos no se leen ni se muestran.
5. **Lo que se muestra es lo que está.** El atlas no corrige ni completa. Un archivo declarado que falta o que está
   vacío se muestra como «sin definir», con su ruta: es lo que a su dueño le falta cargar. Vacío quiere decir que, sin
   comentarios, no tiene más que títulos.
6. **Un recorte es otra salida.** Lo que un recorte oculta no se lee y no aparece en ninguna parte de esa salida: ni en
   las páginas, ni en el dibujo, ni en los nombres de archivo, ni en `estatico/`, ni en `.informe.json`. Nunca es una
   salida con partes escondidas. Una mención en el texto de otro documento se muestra como está escrita.
7. **Nada de los datos se ejecuta.** Los documentos y los resúmenes salen escapados, como en el informe. Un vínculo
   dentro de un documento se sigue solo para unir eslabones: se compara con los archivos ya recorridos y no abre nada.

### La declaración de la forma

Un objeto JSON, del lado de quien corre el atlas (no hace falta que viva dentro de la base). Una clave que no figura acá
es un error.

| Clave | Qué es |
|---|---|
| `titulo` | Nombre de la base. |
| `forma` | `areas` (área → procedimiento) o `cadena` (eslabones en fila). |
| `orienta` | El archivo que orienta: las reglas de base. |
| `rutea`, `funciones` | Opcionales: el archivo que rutea y el que declara lo que sabe hacer el asistente. |
| `areas` | Con `forma: areas`. Ver abajo. |
| `eslabones` | Con `forma: cadena`. Ver abajo. |
| `ocultar` | Opcional: lo que no se muestra en ninguna salida. |
| `salidas` | Opcional: `{nombre: {ocultar?}}`. Cada salida suma su `ocultar` al general. Sin `salidas` hay una sola, `completa`. |

`areas`:

| Clave | Qué es |
|---|---|
| `carpeta` | Donde están las áreas: cada subcarpeta es un área. Puede ser `.`, la raíz de la base. |
| `reglas` | El archivo de reglas, dentro de cada área. |
| `procedimientos` | La carpeta de procedimientos, dentro de cada área: cada subcarpeta es un procedimiento. |
| `documento` | El archivo principal, dentro de cada procedimiento. |
| `pasos`, `lee`, `deja` | Opcionales: los títulos que buscar en el documento del procedimiento, como lista. Por defecto `["Pasos"]`, `["Lee", "De dónde lee"]` y `["Deja", "Qué deja"]`. |

`eslabones`: una lista de uno a cinco `{carpeta, titulo, versiones?, indice?}`, en el orden de la cadena. Las carpetas no
pueden repetirse ni estar una dentro de otra.

- Sin `versiones`, cada archivo de la carpeta (también de sus subcarpetas) es una pieza con una sola versión.
- `versiones: carpeta`: cada subcarpeta es una pieza y sus archivos son las versiones. Un archivo suelto es una pieza.
- `versiones: sufijo`: los archivos que se llaman igual salvo un final `-v<número>`, `_v<número>` o `.v<número>` son
  versiones de la misma pieza, ordenadas por ese número.
- `indice`, que va junto con `versiones`: el archivo, relativo a la base, que marca la versión vigente. Cada vínculo
  markdown `[texto](ruta.md)` del índice, relativo a la carpeta del índice, marca como vigente ese archivo. El índice no
  es una pieza.

Qué frena, además de una declaración mal escrita: que falte la base, la carpeta de áreas, la de un eslabón o el
índice; que el recorte oculte cualquiera de ellos; que el índice marque un archivo que no existe o que no es de su
eslabón, o dos versiones de la misma pieza. Una pieza que el índice no marca se muestra sin vigente, con un aviso.

`ocultar` es una lista de rutas relativas a la base, partidas por `/`. Un tramo `*` vale por un nombre cualquiera y `**`
por cualquier cantidad de carpetas. Oculta lo que nombra y todo lo de adentro. Se compara sin distinguir mayúsculas.

### Qué muestra cada nivel

1. **El mapa de la casa** (`index.html`). Un `mapa` con vínculos. Con `forma: areas`: tres columnas, las reglas de
   base (orienta y rutea), las áreas con sus procedimientos como fichas, y lo que sabe hacer el asistente (cada ítem de
   primer nivel de `funciones`); el archivo que rutea apunta a cada área. Con `forma: cadena`: una columna por
   eslabón y una caja por pieza; una flecha une dos piezas de eslabones vecinos cuando una enlaza a la otra. Las reglas
   de base y el asistente van debajo. Una columna dibuja hasta 15 cajas; el resto lo dice una nota y está en la página
   del nivel siguiente.
2. **Un área** (`area/<nombre>.html`): sus reglas y sus procedimientos, y los demás documentos de la carpeta. **Un
   eslabón** (`eslabon/<nombre>.html`): sus piezas, cada una con su versión vigente a la vista y las anteriores
   plegadas, como antecedentes.
3. **Un procedimiento** (`proc/<nombre>.html`): sus pasos, de dónde lee y qué deja, tomados de las secciones con esos
   títulos de su documento, y los demás documentos de su carpeta.
4. **El documento** (`doc/<nombre>.html`): el markdown entero, como en el informe. Una versión reemplazada lo dice
   arriba y nombra la vigente.

Cada página lleva arriba el camino de vuelta hasta el mapa. Los nombres de área, procedimiento y eslabón salen de su
carpeta (guiones y guiones bajos pasan a espacios); el de un documento, de su primer título o de su archivo.

### La caché de resúmenes

El resumen de cada documento lo escribe alguien de afuera (un modelo, una persona) y se guarda. El componente no llama
a ningún modelo ni usa red. Sin resumen guardado, o con uno de otra versión del archivo, se usa el primer párrafo.

```json
{"formato": "resumenes-1",
 "resumenes": {"areas/altas/reglas.md": {"huella": "sha256:<64 hex>", "resumen": "Qué dice, en una o dos frases."}}}
```

- La clave es la ruta relativa a la base. `huella` es el SHA-256 de los bytes del archivo tal como está en disco.
- Un resumen vale solo si su huella es la del archivo de hoy: cambiar un archivo invalida solo el suyo.
- `resumen`: de 1 a 1200 caracteres, con el formato de línea.
- `pendientes` lista los documentos cuyo resumen muestra el atlas (en el mapa y en las páginas de los niveles 2 y 3)
  y que no tienen uno vigente: una línea `<huella> <ruta>` por documento, ordenadas por ruta. Una versión reemplazada o
  un índice no muestran resumen y no se listan. Con `--json` da la caché a llenar, con `resumen` vacío. Si el archivo de resúmenes no
  existe, todos están pendientes.
- `incorporar` suma a la caché los resúmenes de otro archivo con el mismo formato. Frena, sin escribir nada, si una
  ruta no es un archivo de la base o si su huella no es la de hoy. Un `resumen` vacío se saltea y se cuenta. La caché se
  escribe ordenada y se reemplaza de una vez.

## 7. Lo que todavía no hace

- No resalta dentro del bloque: señala el bloque entero que contiene el pasaje.
- Un diagrama conserva al imprimir los colores con que estaba dibujado en pantalla: conviene imprimir desde el esquema
  claro.
- Un comentario HTML dentro de un tramo de código en línea también se oculta.
- El conversor de markdown no cubre notas al pie, HTML embebido ni listas de tareas.
- El mapa se prueba entero, porque sale de Python. La lupa y el dibujo de los diagramas escritos como texto no tienen
  pruebas automáticas: se comprueban a mano (README).
- El mapa no mide el texto en un navegador: parte los renglones contando letras. Con una letra muy ancha, un renglón
  puede pasarse de su caja.
- El atlas no dibuja todavía la plantilla de árbol: una base en cadena va con el mapa por columnas.
- En el atlas, un vínculo entre documentos de la base se muestra como texto con su ruta; no lleva a la página del otro
  documento.
- El índice de vigentes se lee solo en los eslabones de una cadena, no en las áreas.
