# Contrato del guion, las marcas y la edición

El guion es un `guion.yaml` en la carpeta de la instancia; `tomar.py`, `editar.py` y `locutar.py` lo leen tal cual. Lo
particular de cada video (empresa, flujo, textos, personas) vive solo en el guion.

## Molde

```yaml
titulo: "Alta de una persona con un prompt"       # va en la placa de inicio
formato: pc                                       # pc (1920×1080) | celular (1080×1920, vertical)
marca: marca/marca.json                           # relativa a la raíz del repo del guion (o VIDEOS_BASE), o absoluta
musica: null                                      # un .mp3 de licencia libre (ruta absoluta), opcional
subtitulos: abajo                                 # abajo | superpuestos
binarios: '%OneDrive%\<carpeta de binarios de esta instancia>'
entorno:                                          # opcional: lo de esta PC para esta instalación (ver «Entorno»)
  VIDEOS_RAIZ: 'D:\<raíz propia>'
datos_reales: true                                # muestra personas o datos reales: ver «Datos reales»
tapar:                                            # zonas a difuminar en la versión de difusión (todas las escenas)
  - {x: 0, y: 0, w: 640, h: 40}
escritorio: {monitor: 1}                          # qué se graba de la PC: {monitor: n} o {region: {x, y, w, h}}
terminal: {pestana: "Alta", espacio: "<workspace>"}   # el panel de herdr del agente
placas:
  inicio: {titulo: "Alta en un prompt", subtitulo: "Cuenta, usuario, sistema, agenda y organigrama",
           locucion: "Así se da de alta a una persona nueva"}
  fin: {titulo: "<sitio>", subtitulo: "<frase>"}
escenario:                                        # lo que tiene que existir antes de la toma: nombres, nunca claves
  - "el agente abierto en la pestaña «Alta», con el flujo en modo ensayo"
edicion: {trabajo_max: 4}                         # opcional: pisa los valores de «Edición»
escenas:
  - id: pedido
    superficie: terminal
    texto: "Le pasás el CV y el puesto"           # el subtítulo de toda la escena
    locucion: "Le paso el CV y le digo qué puesto ocupa"   # opcional: lo que se dice (si falta, el texto)
    recorte: {x: 0, y: 0, w: 1600, h: 900}         # opcional: se muestra solo esta parte de la toma (zoom)
    tapar: [{x: 40, y: 300, w: 900, h: 60, desde: 12, hasta: 40}]   # opcional: zonas de esta escena
    pasos:
      - terminal.prompt:
          texto: "Alta de la persona del CV adjunto, puesto: analista"
          hitos: ['cuenta de correo creada', 'usuario .* creado', 'planning']
      - pausa: 1
  - id: instalacion
    superficie: filmacion
    texto: "Enchufás el pendrive y la PC se instala sola"
    archivo: filmacion/pc_nueva.mp4                # relativo a los binarios
    tramos: [{desde: 2, hasta: 9}, {desde: 9, hasta: 1500, vel: 120}, {desde: 1500, hasta: 1510}]
```

Cada paso es `acción: argumento`. Una URL que no puede quedar escrita (un enlace firmado) se pide con
`{url_de: "<comando que la imprime>"}`: la herramienta corre el comando, usa la salida y no la anota en ningún lado.

## Superficies y acciones

Una toma graba un solo grupo: `android` (formato celular), `web` o `escritorio` + `terminal` (formato pc). Un video que
mezcla grupos se arma con un guion por grupo y los mismos binarios, o con escenas de clip. Las escenas de clip
(`animacion`, `filmacion`) no se graban: las pone la edición.

| Superficie | Graba con | Acciones |
|---|---|---|
| todas | — | `pausa: <s>` · `captura: <nombre>` (PNG a la carpeta de binarios) |
| `escritorio` (la pantalla de la PC; `escritorio.py`) | ffmpeg `gdigrab` (`.mkv` del tamaño del monitor o de la región) | `escritorio.abrir: <url \| archivo \| {url_de}>` (con su aplicación; tramo `carga`) · `escritorio.tecla: "ctrl+l"` · `escritorio.escribir: <texto>` (en la ventana con foco) · `escritorio.esperar_carga: <s>` · `escritorio.manual: {aviso, tecla: F9, max}` (lo hace la persona y avisa con la tecla; tramo `manual`) |
| `terminal` (un panel de herdr; `terminal.py`) | la de `escritorio` | `terminal.prompt: <texto \| {texto, hitos, max, leer, comprimir_a}>` (escribe, manda y espera a que el agente termine) · `terminal.escribir: <texto>` · `terminal.enviar: {}` · `terminal.esperar: {hitos, hasta, max, leer, comprimir_a}` (sin mandar nada; `hasta`: una expresión regular que corta la espera, para una consola que no es un agente) · `terminal.tecla: <tecla de herdr>` · `terminal.enfocar: {}` (trae la pestaña al frente) |
| `web` (Chrome sin ventana, con Playwright; `web.py`) | DevTools (`.mkv` de 1440×810) | `web.ir: <url \| {url_de}>` (tramo `carga`) · `web.tocar: <texto visible \| {selector, texto, n}>` (un cursor dibujado va hasta el elemento) · `web.escribir: <texto>` · `web.tecla: <Tab \| Enter…>` · `web.desplazar: arriba \| abajo` · `web.esperar_carga: <s>` |
| `android` (emulador; `android.py`) | scrcpy (`.mkv`) | `android.abrir_app: <paquete>` · `android.abrir_url: <url \| {url_de}>` · `android.tocar: <texto visible \| {x, y}>` · `android.escribir: <texto>` · `android.deslizar: arriba \| abajo \| izquierda \| derecha \| {x1, y1, x2, y2, ms}` · `android.tecla: <TAB \| ENTER \| DEL…>` · `android.atras: {}` · `android.esperar_carga: <s>` |
| `android` → Element X (`elementx.py`) | la de `android` | `elementx.abrir_sala: <nombre>` · `elementx.mandar: <texto>` · `elementx.esperar_bot: {max, quieto}` · `elementx.tocar_link: {contiene}` · `elementx.volver: {}` |
| `animacion` (clip) | — | `archivo: <mp4 relativo a binarios>` · `segundos: <s>` (5): entra a lo ancho, con transición desde lo anterior y hacia lo que sigue |
| `filmacion` (clip: un video filmado aparte, p. ej. con un celular) | — | `archivo: <relativo a binarios>` · `tramos: [{desde, hasta, vel}]` (segundos del archivo; `vel` 1 por defecto: 120 es un minuto en medio segundo) · o `desde`, `hasta`, `vel` sueltos · `recorte` y `tapar` en píxeles del archivo; `tapar` con `desde`/`hasta` en segundos del archivo |

**Hitos.** Mientras el agente trabaja, cada vez que en el panel aparece por primera vez una de las expresiones de `hitos`,
o la persona aprieta F8, queda un hito. La edición acelera el trabajo y congela un instante cada hito: así se lee el
resultado de cada paso. Un hito que no apareció se avisa al terminar la toma.

**Permisos del agente.** Si el agente pide confirmar algo, la toma espera a que alguien conteste en la terminal; ese
tiempo cuenta como trabajo y se acelera.

Para escribir un guion: `python <superficies>/escritorio.py monitores`, `python <superficies>/terminal.py panel
<pestaña>`, `python <superficies>/web.py donde <url> "<texto>"` y `python <superficies>/android.py pantalla`.

## Entorno

Todo lo que depende de la PC cuelga de la raíz de la instalación, `VIDEOS_RAIZ`, que se dice en el entorno o en
`entorno:` del guion (solo claves `VIDEOS_*`, `ANDROID_SDK_ROOT` y `ANDROID_AVD_HOME`). Detalle: `herramientas/
superficies/entorno.py`. Para escritorio y terminal no hace falta raíz; para las fuentes de la marca, sí (sin ellas, las
letras son las de Windows).

| Variable | Por defecto | Qué |
|---|---|---|
| `VIDEOS_AVD` / `VIDEOS_PUERTO` / `VIDEOS_GRPC_PUERTO` | `video` / 5554 / 8554 | el celular de los videos y sus puertos |
| `VIDEOS_EMULADOR_EXTRA` | — | banderas de más para el emulador (p. ej. `-dns-server <ip>,8.8.8.8`) |
| `VIDEOS_MEMORIA_MIN_GB` | 4 | memoria libre que tiene que haber para arrancar un emulador |
| `VIDEOS_WEB_CDP` | 9333 | DevTools del Chrome de `web.py` |
| `VIDEOS_CLIC_RGB` | `37,99,235` | el color del anillo que marca un clic en la web |
| `VIDEOS_MICROFONO` | — | el micrófono de `locutar.py` |
| `VIDEOS_BASE` | la raíz del repo del guion | contra qué se resuelven `marca` y `con.marca` |

Dos instalaciones en la misma PC: raíces distintas, puertos distintos y **la misma versión de adb** (uno solo atiende a
toda la PC; otra versión lo corta). Procesos: cada una mata solo los suyos, por PID.

## Marcas de una toma

`toma_<n>.marcas.json`: `escenas` (id, t0, t1) y `tramos` (escena, acción, tipo, t0, t1 y extras), en segundos desde
el primer cuadro de la toma.

| Tramo | Qué es | Extras |
|---|---|---|
| `escribir` | lo que se tipea, letra por letra | `letras` |
| `espera` / `respuesta` | desde que se mandó hasta que asoma la respuesta del bot / la respuesta llegando | |
| `trabajo` | un agente trabajando | `hitos: [{t, que}]`, `comprimir_a` |
| `carga` | una página o un programa cargando | |
| `lectura` | la pantalla quieta después de una respuesta | `leer` |
| `accion` | tocar, deslizar, abrir, una tecla | |
| `manual` | lo que hizo la persona a mano | |
| `pausa` | una pausa del guion | |

## Edición

| Tramo | Regla (valor por defecto, en `edicion:`) |
|---|---|
| `escribir` | a `letras_s` letras por segundo (15); sin `letras`, ×`vel_escribir` (2) |
| `espera` | comprimida a `espera_max` s (3) |
| `respuesta` | ×`vel_respuesta` (2) |
| `trabajo` | comprimido a `trabajo_max` s (4) o a su `comprimir_a`; cada hito, `leer_hito` s quieto (1,2) |
| `carga` | comprimida a `carga_max` s (0,5) |
| `lectura` | se deja `leer` s (2), o el `leer` del paso |
| `accion` | ×1, hasta `accion_max` s (2,5) |
| `manual` | ×`vel_manual` (1,5) |
| `pausa` | ×1 |
| hueco entre pasos | hasta `hueco_max` s (0,5) |

**Diseño.** El logo arriba, la pantalla con esquinas redondeadas sobre el fondo de la marca y el texto de la escena abajo,
pegado, como un subtítulo (con el texto arriba no se puede mirar y leer a la vez). Con `subtitulos: superpuestos`, sin
banda: la pantalla más grande y el texto encima, en letras grandes con borde (`texto_posicion: arriba` en la escena si
abajo tapa). La pantalla entra entera en su caja respetando la proporción de la toma (un monitor vertical queda angosto:
para eso está `recorte`). Placas de inicio (`placa_inicio` 2,5 s) y de fin (`placa_fin` 3 s). De `marca.json`: el acento
(`color_marca`), el fondo (`color_texto_marca`), las letras (`tipografia`, `tipografia_texto`, como TTF en
`<raíz>/fuentes` sin espacios) y los logos (`logo_invertido.b64`, `logo_placa.b64`; `logo_ancho_video`,
`logo_placa_alto_video`). Sin marca, una neutra sin logo.

**Celular emulado.** Se muestra hasta la barra de navegación; si se ve el cartel del teclado ADB, se corta arriba del
cartel y se completa con blanco.

**Voz.** `binarios/voz/<id de escena>.wav` (o m4a, mp3, ogg, flac; `placa_inicio` y `placa_fin` para las placas). Entra
`voz_retardo` s (0,3) después de que empieza su escena, normalizada a `voz_lufs` (-16). Si es más larga que la escena, la
escena se queda quieta en su último cuadro hasta `voz_margen` s (0,6) después de la voz. Con voz, la música va a
`musica_lufs_con_voz` (-27) y además baja sola mientras se habla. `--sin-voz` la deja afuera.

**Música.** Es lo único que suena además de la voz (las tomas no traen sonido), a `musica_lufs` (-16) con fundido de
salida. Si el tema es más corto que el video, se repite un tramo del medio saltando donde el tema más se parece a sí
mismo, para que termine cerca de su final natural.

**Salida.** `video_<AAMMDD>_v<k>.mp4` (difusión) o `video_<AAMMDD>_v<k>_interno.mp4`, con su `.escenas.json` (dónde
empieza y termina cada escena y cada voz). El mp4 nunca se edita a mano: un cambio va al guion y se vuelve a armar.

## Datos reales

Un guion que muestra personas o datos reales lleva `datos_reales: true`. Entonces:

- La persona que aparece da su consentimiento por escrito antes de la toma, y sabe qué versión se difunde.
- `editar.py` arma por defecto la versión de **difusión**, con las zonas de `tapar` difuminadas, y frena si no hay
  ninguna. La **interna**, sin difuminar, se pide con `--interno` y no sale de la empresa.
- Las zonas se miden sobre la toma: `editar.py --muestras <n>` saca un cuadro por segundo con su segundo y su escena;
  una zona es un rectángulo en píxeles de la toma, para todo el guion, una escena o un lapso (`desde`, `hasta`, en
  segundos de esa toma; `toma` si vale para una sola).
- Antes de difundir, la versión de difusión se revisa entera cuadro por cuadro: `editar.py --cuadros --cada 0.5`. Un
  dato que se lee es un hallazgo: se suma una zona y se vuelve a armar.
- Claves, aunque sean iniciales, no aparecen en pantalla: el flujo no las imprime.
