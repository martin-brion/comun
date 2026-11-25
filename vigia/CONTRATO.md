# Contrato de vigia

Versión del contrato: 0.5.0-dev.1. Lo que sigue no cambia sin decirlo en el pedido antes de cortar.

## 1. Reglas que no se negocian

1. **Observar no escribe.** `correr` no ejecuta ninguna escritura en un servidor: los únicos comandos son los de
   `foto.py` (listar e inspeccionar contenedores e imágenes, consultar paquetes, leer cinco archivos del sistema), el
   escáner y los comandos de lectura que la instancia declara en `repos` (el commit local de un repositorio).
2. **Desconocido no es bueno.** Una foto ausente, vencida o ilegible, una fuente caída o un escaneo cortado dejan el
   dato en «desconocido». No cierran ni descartan candidatas y no se informan como «al día» ni como «cero».
3. **La foto es de otra máquina.** Se reconstruye campo por campo, con tipos y tamaños fijos. Un campo de más no pasa;
   un texto fuera de forma frena la foto entera.
4. **Nada propio sale.** Una imagen cuyo nombre empieza con un prefijo de `propias` no se consulta en ninguna fuente.
   Hacia afuera solo va el nombre público de una imagen, por https y sin credenciales. Un registro que no está en
   `fuentes.registros` no se consulta, y una consulta a un registro solo va a ese registro y a los servidores que la
   instancia aprobó en `fuentes.destinos` (el de las fichas de acceso, el almacén de las capas): ni la paginación, ni
   la ficha, ni una redirección llevan a otro.
5. **El nivel de fábrica es `nunca`.** Una pieza admite aplicación automática solo si la instancia lo declara. Un salto
   mayor queda siempre afuera, cualquiera sea el nivel.
6. **Sin maduración no pasa.** Una versión sin fecha de publicación conocida espera.
7. **Un KEV no es una alarma por sí solo.** Acorta el plazo y abre el carril de urgencia solo con aplicabilidad
   verificada: el escáner lo encuentra en la imagen que corre, con una versión que lo arregla, y la pieza está declarada
   `expuesto`. Si no, cuenta como una vulnerabilidad alta. Un KEV sin arreglo en algo expuesto es un incidente.
8. **Una corrida a la vez.** El candado es un bloqueo del sistema operativo que tiene el proceso mientras vive: si
   muere (un corte, el reinicio que él mismo pidió), el sistema lo suelta. Ni la antigüedad ni el contenido del archivo
   deciden nada.
9. **El ejecutor no improvisa.** Solo llama acciones con nombre de la lista cerrada de §6, definidas por la instancia como
   comandos en lista. Nunca arma una línea de shell. Los valores de los huecos se validan contra un patrón fijo, de punta
   a punta (sin saltos de línea ni caracteres de control); la foto, igual. Si una acción pasa sus argumentos por ssh,
   el ssh los vuelve a leer como una línea: la instancia los pasa como datos (por ejemplo, un comando forzado que solo
   lee sus argumentos), nunca a un shell.
10. **La intención va antes que el efecto.** Cada acción que cambia algo deja su entrada en el diario antes de correr, y
    su resultado después.
11. **Que el comando salga bien no alcanza.** Una aplicación vale cuando el servicio corre con la huella pedida, está
    sano y pasó `verificar`.
12. **Un corte no se resuelve repitiendo.** Cada intento sobre una candidata queda abierto, aparte del diario, desde
    antes de su primer paso hasta que termina: un corte ENTRE dos pasos también se encuentra. Lo que quedó a medias se
    mide: si corre sano lo anterior en todos sus servicios y verifica, quedó como estaba; si corre sano lo nuevo, se lo
    **verifica** (`verificar`) y recién entonces está hecho; si no, se escala y en ese servidor no se aplica nada más hasta que una
    persona diga que está resuelto (`resuelto`). Ese bloqueo sobrevive entre pasadas.
13. **Sin vuelta atrás exacta no se aplica.** Hace falta la referencia con huella de lo que corre hoy.
    Una pieza con datos, además, se copia antes bajo una marca de ese intento, y solo esa copia se repone.
14. **El estreno es con el dueño.** La primera aplicación de cada tipo de acción pide `--presente`. Un estreno que falla
    no cuenta. Un estreno listo y sin hacer se avisa, una vez.
15. **Nada por silencio.** Sin ventana definida, sin tipo de acción habilitado o con el archivo `PAUSA`, no aplica.
    Una candidata escalada espera a una persona.
16. **Nada trabado en silencio, nada de más.** A una persona se la llama solo por lo que el vigía no puede resolver
    solo (§7). Lo que se arregla solo no se avisa; lo que dura, sí. Un incidente avisado avisa también su cierre.
17. **La urgencia no saltea compuertas: solo la espera.** Una candidata urgente no espera la maduración y entra en la
    ventana de urgencia; todas las demás compuertas valen igual, y además el escáner tiene que comprobar que la versión
    propuesta ya no tiene esa falla (también si ya maduró). La propuesta urgente es la versión más nueva del paso, no la
    madura. Solo para una imagen pública con el tipo `imagen` o `datos`, y con la lista KEV leída.
18. **El reinicio no tiene vuelta atrás.** Va último en la pasada, nunca después de una falla de esa pasada, solo con el
    arranque medible, y si algo no vuelve escala.

## 2. La foto (`esquema: 1`)

```
{"esquema": 1, "tomada": <segundos>,
 "sistema": {"id", "version", "nombre", "kernel_en_uso", "kernels": [..] | null, "reinicio_pedido": bool,
             "paquetes": [{"nombre", "de", "a", "origen", "seguridad": bool}] | null, "listas_al": <segundos> | null,
             "desatendidas": bool | null, "arrancado": <segundos> | null},
 "docker": {"version": texto | null},
 "contenedores": [{"nombre", "imagen_ref", "imagen_id", "creado", "estado", "salud", "proyecto", "servicio"}] | null,
 "imagenes": {"<imagen_id>": {"tags": [..], "digests": [..], "creada", "etiquetas": {..}}},
 "errores": [texto]}
```

`null` es «no se pudo medir». `arrancado` es `btime` de `/proc/stat` y se suma en 0.5.0 sin cambiar el esquema: una foto
sin ese campo vale igual (el reinicio queda sin poder medirse). De los rótulos de una imagen solo viajan cuatro: nombre y huella de la base, versión y
revisión. No viajan variables de entorno, montajes, comandos ni configuración de los servicios.

## 3. Una pieza y su candidata

Una pieza es una imagen distinta que corre en un servidor. Estados: `al día`, `atrasada`, `propia`, `base renovada`,
`desconocido`. Un contenedor creado desde el ID de su imagen (`sha256:…` o su forma corta) no se consulta como si fuera un
repositorio: se usa una etiqueta local de esa imagen, una propia antes que cualquier otra, y sin ninguna queda «sin
referencia».

**El sistema** de cada servidor es una pieza más, `sistema`, siempre la última de la lista: `al día`, `reinicio pedido`
o `desconocido`. Los paquetes los instala la rutina desatendida de la distribución; lo que le queda al vigía es el
reinicio que esa rutina pide (`/var/run/reboot-required`, o un kernel más nuevo instalado sin usar). Su candidata propone
el kernel más nuevo, o `<kernel> reiniciado`; no tiene maduración (lo instalado ya está instalado) y su plazo es el de una
vulnerabilidad alta. Su nivel y su tipo de acción salen del componente `sistema` de la configuración.

La candidata es el paso más chico disponible dentro de la misma familia de etiquetas (mismo prefijo, misma variante,
misma cantidad de números; las preliminares no cuentan) y, dentro de ese paso, la versión más nueva que ya maduró (si
ninguna maduró, la más nueva): si salen parches seguido, uno maduro no espera para siempre al siguiente. Los pasos más
grandes se informan aparte, sin proponerlos. Si una etiqueta flotante apunta hoy a otra imagen y no se sabe qué versión
corre, la candidata queda `fuera`: no se puede medir el salto. Una candidata que reemplaza a otra abierta de la misma
pieza hereda su fecha de detección: el plazo no vuelve a empezar.

**La fecha de publicación** es la del índice público, o, en otro registro OCI, la más tardía entre la fecha de armado de
la imagen y la primera vez que el vigía vio esa huella: un registro solo da la fecha de armado, que puede ser muy
anterior a la de publicación. Las fechas ya conocidas de corridas anteriores cuentan al elegir la versión madura.

**Una imagen propia** no se consulta afuera: se consulta su base. Si la imagen dice en sus rótulos sobre qué base se armó,
con nombre y huella (`org.opencontainers.image.base.name` y `org.opencontainers.image.base.digest`), y esa etiqueta de la
base apunta hoy a otra huella, la candidata es rehacer la misma imagen sobre la base nueva: `salto` `patch`, `actual` y
`propuesta` dicen `base <12 primeros de la huella>`, `referencia` es la base nueva con huella y `anterior`, la base de hoy
con huella; `version`, el rótulo `org.opencontainers.image.version` de la imagen que corre. La pieza propia informa
`base` con la que corre, y la candidata se cierra cuando todas las imágenes con ese nombre corren sobre la nueva o sobre
su base al día. Sin la huella en los rótulos solo se avisa que la base se renovó, por fecha: no se propone rehacerla.
Si la configuración declara una `base` y los rótulos nombran otra, no se propone nada: lo aprobado es lo declarado. Una
base que es otra imagen propia no se consulta afuera. Con la fuente de la base caída, la pieza queda «desconocido».

```
{"id", "servidor", "pieza", "actual", "propuesta", "salto": "patch" | "minor" | "mayor", "publicada", "madura",
 "nivel", "expuesto", "compuerta_version", "nota", "detectada", "plazo": {"clase", "vence"},
 "indice", "referencia", "anterior"}
```

`referencia` es la imagen propuesta fijada por huella (`registro/repo:versión@sha256:…`); `anterior`, la que corre hoy,
también con huella. Sin alguna de las dos el ejecutor no aplica.

`id` es estable: sale del servidor, la pieza, la variante y la versión propuesta. `urgencia` (opcional) lista los KEV con
arreglo que el escáner encontró en la imagen que corre, si la pieza está declarada `expuesto` y no es propia. Un informe
del escáner con cualquier parte ilegible es «desconocido» entero: nunca cuenta como limpio (`Vulnerabilities` ausente o
`null` es «sin hallazgos» en ese objetivo; cualquier otra cosa que no sea una lista, no). `compuerta_version` vale `pasaría`,
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
  También uno por noticia (un aviso de una sola vez, sin nada que cerrar).
- `<estado_dir>/avisos/<incidente>.cerrado.txt`: el cierre de un incidente que se había avisado. Al escribirlo se retira
  el archivo abierto; el comando de aviso lo recibe igual que los otros.
- `<estado_dir>/latido.json`: la hora de la última corrida (`corrida`) y de la última pasada del ejecutor (`ejecutor`).
- `<estado_dir>/vigia.sqlite3`: el estado, el diario y los incidentes. No se edita a mano.
- `<estado_dir>/PAUSA`: si existe, el ejecutor no hace nada. Lo crea y lo borra una persona.

Códigos de salida de `python -m vigia`: 0 bien, 1 sin corrida previa (o, en `resuelto`, sin ese incidente abierto), 2
configuración inválida, 3 otra corrida en curso, 4 (solo `salud`) el vigía no corrió en las últimas `--horas` (26 de
fábrica), el ejecutor no dejó su latido (en `--horas` si hay ventana de urgencia; si no, en una semana más esas horas) o
está en pausa de más. `salud` es para un monitor que vive fuera de lo que el vigía vigila: el vigía no puede avisar que
está muerto. Solo lee.

`python -m vigia resuelto --config … <incidente>`: una persona dice que un incidente está resuelto. Se cierra (y se
avisa su cierre), se levanta el bloqueo del servidor y la candidata escalada vuelve a quedar a la vista.

## 6. El ejecutor

**Tipos de acción.**

- `imagen`: reemplaza la imagen de un servicio de compose por otra fijada por huella. Su vuelta atrás es la imagen
  anterior, así que **no vale para una pieza que migra datos al actualizarse**.
- `datos`: lo mismo, para una pieza que guarda datos. Antes de aplicar llama `respaldar_datos` con una marca propia de
  ese intento. Si la versión nueva falla, llama `restaurar_datos` con esa misma marca y recién después `volver_imagen`:
  la imagen anterior nunca arranca sobre datos que la nueva ya migró. Cada tipo tiene su propio estreno.
- `propia`: rehace una imagen propia, igual, sobre la versión nueva de su base (§3). Primero `construir_propia` la arma y la
  prueba aislada, sin tocar lo que corre; recién después `aplicar_propia` la pone a correr. Lo que se mide es la huella de
  la base en los rótulos de la imagen con que corre el servicio. Su vuelta atrás es la imagen armada sobre la base
  anterior, que la instancia conserva: `volver_propia`. Como no cambia el código, no migra datos. Solo vale para una pieza
  propia, y una pieza propia solo admite este tipo: con otro se pondría a correr la imagen base sola.

- `reinicio`: reinicia el servidor cuando su sistema lo pide (§3). Antes mide cuándo arrancó y qué servicios de compose
  corren sanos; llama `reiniciar`; espera hasta `espera_reinicio_s` a que el servidor arranque de nuevo (el arranque
  medido cambia); espera hasta `espera_salud_s` a que vuelvan a correr sanos todos los que corrían; y llama
  `verificar_servidor`. **No tiene vuelta atrás**: si no arranca, no responde o no vuelve todo, escala. Si no llegó a
  reiniciar (sigue arriba con el mismo arranque), nada cambió y cuenta como un intento fallido. Solo vale para la pieza
  `sistema`, y la pieza `sistema` solo admite este tipo.

El respaldo es de la instancia. El vigía pide por nombre (¿hay uno vigente?, copiá esto, reponé esto) y no sabe qué
sistema responde: cambiar de servicio de respaldo es cambiar los comandos de `acciones`.

**Acciones con nombre** (por servidor, en `acciones`):

| Acción | Huecos | Contrato |
|---|---|---|
| `respaldo_vigente` | — | Cero solo si hay un respaldo verificado y vigente. No hace el respaldo. |
| `aplicar_imagen` | `{servicio}`, `{referencia}` | Deja el servicio corriendo con esa imagen. Repetirla con los mismos valores no cambia nada. |
| `verificar` | `{servicio}` | Cero solo si el servicio funciona de verdad. |
| `volver_imagen` | `{servicio}`, `{referencia}` | Deja el servicio corriendo con la imagen anterior. |
| `respaldar_datos` | `{servicio}`, `{marca}` | Detiene el servicio, copia sus datos bajo esa marca, comprueba la copia y lo deja **detenido**. Cero solo con la copia comprobada. |
| `restaurar_datos` | `{servicio}`, `{marca}` | Detiene el servicio, repone los datos de esa marca y lo deja **detenido**. Cero solo si quedaron repuestos. Sin esa marca, falla: nunca repone otra copia. |
| `construir_propia` | `{servicio}`, `{base}` | Arma la imagen de ese servicio, la misma versión que corre, sobre esa base, con su nombre y su huella en los rótulos, y la prueba aislada (sin red ni datos reales). Cero solo si quedó armada y pasó su prueba. No cambia lo que corre. Conserva la imagen que corre hoy. Repetirla con los mismos valores no cambia nada. |
| `aplicar_propia` | `{servicio}`, `{base}` | Deja el servicio corriendo con la imagen armada sobre esa base. Sin esa imagen armada, falla. |
| `volver_propia` | `{servicio}`, `{base}` | Deja el servicio corriendo con la imagen armada sobre esa base anterior, la que conservó. |
| `reiniciar` | — | Reinicia el servidor. Puede no volver (corta la conexión o al propio vigía): el vigía no mira su salida sino el arranque. |
| `verificar_servidor` | — | Cero solo si el servidor quedó bien después de arrancar: lo que la instancia sepa que un reinicio puede romper (temporizadores sin próxima corrida, montajes, servicios fuera de compose). No cambia nada. |

Un intento, sus incidentes y el bloqueo del servidor se escriben antes de dar el intento por cerrado; escalar (candidata,
incidente y bloqueo) y `resuelto` (incidente, bloqueo y candidata) son cada uno una sola transacción, y `resuelto` se
puede repetir para completar uno que un corte dejó a medias. Lo resuelto tras un corte cuenta en el tope de su ventana y,
si fue por urgencia, deja su noticia. El latido del ejecutor lo deja solo una pasada que terminó sin error.

Toda acción que recibe `{servicio}` puede recibir además `{proyecto}`, el proyecto de compose
(`^[a-z0-9][a-z0-9_-]{0,62}$`). Si todas las acciones de servicio de un tipo lo usan, dos proyectos pueden tener un
servicio con el mismo nombre; si no, el nombre del servicio tiene que ser único en todo el servidor.

`{servicio}` es el nombre del servicio de compose (`^[a-z0-9][a-z0-9_.-]{0,62}$`); `{referencia}` y `{base}`, una imagen
con huella; `{marca}`, un identificador que pone el vigía (`^m[0-9]{9,12}$`). Una acción tiene 15 minutos, y `construir_propia` una
hora; pasado ese tiempo cuenta como falla. `reiniciar` tiene cinco minutos para salir, pero lo que vale es el arranque medido.

Con `datos`, lo que el servicio haya escrito entre la copia y la restauración se pierde: por eso la copia se toma con el
servicio detenido y justo antes. Retirar copias viejas es de la instancia, y nunca la de un intento que quedó a medias.

**Orden de un intento con `datos`.** `respaldar_datos` → `aplicar_imagen` → sano con la huella pedida → `verificar`. Si la
copia falla, no aplica: `volver_imagen` levanta lo que había. Si falla después, `restaurar_datos` → `volver_imagen` →
sano → `verificar`. Si la restauración falla, no levanta la imagen anterior: escala y frena la ventana.

**Orden de un intento con `propia`.** `construir_propia` → `aplicar_propia` → sano, sobre la base pedida y en la misma
versión → `verificar`. La instancia pone en el rótulo de la base exactamente la huella que recibió en `{base}`.
Si el armado falla, no se tocó nada: queda como estaba y cuenta como un intento fallido. Si falla después,
`volver_propia` → sano sobre la base anterior → `verificar`.

**Tras un corte.** Un reinicio a medias se resuelve midiendo el arranque: si cambió, volvió todo lo que corría y
`verificar_servidor` da cero, está hecho (y cuenta como estreno si lo era); si no cambió pasado `espera_reinicio_s`, no se
aplicó; mientras el servidor recién arranca, espera; si arrancó y no volvió todo, escala. Para que esto pase cuando el
vigía corre en el mismo servidor, la instancia corre una pasada de `aplicar` al arrancar. Un armado a medias con el servicio sobre la base de siempre es «no aplicado». Una copia a medias con el servicio corriendo lo de siempre es «no aplicado». Una copia a medias con el
servicio detenido, o una restauración a medias, es ambiguo: no se mide cómo quedaron los datos desde afuera. El aviso
dice la marca de la copia de ese intento.

**Compuertas**, en este orden; la primera que frena deja la candidata «bloqueada» y el acta dice cuál:

1. Versión: `pasaría`.
2. Tipo de acción: la pieza declara uno y está en `playbooks`.
3. Acciones: el servidor define todas las del tipo.
4. Estreno: el tipo ya se aplicó bien una vez, o la pasada es `--presente`.
5. Imagen fija: se conoce la huella de la versión propuesta.
6. Vuelta atrás: se conoce la huella de la imagen de hoy.
7. Servicios: cada contenedor que usa la imagen es un servicio de compose que se puede nombrar sin ambigüedad.
8. Sin escalar.
9. Arreglo comprobado, solo si la candidata no maduró y va por urgencia: el escáner revisa la versión propuesta (por su
   referencia con huella) y ya no encuentra esos KEV. Sin escáner local, no pasa.
10. Respaldo: `respaldo_vigente` salió con cero. Va última porque es la única que corre un comando del servidor.

El tipo `reinicio` cambia 5 a 7 por **arranque medible**: la foto dice cuándo arrancó el servidor.

Además: la ventana (o `--presente`), un servidor por día de ventana, el tope `max_por_ventana`, el archivo `PAUSA` y,
para el reinicio, que nada haya fallado antes en la misma pasada (va último).

**Dos ventanas.** La ordinaria (`ventana`) toma todas las candidatas. La de urgencia (`ventana_urgente`, todos los días de
fábrica) toma solo las urgentes (§3), en el servidor que ya la tomó ese día o, si no, en el que tiene la urgencia de plazo
más corto. Cada una lleva su propio tope. Cada candidata que entró sin esperar la maduración deja una noticia.

**Varios servicios con la misma imagen.** Se aplican de a uno. Si uno falla y quedó como estaba, los que ya habían
cambiado vuelven también; con `datos` eso no se hace a ciegas: se escala. Una candidata se intenta una sola vez por pasada.

**Resultado de una candidata.** `aplicada`; `falló; quedó como estaba` (a la segunda se escala y se avisa); o `falló y la
vuelta atrás también` (se escala, se avisa y la ventana se frena ahí).

**El aviso.** Cada incidente deja su archivo (§5) y, si la instancia definió `aviso`, se le pasa ese archivo a ese
comando. Por dónde llega a una persona (un chat, un mail) lo decide la instancia: el canal no puede depender de lo que
el vigía actualiza.

## 7. Incidentes: cuándo se llama a una persona

Un aviso por incidente, un recordatorio por día mientras siga abierto y uno cuando se cierra. Nunca un aviso de éxito,
salvo la noticia de una aplicación por urgencia. Un aviso cuenta como dado recién cuando el comando de aviso sale con
cero; si no, se reintenta en la pasada siguiente. Lo que no se pudo medir no abre ni cierra un incidente.

| Incidente | Se abre | Se cierra |
|---|---|---|
| `roto-<candidata>` | vuelta atrás fallida, algo a medias con datos, o un reinicio que no volvió bien. Bloquea el servidor | solo `resuelto`: ver la versión no prueba que funcione ni que hayan vuelto todos los servicios |
| `ambiguo-<candidata>` | un corte dejó algo que no se puede acreditar. Bloquea el servidor | solo `resuelto` |
| `fallo-<candidata>` | una candidata no entró en dos intentos | la candidata se cierra o se descarta |
| `plazo-<candidata>` | se venció el plazo de la candidata | la candidata se cierra |
| `estreno-<tipo>` | una candidata frena solo por el estreno | el tipo se estrena |
| `trabada-<candidata>` | dos ventanas seguidas frena en la misma compuerta de persona (acciones, imagen fija, vuelta atrás, servicios, arranque medible) | pasa esa compuerta o la candidata se cierra |
| `respaldo:<servidor>` | dos ventanas seguidas sin respaldo vigente | `respaldo_vigente` vuelve a dar cero |
| `sin-medir-<servidor>` | más de un día sin poder medir el servidor o listar sus contenedores | se lo vuelve a medir |
| `escaner-<servidor>` | con escáner configurado, más de un día sin resultado para alguna imagen | da resultado para todas |
| `fuentes-<servidor>` | más de tres días con piezas públicas que no se pueden comparar con lo publicado (salvo las que la configuración deja sin fuente) | se las vuelve a comparar |
| `fuente-kev` | más de tres días sin poder leer la lista KEV | se la vuelve a leer |
| `sistema-desatendidas-<servidor>` | la rutina desatendida del sistema está apagada | se prende |
| `sistema-seguridad-<servidor>` | un mismo parche de seguridad del sistema (paquete y versión) pendiente por más de `sistema_plazo_h` | ya no queda ninguno con esa antigüedad |
| `kev:<servidor>:<pieza>` | una pieza expuesta tiene un KEV sin arreglo publicado | un escaneo válido y la lista KEV leída dicen que ya no, o la pieza dejó de correr |
| `kevimg:<servidor>:<pieza>` | una pieza expuesta tiene un KEV con arreglo pero no hay imagen nueva que aplicar, por más que el plazo `kev_expuesto` | aparece la candidata, o lo anterior |
| `pausa` | el archivo `PAUSA` tiene más de `pausa_max_dias` | se borra |
| `maquinas-censo`, `maquina-…` | el censo de máquinas no se puede leer; una esperada falta o está detenida; una corre sin que la lea un servidor del vigía y sin estar excluida a propósito (`sin_vigia`) | deja de pasar |

## 8. Lo que solo informa

- **Censo de máquinas virtuales** (`maquinas`): un archivo que deja la instancia, `{"tomada", "maquinas": [{"nombre",
  "estado"}]}`, con `estado` en `corriendo`, `apagada` u otro; se compara con las esperadas (`{servidor, apagada,
  sin_vigia}`). No enciende ni apaga nada.
- **Repositorios montados** (`repos`): el commit local (un comando de lectura de la instancia) contra la punta de su rama
  en el remoto, por https y el protocolo de git, sin credenciales. `al día`, `distinto del remoto` o `desconocido`. No
  propone nada: un addon se actualiza por su carril.
