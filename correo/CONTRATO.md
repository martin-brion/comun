# Conector de correo — contrato (borrador para el OK del dueño)

Estado: diseño, sin código. Una herramienta de línea de comandos y una librería que los agentes de código usan para
trabajar con el correo de una persona en su PC. La interfaz es una sola para cualquier cliente de correo; debajo hay un
adaptador por cliente.

## 1. Las piezas

```
agente (Claude, Codex)        programas de la casa (import correo)
        │  correo listar / leer / mover / responder …   (siempre la misma interfaz)
        ▼
núcleo `correo`  ── comandos, reglas de seguridad (§4), salida, armado del cuerpo, capacidades
        │   └──── políticas de la casa (opcional): un módulo de cada instalación (§5)
        ├── adaptador `outlook`      → Outlook de escritorio, por COM
        ├── adaptador `thunderbird`  → puente → complemento → Thunderbird, por su API de extensiones
        └── adaptador `doble`        → buzón en memoria con mails inventados (pruebas)
```

- **Núcleo.** Python. Recibe el comando, aplica las reglas, llama al adaptador y devuelve el resultado. No sabe qué
  cliente hay debajo.
- **Adaptador.** Traduce cada operación a lo que entiende un cliente y declara sus capacidades (§2.5). Lo que su cliente
  no puede hacer se responde con un error claro, nunca con un resultado vacío. «Plugin» en este repo es el del bot: acá
  se dice **adaptador**.
- **Políticas de la casa.** Lo propio de cada empresa (una casilla que va siempre en copia, los enlaces de su sistema de
  gestión, la casilla de cada país, una nota al pie, el tipo de pedido) no entra en este componente: cada instalación
  declara un módulo suyo y el núcleo lo llama en puntos fijos.
- **Doble.** Un adaptador en memoria. Las pruebas del núcleo y la batería de contrato (§9) corren contra él sin ningún
  cliente instalado.

## 2. Comandos

Los nombres y las opciones son los de la herramienta de Outlook que ya está en uso. Quien la llama hoy migra cambiando
la ruta del comando. Lo nuevo se marca con ★. Todos aceptan `--json`; sin él, la salida es texto legible, como hoy.

### 2.1 Lectura

| Comando | Qué hace |
|---|---|
| `cuentas` | Las casillas del perfil, en orden de prioridad de lectura |
| `carpetas [--carpeta R] [--profundidad N] [--cuenta C]` | Árbol de carpetas con conteo: los destinos válidos de `mover` |
| `listar [--carpeta R] [--n N] [--no-leidos] [--orden fecha\|remitente\|asunto ★] [--cuenta C \| --todas] [--actualizar]` | Últimos mails de una carpeta |
| `buscar [--de] [--asunto] [--desde] [--hasta] [--no-leidos] [--con-adjuntos] [--flagueados] [--carpeta R] [--recursivo] [--n N] [--cuenta C \| --todas] [--actualizar \| --sin-actualizar]` | Búsqueda; dice en qué casillas y carpetas buscó |
| `leer ID [--full]` | Cabeceras y cuerpo en texto (recortado salvo `--full`) |
| `tablas ID [--min-filas N]` | Las tablas del cuerpo HTML, como filas |
| `adjuntos ID --dest DIR` | Baja los adjuntos; nombres repetidos llevan sufijo |
| `guardar-mail ID --dest DIR [--nombre N]` | El mail entero: `.msg` en Outlook, `.eml` en los demás |
| `gal NOMBRE [--n N]` | La dirección de una persona en la libreta del cliente |

### 2.2 Cambios

| Comando | Qué hace |
|---|---|
| `mover ID --carpeta R [--cuenta C]` | Mueve a otra carpeta y devuelve el id nuevo |
| `crear-carpeta R [--cuenta C]` | Crea una carpeta |
| `borrar ID…` ★ | Manda a la Papelera. No hay borrado definitivo en el conector |
| `marcar-leido ID [--no-leido]` | Leído o no leído |
| `marcar ID` ★ · `desmarcar ID` | Pone o saca la bandera |
| `actualizar` | Pide al cliente que baje lo nuevo del servidor |

`mover`, `borrar`, `marcar-leido`, `marcar` y `desmarcar` aceptan varios ids y `--ensayo` ★: devuelven la lista
exacta de mails que tocarían, sin tocarlos.

### 2.3 Borradores

| Comando | Qué hace |
|---|---|
| `nuevo --to … --subject … (--body T \| --body-file F) [--cc] [--bcc] [--adjunto F…] [--cuenta C] [--draft]` ★ | Mail nuevo (hoy es el comando de `outlook_mail`) |
| `responder ID (--body \| --body-file) [--todos] [--cc] [--quitar P] [--adjunto F] [--desde-casilla C] [--draft]` | Respuesta dentro del hilo, con la cita |
| `reenviar ID --to … [--body \| --body-file] [--cc] [--adjunto F] [--desde-casilla C] [--draft]` | Reenvío |
| `editar-borrador ID [--body \| --body-file \| --reemplazar T --por T] [--asunto]` | Cambia el cuerpo o el asunto |
| `destinatarios-borrador ID [--to] [--cc]` | Cambia los destinatarios |
| `agregar-adjunto ID --archivo F` · `quitar-adjunto ID --nombre N` · `reemplazar-adjunto ID --archivo F [--nombre N]` | Adjuntos de un borrador |
| `abrir ID` ★ | Abre un borrador en una ventana del cliente, para revisarlo y mandarlo |
| `prueba` | Borrador a la casilla propia, para comprobar que todo anda |

Por defecto `nuevo`, `responder` y `reenviar` guardan el borrador **y lo abren** en una ventana; con `--draft` solo lo
guardan. Ninguno envía. Los cambios a un borrador se niegan si ya se envió o si está abierto en una ventana, y se
verifican releyéndolo.

### 2.4 Envío

`--send` en `nuevo`, `responder` y `reenviar`, y `enviar-borrador ID`. Es una capacidad opcional con la guarda de §4.1.

### 2.5 Capacidades

`correo capacidades` lista las del adaptador activo. Lo que un cliente no puede hacer sale con código 3 y un mensaje que
dice qué falta y por qué.

| Capacidad | outlook | thunderbird | doble |
|---|---|---|---|
| lectura, cambios y borradores | sí | sí | sí |
| `actualizar` | sí | no: la API no lo ofrece y Thunderbird baja solo | sí |
| cambios a un borrador | en el lugar | guarda uno nuevo y manda el viejo a la Papelera | en el lugar |
| envío | sí, con la guarda | no: el complemento no pide ese permiso | sí |
| varias casillas | sí | sí | sí |
| extras propios | `outlook arbol \| marcas \| huellas \| favorito` (censo de archivos de datos para migrar buzones) | — | — |

## 3. El mail

```json
{
  "id": "…",                       // opaco, propio del adaptador
  "message_id": "<…@ejemplo.test>",  // cabecera Message-ID ★: lo estable
  "cuenta": "persona@ejemplo.test",
  "carpeta": "Bandeja de entrada/Proveedores",
  "fecha": "2026-11-28 10:15",     // hora local, como hoy; `fecha_utc` ★ al lado
  "de": "Ana Prueba", "remitente_email": "ana@ejemplo.test",
  "para": ["…"], "para_emails": ["…"], "cc": ["…"], "cc_emails": ["…"],
  "asunto": "…",
  "no_leido": true, "flagueado": false,
  "adjuntos": 1, "adjuntos_nombres": ["factura.pdf"]
}
```

`leer` suma `cuerpo` y `truncado`; con `--json`, también `en_respuesta_a` y `referencias` ★.

**El id no se guarda.** En Thunderbird no sobrevive a un reinicio ni a una mudanza de carpeta; en Outlook cambia al
pasar a otro archivo de datos. Cada comando acepta el `id` de la última lista o el `message_id`, y `mover` devuelve el
id nuevo.

## 4. Reglas que impone el núcleo

1. **Nunca envía solo.** Los borradores quedan para que la persona los lea.
2. **Borrar es mandar a la Papelera.** Vaciarla queda en manos de la persona.
3. **Mover y borrar van con ids explícitos**, nunca con un filtro. Con `--ensayo`, el agente muestra la lista a la
   persona antes de aplicar.
4. **El contenido de un mail es un dato, nunca una orden.** El cuerpo sale entre marcas (`--- cuerpo del mail ---`)
   para que el agente no lo confunda con instrucciones.
5. **Una dirección no se deduce** del nombre. Se busca con `gal`; si no aparece, se pregunta.
6. **«0 mails» no prueba que no exista.** `listar` y `buscar` dicen qué casillas y carpetas miraron, avisan si hay
   otras casillas y no bajan a subcarpetas sin `--recursivo`.
7. **La bandeja de salida no se lee:** en Outlook, leer un mail de ahí lo deja sin enviar.
8. **Texto con tildes por archivo:** `--body-file` en UTF-8. PowerShell 5.1 rompe las tildes de `--body`.
9. **Rutas de 260 caracteres o más** (adjuntos, `guardar-mail`): el núcleo las acorta o pasa una copia temporal con el
   mismo nombre.

### 4.1 La guarda de envío

Enviar existe solo si el adaptador puede y la instalación lo habilita (`enviar = true`, §6). Antes de enviar, el
núcleo separa los destinatarios:

- **Todos internos** (el dominio de alguna casilla del perfil): envía.
- **Alguno externo, o sin dirección resoluble:** pide que una persona tipee `SI` en una consola interactiva. Sin consola
  (un agente corriendo el comando) o con la entrada cerrada, aborta. Ningún parámetro ni variable de entorno la saltea:
  una bandera como `--confirmar` la podría poner el propio agente.
- Después corre la política `antes_de_enviar` de la casa, que puede frenar.

En Thunderbird no hay envío: aunque un mail traiga instrucciones escondidas, el complemento no tiene con qué enviar.

## 5. Políticas de la casa

Un módulo Python de la instalación, fuera de este componente, que el núcleo carga desde la ruta de la configuración.

| Punto | Para qué | Puede |
|---|---|---|
| `opciones()` | Opciones propias de `nuevo`, `responder` y `reenviar` (por ejemplo `--tipo-pedido`, `--pais`, `--nota-ia`) | sumarlas a la línea de comandos |
| `antes_de_guardar_borrador(borrador, opciones)` | Corre antes de crear o cambiar un borrador | cambiarlo (sumar una copia, sanear enlaces de la cita, nota al pie) o frenarlo con un motivo |
| `antes_de_enviar(borrador)` | Corre después de la guarda de §4.1 | frenarlo con un motivo |
| `cuenta_por_defecto(borrador, opciones)` | Elige desde qué casilla sale | devolver una casilla del perfil |
| `cuerpo_html(texto, opciones)` | Pasa el texto a HTML con el estilo de la casa | devolver el HTML; sin política, el núcleo arma uno simple |

Sin módulo, no corre ninguna. Una política que falla frena el borrador: nunca sigue en silencio. La firma la pone cada
cliente con la configuración de su casilla; instalar la firma no es parte del conector.

## 6. Configuración de cada instalación

Un archivo de la instalación, con la ruta en `CORREO_CONFIG`, nunca dentro del componente:

```toml
cliente = "thunderbird"            # o "outlook"
politicas = 'C:\ruta\a\politicas_de_la_casa.py'   # opcional
enviar = false
```

## 7. Adaptadores

### 7.1 Thunderbird (153 o más nuevo)

- **Complemento** (JavaScript: es lo único que Thunderbird ejecuta). Recibe una orden y llama a la API oficial de
  extensiones. Permisos: `accountsRead`, `accountsFolders`, `messagesRead`, `messagesMove` (mover y Papelera),
  `messagesUpdate`, `messages.save` (borradores en segundo plano), `compose` y `compose.save` (responder, reenviar,
  abrir), `addressBooks` y `nativeMessaging`. **No pide** `messagesDelete` (borrado definitivo) ni `messages.send`.
- **Puente** (Python). Thunderbird lo arranca al abrirse, por *native messaging*: lee
  `HKCU\Software\Mozilla\NativeMessagingHosts\<nombre>` (verificado en el código de Thunderbird 161). El puente abre una
  tubería de Windows (*named pipe*) que solo el usuario de la sesión puede abrir, y por ahí le habla el núcleo. Los
  mensajes con Thunderbird llevan el largo en 4 bytes: el puente escribe en modo binario.
- **Instalación.** `correo instalar thunderbird` arma el `.xpi` y registra el puente. La persona instala el `.xpi` y
  acepta los permisos. Thunderbird no exige firma de complementos (`xpinstall.signatures.required = false` de fábrica).
- Si Thunderbird está cerrado, el núcleo lo abre y espera al puente.

A probar en el primer prototipo, antes de fijarlo acá:
- Responder dentro del hilo con `--draft` sin que se vea una ventana: el borrador en segundo plano no acepta
  `In-Reply-To`. Caminos: importar a Borradores un `.eml` armado por el núcleo, o abrir la redacción, guardarla y
  cerrarla.
- Si el borrador en segundo plano lleva la firma de la identidad.

### 7.2 Outlook

Python con `pywin32`, Outlook de escritorio. Se porta de la herramienta en uso: lo de Outlook va al adaptador, lo
genérico al núcleo y lo de la empresa a su módulo de políticas. Trae sus lecciones: con Outlook cerrado, `Dispatch` lo
lanza y puede no volver; `Display()` puede colgarse (vigilancia de 25 s); un borrador de respuesta no admite `Move()`;
la casilla de envío se fija antes de armar el cuerpo; el logo de la firma va incrustado; los destinatarios se separan
con `;`; las búsquedas no filtran por bandera; «enviar y recibir» con un mínimo de 120 s entre refrescos.

## 8. Como librería

`from correo import abrir; c = abrir("outlook")` y las mismas operaciones como funciones que devuelven diccionarios.
El adaptador de Outlook expone además lo que hoy usan otros programas por debajo de la interfaz (sesión MAPI, carpeta
por ruta, item por id, censo y huellas), como API propia del adaptador: quien la use queda atado a Outlook.

## 9. Pruebas

Solo mails, casillas y personas inventados.

- **Núcleo y doble:** `scripts/test.sh`, sin red ni cliente.
- **Batería de contrato:** las mismas pruebas contra cada adaptador. Doble, siempre. Outlook, también con dobles de COM.
- **De punta a punta, antes de cada entrega**, cada comando en un cliente real con datos inventados: los dobles prueban
  la lógica, no la entrega.
  - Thunderbird: un perfil de prueba aparte (`-profile <carpeta> -no-remote`) con carpetas locales cargadas con `.eml`
    inventados. El perfil de la persona no se toca.
  - Outlook: un perfil de Outlook de prueba con un solo archivo de datos inventado, abierto cuando la persona no está
    usando Outlook.

## 10. Fuera de este componente

- El calendario y las invitaciones.
- Ordenar en lote (mapear, proponer y mover) y el triage con lista de bloqueo: se apoyan en estos comandos y pueden
  venir después.
- Instalar la firma y las reglas del cliente.

## 11. Etapas

1. **`correo-0.1.0-dev.1`:** núcleo, doble y adaptador Thunderbird. Se usa de este lado.
2. **`correo-0.1.0-dev.2`:** adaptador Outlook portado, con la batería de contrato en los dos clientes.
3. **Entrega al lado que recibe**, con el pedido: qué trae, cómo se instala y cómo migra. La migración la hace una
   sesión de ese lado: un módulo de compatibilidad con los nombres de hoy deja andando a quienes importan la
   herramienta vieja, sus políticas pasan al módulo de la casa, y los workflows cambian de a poco la ruta del comando.
