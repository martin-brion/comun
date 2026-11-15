# Contrato de informe

Versión del contrato: 0.1.0-dev.1. Lo que sigue no cambia sin decirlo en el pedido antes de cortar.

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
  (cuántas filas ocupa, hacia abajo) y `dentro`, una lista de fichas `{titulo, estado?, vistas?}` que se dibujan adentro.
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

## 6. Lo que todavía no hace

- No resalta dentro del bloque: señala el bloque entero que contiene el pasaje.
- Un diagrama conserva al imprimir los colores con que estaba dibujado en pantalla: conviene imprimir desde el esquema
  claro.
- Un comentario HTML dentro de un tramo de código en línea también se oculta.
- El conversor de markdown no cubre notas al pie, HTML embebido ni listas de tareas.
- El mapa se prueba entero, porque sale de Python. La lupa y el dibujo de los diagramas escritos como texto no tienen
  pruebas automáticas: se comprueban a mano (README).
- El mapa no mide el texto en un navegador: parte los renglones contando letras. Con una letra muy ancha, un renglón
  puede pasarse de su caja.
