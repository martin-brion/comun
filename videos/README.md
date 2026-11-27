# videos

Graba y edita videos de uso a partir de un guion: lo que pasa en la pantalla de la PC (un agente de código trabajando,
el navegador, cualquier programa), en un celular emulado o en un navegador sin ventana, más lo filmado aparte con un
celular. La edición acelera las esperas, congela los resultados de cada paso, pone subtítulos, placas con la marca, voz
en off y música, y saca dos versiones cuando hay datos reales: una interna y otra difuminada para difundir.

- Guion, superficies, acciones, marcas y reglas de edición: `CONTRATO.md`.
- Reglas para tocar el código: `AGENTS.md`.
- Herramientas: `herramientas/tomar.py`, `editar.py` y `locutar.py`; superficies en `herramientas/superficies/`.

## Workflow: de un guion a un video editado

Un solo job: entra un guion de escenas y sale un video editado que muestra cómo se usa algo. Sirve para vender (una demo)
y para capacitar (cómo se hace X). Se repite cada vez que hace falta un video; la herramienta no sabe de ninguna empresa
ni de ningún flujo: todo lo particular está en el guion. Cada instalación dice en su ruteo dónde viven sus instancias.

### Inputs

| Fuente | Ubicación | Por qué |
|---|---|---|
| De ESTA corrida | `<instancia>/guion.yaml` y `<instancia>/00_estado.md` | qué se muestra, en qué orden, con qué textos; en qué quedó |
| De ESTA corrida | la `marca.json` que nombre el guion | colores, letras y logo |
| De TODAS las corridas | `CONTRATO.md` de este componente | formato del guion, acciones, edición, datos reales |
| De TODAS las corridas | `herramientas/` de este componente | tomar, editar y locutar |

`<instancia>` = `.../videos/<AAMMDD>_<slug>/`, en el repo dueño del tema. Los binarios (tomas, filmaciones, voces y
videos) van fuera de git, en la carpeta que dice `binarios:` del guion, con un puntero `📁 Binarios en …` en
`00_estado.md` y su inventario en `00_binarios.md`.

NO cargar: tomas ni videos (son binarios: se miran con `editar.py --cuadros`, no se leen), instancias de otras corridas,
los datos del flujo que se muestra (el guion solo los nombra).

### Process

1. **Guion.** Del pedido a `guion.yaml`, con el molde de `CONTRATO.md`: formato, escenas con su superficie, sus pasos,
   su texto en pantalla y su locución, y qué tiene que existir antes (el escenario). Un video de difusión: 60-90 s y una
   idea por escena; uno de capacitación: los pasos en orden, sin cortes que escondan un paso. Con un agente: un prompt
   por escena y un hito por cada resultado que se quiere mostrar. Si aparecen personas o datos reales, `datos_reales:
   true` («Datos reales» del contrato) y el consentimiento por escrito antes de grabar.
2. **Escenario.** Que exista lo que el guion nombra: el agente abierto en su pestaña de herdr, la sesión iniciada en el
   navegador (`web.py iniciar <url>`, una vez, a mano), el celular emulado con su sesión, la terminal a la vista y
   «No molestar» de Windows prendido. Lo creado se anota en `00_estado.md` (nombres, nunca claves).
3. **Ensayo.** `python herramientas/tomar.py <instancia>/guion.yaml --sin-grabar`, o una primera toma grabada que se
   tira: con un agente, con el flujo en modo ensayo (sin aplicar cambios). Lo que salió distinto se corrige en el guion.
4. **Toma.** `python herramientas/tomar.py <instancia>/guion.yaml --cuenta 5`: prepara la superficie, graba y corre los
   pasos; deja `toma_<n>.mkv`, `toma_<n>.marcas.json` y `toma_<n>.guion.yaml`. La grabación del escritorio es continua:
   si un paso falla, lo grabado hasta ahí queda. Una toma con un error se repite entera, o el tramo que falta con
   `--desde <escena>` en una toma nueva (la edición toma cada escena de la última toma que la tiene). Lo filmado con un
   celular se copia a `binarios/filmacion/` y entra como escena `filmacion`.
5. **Edición.** `python herramientas/editar.py <instancia>/guion.yaml --toma <n>` (`--toma 7 --toma 8` con una
   regrabación). Con datos reales: primero las zonas (`--muestras <n>`), después la versión de difusión y, aparte, la
   interna (`--interno`). Sale `video_<AAMMDD>_v<k>[_interno].mp4`. Los ritmos se ajustan en `edicion:` del guion.
6. **Locución.** Con el corte aprobado: `python herramientas/locutar.py <instancia>/guion.yaml --microfono "<nombre>"`
   en una terminal propia (`--listar` muestra los micrófonos). Graba escena por escena mirando el texto y la duración;
   después se arma de nuevo (paso 5) y la voz entra sola.
7. **Entrega.** El video queda en la carpeta de binarios, anotado en `00_binarios.md`, y `00_estado.md` dice cuál es la
   versión aprobada de cada tipo. Mandarlo o publicarlo es una salida al mundo real y no es parte de este workflow.

### Outputs

- `guion.yaml`, `00_estado.md` y `00_binarios.md` → en la instancia (texto, en git).
- Tomas, marcas, filmaciones, voces y videos → en la carpeta de binarios de la instancia.

### Chequeo

- **Paso 1, humano:** quien pidió el video lee el guion y contesta: «¿estas escenas, en este orden, cuentan lo que quiero
  que vea esta persona?».
- **Paso 2, humano:** OK antes de crear algo en un sistema de verdad (una cuenta, una base, una dirección).
- **Paso 5, humano:** mira el video entero contra el texto de cada escena; los cambios van al guion. Con datos reales,
  además revisa la versión de difusión cuadro por cuadro (`--cuadros --cada 0.5`) y ningún dato se lee.
- **Paso 6, humano:** escucha el video con voz entero.
- Pasos 3, 4 y 7: ninguno (la toma la juzga el paso 5; la entrega solo ordena).

### Punto de commit

Guion aprobado (fin del paso 1) y video aprobado (fin del paso 6): los archivos de texto de la instancia, con rutas
explícitas. Los binarios, nunca.

## Instalar en una PC

- Python 3.11+ con `pyyaml`, `numpy` y `Pillow`; para la superficie web, `playwright` (con Chrome) y `websocket-client`.
- ffmpeg (`winget install Gyan.FFmpeg`), con ffprobe y ffplay.
- Escritorio y terminal: Windows y, para la terminal, herdr.
- Celular emulado: Android SDK (cmdline-tools, platform-tools y emulator) en `<raíz>/sdk`, la imagen
  `system-images;android-35;google_apis_playstore;x86_64`, scrcpy (`winget install Genymobile.scrcpy`) y las APK en
  `<raíz>/descargas/apk` (ADBKeyboard para escribir con tildes). `python herramientas/superficies/android.py instalar`
  crea el celular y las instala.
- Una raíz propia por instalación: `VIDEOS_RAIZ` (`CONTRATO.md`, «Entorno»).

## Cómo se comprueba

- `bash scripts/test.sh`: guion, marcas, segmentos, zonas, panel de herdr y un armado entero con una toma inventada
  (difusión contra interna, filmación acelerada y voz que alarga una escena). Necesita ffmpeg para el armado.
- A mano, en la PC que va a grabar: una toma corta de una consola en una pestaña de herdr (`terminal.escribir`,
  `terminal.enviar`, `terminal.esperar` con `hasta`), su edición en las dos versiones y `--cuadros`: la zona tapada no
  se lee en la de difusión y sí en la interna.
