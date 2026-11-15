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
| `diagrama` | `mermaid`; opcionales `titulo` y `pie`. |
| `antes_despues` | `antes` y `despues`, cada uno `{mermaid, titulo?}`; opcional `pie`. |
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

## 4. El documento con el pasaje señalado

El markdown se convierte con un conversor propio: títulos, párrafos, listas, tablas, citas, bloques de código, reglas y el
formato de línea. Lo que no reconoce queda como texto. Un comentario HTML no se muestra. Se señala el bloque que contiene
el pasaje: el párrafo, el ítem de lista o la fila de tabla. Un bloque `mermaid` del documento se dibuja con el visor.

## 5. El visor de diagramas

- Dibuja cada diagrama en el navegador, con los colores del sitio en claro y en oscuro.
- Tamaño inicial: entero si entra en la columna; si no, achicado hasta el 55 % y el resto se recorre arrastrando. Alto
  máximo: 70 % de la ventana.
- Lupa: `−`, porcentaje, `+`, pantalla completa y volver al inicio. Paso de ×1,25, entre 40 % y 800 %.
- La rueda sola mueve la página; con Ctrl (o Cmd) hace zoom en el punto del cursor. En pantalla completa hace zoom sola.
- Arrastre con mouse o dedo; pellizco con dos dedos; doble clic para pantalla completa.
- Teclado, con foco en el diagrama: `+`, `-`, `0`, `f`, `Esc`.
- Un diagrama dentro de un desplegable cerrado se dibuja al abrirlo.
- En un diagrama de flujo se pueden usar tres clases sin definirlas: `cambia`, `nuevo` y `apagado`.
- Si la librería no cargó o un diagrama falla, queda su texto a la vista. Nunca un hueco.
- Al imprimir: sin controles, sin recorte y en esquema claro.
- Rastro: `window.__visor_diagramas` es una línea de texto con la versión, si corrió y cuántos diagramas vio y dibujó.

## 6. Lo que todavía no hace

- No resalta dentro del bloque: señala el bloque entero que contiene el pasaje.
- El conversor de markdown no cubre notas al pie, HTML embebido ni listas de tareas.
- El dibujo de los diagramas no tiene pruebas automáticas: se comprueba a mano (README).
