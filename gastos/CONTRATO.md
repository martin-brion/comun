# gastos/1 — contrato de extracción (E0)

Estado E3: varias empresas, destinos, políticas, catálogo y recibos implementados.
La verificación efectiva de cada entrega consta en su manifiesto. Base del motor: v0.5.4,
commit 171a045b490fef702d390f117c340fbad6b6ad3a. No declara compatibilidad con
el motor sin los cambios de esta extracción. Ejemplos exclusivamente sintéticos.

## 1. Responsabilidades y carga

El motor presta mecanismos públicos. El paquete gastos contiene el circuito,
los contratos de imputación y política, la validación y sus pruebas. La capa
final aporta empresas, destinos y proveedores de extensiones. El código se carga
desde PLUGINS_DIR de solo lectura, nunca desde el KB. Exporta PLUGIN sin efectos
de registro al importar. Conserva nombre/grupo/trámite/consumidor gastos y prefijo
gasto_. El motor libera esas reservas y deja de importar gastos.

## 2. Extensiones genéricas del motor

API prevista en bot.plugins:
PuntoExtension(id, version, validar): contrato declarado por su consumidor.
Extension(punto, version, nombre, proveedor): implementación declarada por otro plugin.
Plugin suma puntos_extension y extensiones (tuplas vacías por defecto).
resolver_extension(punto, version, nombre): resuelve solo proveedores habilitados.
El validador del consumidor valida la forma del proveedor, no ejecuta efectos.

La carga registra declaraciones atómicamente por módulo. Después de cargar todos
los módulos se enlazan los contratos; no depende del orden de nombres. Clave única
(punto, version, nombre). Duplicados, contratos incompatibles y proveedores
inválidos no quedan disponibles y generan diagnóstico al owner. Una extensión
huérfana no ejecuta nada. Los registros participan del rollback existente. La
resolución vuelve a comprobar el perfil; un proveedor deshabilitado no es utilizable.
Una referencia configurada inválida falla cerrado; vacío significa ausencia
intencional. No se importa código por un nombre obtenido de JSON.

El plugin declara gastos.imputador/1 y gastos.politica/1. Sus tipos pertenecen al
paquete gastos, no al motor. Un proveedor declara su propio grupo de perfil.
Son código de confianza; el contexto de solo lectura no es un sandbox.

Imputador: async proponer(ctx, marco, lineas) -> resultado y async
revalidar(ctx, marco, lineas) -> None | str; nota opcional. Se conserva la forma
vigente: pendiente/opciones o lineas por número con distribucion, grupo, rotulo,
detalle, texto, estado. Distribuciones positivas suman 100, con claves enteras o
combinadas de 2 a 5 IDs distintos (0.1.0-dev.10, CONTRATO 9); agrupaciones distintas
tienen rótulos distintos y una agrupación no vacía exige rótulo. Estado JSON hasta
2 KiB. Recibe copias de líneas y marco.odoo limitado a lectura, ligado al destino.

Política: async evaluar(ctx, marco, lineas) -> {observaciones: [str], bloqueos:
[str], estado: dict}; async revalidar(ctx, marco, lineas, resultado) -> None | str.
Una excepción bloquea. Observaciones se muestran enteras; bloqueos impiden cargar.
Resultados, versión y proveedor integran la propuesta y su huella. Nunca se permite
que una política afloje identidad, confirmación, checksum o estado borrador.

## 3. Odoo nominal con destino

API prevista en bot.odoo: async llamar(ctx, modelo, metodo, args, kw, *,
destino=None). args es lista y kw es dict. Retorna el valor XML-RPC o error
clasificado sin credenciales ni cuerpo remoto sensible.

Destino explícito: {url, db, link_url, secreto_login, secreto_api_key}.
TODAS las claves son obligatorias, incluso los dos nombres de secretos. No hay
fallback de credenciales hacia otro destino. Nombres de secretos distintos para
cada destino declarado; no se acepta compartir el par entre destinos. Los valores
se resuelven en la bóveda nominal al llamar, nunca se persisten ni se registran.
URLs http/https con host, sin credenciales, query ni fragmento; link_url solo para
mostrar enlaces. db no vacío. Ningún argumento del modelo puede introducir URL,
base o nombres de secreto: solo una clave de destino validada en configuración.
None conserva el destino y los nombres globales del motor para consumidores
anteriores y configuración plana heredada.

Mantiene compuertas de sesión y acceso al servicio, timeout y controles aplicables
del broker. Revalida sesión después de la llamada. No altera globals. No incorpora
un permiso general de escritura: el plugin solo escribe por su camino confirmado.
La identidad autenticada se coteja con el vínculo nominal del destino; nunca se
compara a ciegas un uid de un ERP distinto con el uid global. Se define el comando
genérico atar-erp para valores nominales positivos; nunca recibe credenciales.
Identidades nuevas: odoo_ids_extra.usuarios_erp[clave_destino] = uid. Sin vínculo
nominal para un destino nuevo no se prepara ni carga. El uid global queda como
compatibilidad exclusiva del destino heredado.

## 4. APIs públicas auxiliares

Se declaran públicas y se prueban: lotes.registrar_consumidor, lote_ver y store;
salas_personales.registrar_tramite y de_persona; engine.estructurado e
imagen.normalizar. No se copian sus implementaciones.

Turno de lote: función pública async entregar_lote_a_turno(lote, texto), con
resolución de persona/canal/sala de origen, serialización mediante turnos.turno,
selección de sala y restauración del contexto aun ante fallo/cancelación. No se
expone matrix_adapter._sala_respuesta. El consumidor recibe error explícito si no
puede entregar; no se redirige material privado a otra sala.

## 5. Empresas, destinos y selección

gastos.json: {empresas: {casa_prueba: {company_id, zona_horaria,
diario: {ids: {ensayo: ID, principal: ID}}, etiqueta, compania, comprobante,
workflow, imputador, politica, destinos: {ensayo: DESTINO, principal: DESTINO}}}}.
Cada destino explícito exige el objeto completo del apartado 3. Los ejemplos de
host usan erp.example.test. No hay país fijo. Configuración plana sigue siendo
una sola empresa con destino heredado. Los ids de empresa y destino son slugs
[a-z][a-z0-9_]{0,40}. Claves desconocidas se rechazan con diagnóstico.

Empleados: odoo_ids_extra.empleados_gastos[empresa] = id. empleado_gastos es alias
solo para la empresa única heredada. Como los IDs no son portables entre ERPs,
se admiten empleados_gastos_destinos[empresa][destino] como overrides; el id siempre
se valida en el ERP elegido contra usuario, compañía y actividad. Ningún fallback
habilita un empleado cuyo vínculo nominal no coincida.

Una empresa accesible se infiere; varias exigen empresa. Más de un destino exige
destino explícito, salvo destino_por_defecto (0.1.0-dev.10, CONTRATO 9). La propuesta tiene empresa y destino inmutables. El mismo lote admite propuestas
sucesivas para distintos destinos, una vigente por destino, sin reenviar fotos.
Cada propuesta exige su propio OK L2 y conserva recibos independientes. La purga
se retiene mientras haya destinos declarados pendientes, hasta un plazo configurable.
Cambiar destino requiere propuesta nueva, IDs resueltos otra vez y OK nuevo. La
compañía es propia del destino: el destino admite company_id como override
validado. El diario va en la empresa por ID, uno por destino declarado, como
cuentas: el nombre de un diario se traduce con el idioma de Odoo y no sirve para
buscarlo. Un diario por texto, incompleto o dentro del destino se rechaza como
error de configuración. Se lee por ID y se valida tipo compras, compañía y activo.
Los errores de validar la configuración empiezan por «Configuración del bot
(gastos.json)» y dicen que no es un problema de Odoo.

Se rechazan alias de destino con igual identidad canónica (url normalizada, db)
dentro de la configuración; nombres diferentes no crean dos ERPs. No se reenvía
una corrida ya aplicada al mismo ERP mediante otro alias.

## 6. Persistencia y contrato para el addon

Se conservan DATA_DIR/gastos, lote L seguido de seis dígitos, prefijo gasto_,
grupo/trámite/consumidor gastos y marcador exacto [<lote>/F<nn>], por ejemplo
[L000001/F01]. El número es el de la foto principal, con al menos dos dígitos.
Gasto: nombre '<comercio> <marcador>'.
Hoja: 'Rendición <persona> <fecha mínima YYYYMMDD>< rótulo opcional> [<lote>] · <pago>'.
Los valores exactos de pago y agrupación se fijan en FORMATOS.md, extraídos de la
base. No se cambia el marcador para separar destinos: la separación es el ERP,
la compañía y la bitácora. En el mismo ERP no se duplica una corrida.
Adjunto principal: 'Comprobante <marcador>.jpg'. Otro respaldo:
'Respaldo <marcador> foto <nn>.jpg'. SHA-256 verifica originales; el checksum Odoo
SHA-1 del JPEG normalizado verifica el adjunto contra el contenido enviado.

Propuesta nueva versionada: empresa, identidad canónica de destino, revisión de
configuración, usuario/empleado/compañía/diario resueltos, proveedores y contratos,
resultado de políticas, líneas y checksums. Secretos nunca entran en la huella.
Recibos separados por lote, empresa y ERP canónico; escribir propuesta nueva no
borra recibos anteriores. Los estados antiguos sin destino se interpretan solo
como destino heredado, conservando archivos y huellas pendientes. Si no se puede
establecer ese destino unívocamente se bloquea y se pide revisión, sin escritura.
Un OK existente nunca autoriza una propuesta nueva ni otro destino.

Preparar solo lee. Cargar exige L2 y la huella mostrada, revalida identidad,
configuración, imputación y política. Conserva reintentos idempotentes, varias fotos,
fotos excluidas, hojas por pago y agrupación, impuestos vacíos por defecto (salvo la
factura estándar con impuestos del apartado 9) y todo en borrador. Nunca envía, aprueba ni contabiliza. Los fallos parciales conservan
recibos suficientes para retomar sin duplicar gastos, hojas ni adjuntos.

## 7. Verificación y entregas siguientes

E1: APIs genéricas y pruebas de carga/rollback/orden/perfil, destino/secretos,
identidad, sesión, aislamiento concurrente y turno de lote.
E2: extracción equivalente, pruebas de gastos con cargador real contra git archive
de una ref exacta del motor modificado, núcleo sin imports ni reservas de gastos.
E3: varias empresas/destinos, aislamiento, compatibilidad e idempotencia.
E4: integración del addon y paquete reproducible. Validación externa real y corte
de versión quedan separados de las pruebas locales; no se declaran ejecutados.

Cada entrega incluye manifiesto, base, hashes y verificaciones. Sin push, nuevo
repositorio ni despliegue durante E0-E2. Los contratos se ajustan por observaciones
con revisión explícita y registro de diferencias; nunca mediante cambio silencioso.

## Revisión 1

El marco de imputador y política incluye empresa y destino (claves), además de
company_id, tz, uid y odoo de lectura ligado al destino. Los textos de identidad
gastos_requiere_acceso y gastos_credenciales pasan al plugin; textos en configuración
permite overrides. Prueba obligatoria: ensayo cargado, propuesta principal, OK nuevo,
carga principal; recibos de ensayo intactos y su OK incapaz de autorizar principal.

## Revisión 2 — categorías (alcance E3)

Cada empresa declara categorías como nombre visible -> {default_code, ids}.
default_code es opcional y, si se declara, es texto no vacío; ids es un mapa destino -> entero positivo
que reemplaza la referencia por código solo en ese destino. El modelo ofrece
exclusivamente nombres visibles del catálogo. La resolución admite productos
can_be_expensed de la compañía elegida o compartidos (company_id vacío), siempre
activos. Cero coincidencias o varias coincidencias fallan cerrado; nunca se elige
la primera. El ID resuelto y su referencia integran la propuesta y se revalidan
antes de aplicar. Los nombres visibles no se usan para resolver por nombre en ERP.

## Estado implementado en E2

El paquete carga desde plugin/gastos mediante el cargador real. Declara el contrato
gastos.imputador/1; el campo plano imputador selecciona un proveedor nombrado.
Vacío omite imputación; una referencia configurada que falta o está fuera del
perfil falla cerrado. Imputador vive en el paquete, no en bot.plugins.
Los registros de lotes y salas ocurren en Plugin.activar, dentro del rollback.
La forma plana, nombres de datos y huellas existentes se conservan. El comando
anterior se reemplaza por atar-erp <login> empleado_gastos <id>. El marco plano
expone empresa=principal y destino=heredado hasta implementar selección en E3.
La consulta de categorías plana mantiene temporalmente la lógica anterior;
el catálogo de esta revisión no se declaraba implementado ni validado en E2.

## Revisión 3 — estado E3

La forma plana conserva empresa principal y destino heredado cuando no declara
destinos. Su catálogo ahora es obligatorio para preparar nuevas propuestas: no
hay búsqueda por nombre en ningún esquema. Un catálogo vacío se acepta como
configuración inicial pero la empresa no puede rendir hasta tener categorías.
Cada entrada debe tener ID para todos sus destinos o default_code; ID prevalece
en el destino que lo declara. No existe fallback de un ID ausente a buscar por
nombre. Una categoría no declarada pide configurar o crear el producto.

gasto_catalogo informa empresas accesibles por vínculo de empleado y destinos;
elegidos ambos, verifica las referencias y devuelve solo nombres visibles.
gasto_preparar y gasto_cargar reciben empresa y destino. La empresa se infiere
solo si hay una accesible; el destino solo si hay uno declarado. Un lote queda
ligado a su empresa al persistir la primera propuesta válida. Toda propuesta
nueva tiene revision propia y exige OK nuevo, incluso con datos idénticos.

Las propuestas nuevas tienen version=2 y seleccion={empresa,destino,erp,firma}.
La firma cubre la configuración efectiva de esa corrida, incluyendo referencias
de categorías y nombres de secretos, nunca sus valores. Cambios exclusivos de
otro destino no invalidan una propuesta. Se releen identidad, empleado, categoría,
diario, imputación, política, configuración y huella antes de escribir.

Persistencia bajo DATA_DIR/gastos:
- <lote>.json: propuesta heredada; no se reescribe al cargar.
- <lote>/<empresa>/<hash-ERP>.json: propuesta por ERP explícito.
- <lote>/<empresa>/<hash-ERP>.recibo.json: huella aprobada, estado parcial/cargado,
  IDs de efectos reconciliados y resultado, sin credenciales ni contenido de fotos.
- <lote>.indice.json: persona, empresa, destinos pendientes y retener_hasta.
- <lote>.lecturas.json: lectura de cada foto al cerrar el lote, con su SHA-256. De
  ahí salen los números fiscales de la factura, no de lo que escribe el agente.

Una corrida con recibo ya iniciado se retoma, no se reemplaza. Los marcadores
permiten reconciliar una escritura cuyo recibo no alcanzó a persistirse.
Preparar o aplicar otro destino no modifica sus recibos. Los alias se comparan
por identidad canónica URL/base: un mismo alias no puede nombrar dos ERPs y dos
alias no pueden nombrar uno. El alias y sus credenciales pueden compartirse entre
empresas del mismo ERP; ERPs distintos requieren ambos nombres de secretos propios.

retencion_dias es entero entre 30 y 3650, default 90. El índice fija el plazo
desde la creación del lote al persistir su primera propuesta. Se conserva hasta
completar todos los destinos declarados o vencer ese plazo; no se prolonga al
preparar otro destino. Los destinos retirados de configuración siguen pendientes
en el índice hasta el plazo; nuevos destinos también impiden purgar. La purga del
motor sigue su mínimo habitual de 30 días. Durante una operación activa no borra
las fotos. No se inicia una corrida después de vencer el plazo.

lote.cierre_inactividad_min (0.1.0-dev.8) va en la raíz, no en una empresa: el
consumidor de lotes es uno solo. Es null, el default, para no cerrar por
inactividad: el lote queda abierto hasta que la persona contesta «¿Es lo último?».
Si no, es un entero >= 1 de minutos sin material nuevo tras los que el lote se
cierra solo. Se rechazan 0, los negativos y los que no son enteros. El plugin lo
lee al activarse y lo registra en el consumidor; sin cierre registra infinito,
porque el motor no acepta null. Un cambio rige al reiniciar el bot. No entra en
la firma de la corrida.

El proveedor de política implementa evaluar/revalidar async; observaciones y
bloqueos son listas de hasta 20 textos no vacíos de hasta 1000 caracteres; estado
es JSON finito de hasta 2 KiB. Se muestra todo sin recortar. Si la tarjeta supera
el límite del motor se rechaza, nunca se aprueba una vista truncada. Un bloqueo,
excepción, cambio del resultado o referencia configurada inválida impide escribir.

Las propuestas anteriores sin seleccion solo se retoman en forma plana y ERP
heredado inequívoco, conservando sus bytes y su huella. Una propuesta heredada
impide cambiar ese lote a esquema explícito automáticamente: se retoma con su
configuración anterior. Los catálogos deben configurarse para revalidar productos.
Los flujos requieren chat privado/sala personal y turno de la persona.

## Revisión 4 — paquete E4

odoo/ incorpora hr_expense_receipt_bill y hr_expense_receipt_bill_latam, con su
README y sus 27 pruebas sin Odoo. Los archivos recibidos se conservan byte por
byte, excluyendo únicamente cachés de Python; no cambia su comportamiento.
El puente base es neutro respecto del país; LATAM es una extensión separada.
Su dependencia OCA hr_expense_invoice y las de localización se instalan en Odoo,
no se incluyen ni se descargan automáticamente desde el paquete del bot.

La integración usa únicamente FORMATOS.md: marcador exacto del gasto y un solo
marcador de lote en la hoja; el pago sale de los gastos. Los addons quedan
deshabilitados hasta habilitar base, compañía y usuarios según odoo/README.md.
El plugin continúa creando borradores y no invoca revisión ni contabilización.

scripts/empaquetar.py genera un ZIP determinista con hashes por archivo, sin
rutas locales, fechas de trabajo, credenciales, cachés ni metadatos de autor.
scripts/verificar_paquete.py comprueba inventario y contenidos. VERSION del
paquete pasa a 0.1.0-dev.4; APIs y ref del motor se mantienen como en E3.
Las verificaciones locales y la sonda ORM comunicada por la integración se
registran por separado; esta incorporación no declara repetir esa sonda.

## Revisión 5 — factura independiente y rendición opcional

Una factura y la evaluación de la conducta de quien gastó son dos objetivos
independientes. La aprobación, rechazo o ausencia de rendición no condiciona
registrar el comprobante, su analítica ni su revisión fiscal. Registrar no
significa contabilizar: los permisos, datos fiscales, imputación y evidencia
deben estar verificados antes de los pasos contables correspondientes.

Cada empresa declara rendicion=true o false para seleccionar el nuevo circuito
de facturas. false no consulta empleados ni modelos HR y no requiere instalar
hr_expense ni OCA. true habilita una acción separada de rendición sobre facturas
ya registradas y contabilizadas por contabilidad, enlazadas por invoice_id;
nunca crea otra factura ni otro asiento de coste. La revisión de la rendición
se realiza posteriormente en Odoo y no cancela la factura si se rechaza.

La ausencia de rendicion conserva exclusivamente la compatibilidad E4; no se
convierte una propuesta ni un recibo existente. El despliegue nuevo debe elegir
explícitamente true o false y configurar cuentas contables por categoría/destino.
cuentas es nombre visible del catálogo -> {ids: {destino: account.account ID}}.
El catálogo sigue apuntando a productos por ID/código; en el circuito directo
no se exige can_be_expensed, que depende de HR. No se resuelve por nombre.
Imputación analítica explícita requerida por el nuevo puente; no se inventa.

gasto_preparar/gasto_cargar sólo registran facturas con fotos y analítica en
borrador. Su OK L2 confirma exactitud y autoriza escritura nominal: no aprueba
conducta ni reembolso. La política de rendición no se ejecuta ni bloquea estas
acciones. gasto_rendicion_preparar/gasto_rendir tienen propuesta, huella, OK L2
y recibo separados; se evalúa allí la política, sin modificar el recibo fiscal.
Los bloques de política afectan únicamente esta segunda acción. Un fallo del
proveedor de política tampoco impide registrar la factura.

receipt_bill depende sólo de account y aplica guardas de origen, idempotencia,
revisión fiscal y evidencia; receipt_bill_latam añade los documentos LATAM.
receipt_bill_expense es el adaptador opcional HR/OCA: exige factura canónica
contabilizada, vínculo inmutable y unicidad por factura para impedir duplicar
coste/analítica. No automatiza aprobación, contabilización ni pagos.
Los addons recibidos en E4 siguen íntegros; no se habilitan sus creadores junto
al nuevo. La nueva versión requiere repetir instalación y sonda ORM, incluyendo
un entorno sin HR y rechazo de rendición con factura conservada.

## Revisión 6 — capacidades explícitas por destino

Base probada: motor v0.6.0, commit 1efc814a569f661c62a3eef0181a27170f96aaf1.
El motor no cambia. Un destino admite capacidades={factura, rendicion,
control_sin_contabilizar_confirmado}. factura es addon o estandar; rendicion es
vinculada, revision_hr o ninguna. Se validan en el plugin y no se envían como
parámetros de conexión al motor. No hay autodetección ni fallback entre rutas.

La empresa mantiene rendicion=true/false: false exige ninguna en sus capacidades
explícitas. estandar no admite vinculada. Ausencia de capacidades conserva la
ruta E5 addon/vinculada (o ninguna si rendicion=false), sólo por compatibilidad.
La factura elegida integra su firma fiscal; política, modo HR y confirmación
administrativa no la integran. Cambios HR invalidan exclusivamente su propio OK.

estandar usa únicamente account.move, account.move.line e ir.attachment. Valida
metadatos, tipos y permisos nominales antes de escribir, y crea borrador con
proveedor/ref/documentos fiscales vacíos, auto_post=no, una línea sin producto,
cantidad uno, cuenta contable del catálogo y analítica explícita. invoice_origin
lleva exactamente el marcador [Lnnnnnn/Fnn]. Se revalidan cabecera, creador,
cuenta, analítica, total, compañía, moneda, fecha, estado y adjuntos/checksums.
Un default incompatible detiene el proceso sobre su ID conocido, no crea otro.

Los tipos de cada contrato se comparan con lo que devuelve fields_get. Un campo que
cambia de tipo entre versiones de Odoo lleva en el contrato los tipos que valen
(0.1.0-dev.8): ir.attachment.res_id acepta many2one_reference, que es el de Odoo 18,
o integer. Una sola definición (estandar.ADJUNTO) sirve a este contrato y al control
de RRHH (facturas.CONTRATO_CONTROL). Todos los tipos fijos se prueban contra el
fields_get real de un Odoo 18 en producción (tests/datos/fields_get_odoo18.json, solo
esquema). Si el destino no cumple, el error dice que es del plugin («Plugin de gastos
(versión del bot): … Lo arregla quien mantiene el bot…»). Dice además qué campo
falta o tiene otro tipo, y que cambiar de destino no lo resuelve.

La intención durable se persiste antes de cada create de factura o adjunto,
incluyendo huella y destino/compañía/lote/foto. ID recibido se guarda antes de
releer. Un timeout/cancelación/error deja resultado incierto: se busca coincidencia
exacta, se coteja todo y se recupera; cero o varias coincidencias requieren
conciliación, nunca un create ciego. Un marcador editado no reemplaza el ID conocido.
Los archivos se escriben atómicamente con fsync de archivo y directorios POSIX.
Candado de archivo por lote coordina procesos sobre el almacenamiento compartido,
protege también el índice entre destinos y la purga, y se libera al cancelar.
Se requiere un único escritor del origen con ese almacenamiento compartido.

La ruta estándar NO tiene restricción UNIQUE ni huella inmutable servidor ni
guarda de revisión fiscal del addon. No se promete exclusión frente a ingreso
manual u otro escritor ajeno al candado. El plugin nunca contabiliza ni paga;
la revisión/contabilización del destino son externas al plugin.

revision_hr requiere control_sin_contabilizar_confirmado=true, confirmado por el
responsable administrativo. Su default false bloquea únicamente la fase HR.
La propuesta, tarjeta y descripción del gasto cuando está disponible muestran:
«Hoja de control: no se aprueba ni se contabiliza. La factura ya llevó el coste.»
El aviso se funda en verificar la factura ya contabilizada, sin cambiarla.
Esta hoja no es un segundo circuito contable. La restricción de no aprobar ni
contabilizarla es un procedimiento confirmado, no una guarda servidor instalada.

Esta fase usa sólo campos estándar hr.expense/hr.expense.sheet y adjuntos,
con nombres persistidos E4, empleado y categoría de la compañía, fotos y estado
borrador. No usa invoice_id ni campos de addons. Conserva correlación local de
factura/gasto, propuesta/huella/OK/recibo propios e intenciones durables; no altera
factura, coste ni analítica por rechazo/política. Si faltan fotos originales o
metadatos/permisos HR, se detiene la rendición sin afectar el registro fiscal.
No aprueba, envía, contabiliza, reembolsa ni cancela hojas. Sin OCA, un operador
que contabilice la hoja nativa podría duplicar coste; por eso la confirmación y
el aviso son obligatorios. La ruta vinculada mantiene el adaptador/OCA de E5.

Las sondas reales de ambos destinos y la confirmación administrativa corresponden
a integración. Las pruebas locales no acreditan políticas del proveedor ni ORM.
El árbol final plugin/gastos se entrega con inventario y hashes para fijarlo;
no se modifica después de la entrega sin aviso.

## 9. Factura estándar con impuestos (0.1.0-dev.7)

Opcional por empresa con rendicion declarada: impuestos {iva: {"<alícuota>": {ids}},
no_gravado, exento, sin_credito: {ids}} y documentos {"<código impreso>": {ids,
iva: discriminado|incluido}}, con un ID por destino. Van juntos; un documento con
IVA incluido exige sin_credito. Solo se usa en la ruta estandar de un destino que
expone l10n_latam_document_type_id, l10n_latam_document_number y amount_tax.

El lector devuelve líneas y desglose fiscal: CUIT/RUT, código, letra y número de
documento, CAE, neto e IVA por alícuota, no gravado, exento, percepciones y otros
tributos. Es dato no confiable: textos cortos y listas acotadas. El agente ve las
líneas para elegir la categoría; una categoría e imputación por comprobante.

Con impuestos solo si cuadra al centavo: total leído igual al confirmado, tipo y
alícuotas configurados, neto por alícuota igual al IVA impreso (redondeo medio
hacia arriba), y netos + IVA + no gravado + exento + percepciones + otros = total.
Discriminado: una línea por alícuota con su IVA, otra para no gravado y otra para
exento con sus impuestos. Incluido: una línea con el total menos percepciones y
tributos, con sin_credito. Cada percepción va en su línea «<descripción>: pasar a
impuesto» y cada otro tributo en «<descripción>: revisar», sin impuesto. Todas
las líneas llevan cuenta e imputación del comprobante. Proveedor por CUIT/RUT
(empresa, no contacto, de la compañía o compartido); nunca se crea. Si el CUIT/RUT
se repite (0.1.0-dev.8), va el que tiene más facturas de proveedor publicadas en
la compañía (in_invoice posted por commercial_partner_id; a igual cantidad, el de
ID menor), y la propuesta, la tarjeta del OK y la nota de la factura lo dicen. Al
OK, el elegido sigue valiendo mientras siga entre los del CUIT/RUT. Documento y
número (PPPPP-NNNNNNNN cuando son punto de venta y número; si no, el texto leído).

0.1.0-dev.9. documentos."<código>" también puede ser {cargar_como: "<código>"}: el
comprobante se carga con el tipo, ids e iva de ese código de la misma empresa, que
no puede ser otro cargar_como (ConfigError). El plan lleva documento (el de carga,
con el que se valida el tipo y se busca el duplicado) y documento_leido; propuesta,
tarjeta y narration dicen los dos. Ninguna línea va sin IVA: cada otro tributo va
con no_gravado si el documento discrimina IVA (sin no_gravado configurado, sin
impuestos) y con sin_credito si lo incluye. impuestos.percepciones: {"<tipo>":
{ids}} o {"<tipo>": {"<jurisdicción>": {ids}}}, tipo en minúsculas y jurisdicción
en mayúsculas (ISO 3166-2:AR para iibb); sus IDs no pueden ser de IVA. El lector da
tipo (iva, iibb, ganancias u otra) y jurisdicción de cada percepción. Con impuesto
configurado, va en todas las líneas que no son de percepción; el impuesto es de
compra, activo, de la compañía, sin precio incluido y de monto fijo mayor que cero.
Dos percepciones con el mismo impuesto suman su importe. Después del create, la
carga escribe el importe leído en la línea de impuesto de cada percepción (write
de account.move con line_ids [(1, id, {amount_currency})], la única escritura que
admite la corrida, en borrador, con evidencia local «ajustadas»; el preflight
exige write sobre account.move solo si hay percepciones). Sin impuesto, la línea
aparte de antes. La verificación compara amount_tax con IVA más percepciones y
cada línea de impuesto (tax_line_id, amount_currency) con lo leído; una propuesta
de antes de la dev.9 se carga como entonces.

0.1.0-dev.10. distribucion: la clave es un entero > 0, una tupla o lista de 2 a 5
enteros > 0 distintos o su texto "a,b[,c…]" (dígitos sin ceros a la izquierda,
sin espacios). Se normaliza a "a,b" en el orden dado. Un ID solo sigue yendo como
entero. Si dos claves normalizan igual, es error. Cada comparación con lo leído del
ERP compara la forma normalizada: la combinación como conjunto y los porcentajes
como decimales. Pasa por la carga estándar y la del addon, por el gasto y su hoja
y por la rendición. Todas las líneas de la factura llevan la misma distribución.

El lector suma anotaciones (transcripción literal de lo agregado a mano o pegado
sobre el comprobante; tope 300) y fiscal.receptor_id_fiscal (dígitos; vacío si no
figura). Un salto de línea leído vale un espacio. Por empresa, opcionales:

- id_fiscal: dígitos, de 5 a 20.
- emisor_id_fiscal_patron: regex de hasta 200 caracteres, con re.fullmatch sobre
  los dígitos del emisor.
- destino_por_defecto: uno de sus destinos.

Las tres no entran en la firma. Al cerrar el lote, cada foto del material lleva
empresa_sugerida y empresa_motivo («receptor», «emisor» o vacío), calculados entre
las empresas accesibles. Si la configuración no es válida, no sugiere y el cierre
no falla. seleccionar sin destino toma destino_por_defecto si es accesible; si no,
pide elegir, como antes.

Barrera, al preparar y otra vez al cargar: si la empresa tiene id_fiscal y una foto
de un gasto trae un receptor distinto, es GastoError. Un lote sigue atado a una
empresa: las fotos de otra se excluyen y se mandan en otro lote.

pago_detalle es opcional por comprobante: una línea imprimible de hasta 80
caracteres, con los espacios normalizados. Va en la línea del plan (y por eso en
la huella) solo si viene, en la propuesta, en la tarjeta, en narration («Pago: …»,
también sin plan fiscal si el destino tiene narration) y en la description del
gasto (hoja heredada, y rendición si el campo se puede escribir). pago sigue siendo
obligatorio. No llega al imputador.

Si algo no cuadra, la factura es la de antes: una línea con el total, sin
impuestos, proveedor ni documento, y el desglose leído en narration. No hay un
tercer estado. Un impuesto o tipo configurado que en Odoo no es de compra, activo,
de la compañía, sin precio incluido y con la alícuota o el código configurados es
error de configuración, no cae a sin impuestos.

Antes de proponer y antes de crear se busca en la compañía una factura de
proveedor, no cancelada, con el mismo tipo y número del mismo emisor, incluidos
los borradores: Odoo recién frena el repetido al confirmar. Si existe no se crea.
Se verifican cabecera, proveedor, documento, número, IVA total, total y cada línea
en orden: cuenta, analítica, precio e impuestos. En la rendición puede haber menos
líneas (percepciones ya pasadas a impuesto); las que quedan conservan cuenta y
analítica. El bot nunca confirma la factura.

La misma foto (SHA-256) en la propuesta de otro lote de la persona, mismo destino:
si ese lote se cargó o empezó a cargarse, no se vuelve a cargar; si solo tenía
propuesta, esa queda reemplazado_por el lote nuevo, su huella cambia y su carga se
niega.

0.1.0-dev.11. documentos."<código>" con ids e iva admite sin_codigo: {letra, tipo}, letra de 1 o 2 mayúsculas y tipo una
expresión regular válida de hasta 200 caracteres (si no, ConfigError). Sin documento_codigo leído, armar usa el único
código cuyo sin_codigo tiene la letra leída y cuyo tipo encuentra el tipo leído en minúsculas y sin acentos; con ninguno o
con dos, sin impuestos («no leí el tipo de comprobante»). El plan con impuestos lleva entonces documento_por_letra: true, y
la nota de la factura lo dice. Un lote con extra.descartado (op lote-descartar o /nueva del motor 0.6.11) no se prepara ni
se carga (GastoError). Con el motor 0.6.11, gasto_preparar en el turno de cierre del lote ve el lote cerrado.

0.1.0-dev.12. fiscal.TOLERANCIA = 1.00: armar acepta el IVA de cada alícuota si |neto × alícuota − IVA impreso| <= 1 y la
suma de las partes si |total − suma| <= 1; la diferencia de la suma va al importe de la línea mayor, y el plan lleva
redondeo (texto para la nota). importe() redondea al centavo hasta cuatro decimales; con más, None. La carga estándar
escribe en su línea de impuesto el IVA impreso si difiere en hasta TOLERANCIA del que calculó Odoo; más lejos no se
escribe y la verificación frena. Cada gasto de gasto_preparar admite receptor_es_la_empresa (bool) y correccion
({emisor_id_fiscal, documento_numero}, textos de hasta 40; el emisor, de 5 a 20 dígitos; el número, con número
canónico). El receptor leído que difiere en un dígito del id_fiscal no es ajeno; uno ajeno confirmado tampoco. La línea
del plan lleva receptor_confirmado, receptor_aviso y correccion si vinieron, y entran en la huella. account.move lleva
ref = la imputación (hasta 80, vacía sin imputación) y date = fecha del comprobante si el destino expone date; la
verificación compara ref. gasto_cargar devuelve texto (una línea por factura o informe, con link), no JSON; la función
cargar() sigue devolviendo el resultado, con «texto». gasto_preparar de un lote con recibo cargado: GastoError «ya está
cargado en Odoo». estado.cargados(lotes) lista los lotes con algún recibo cargado.

0.1.0-dev.13. La carga estándar crea cada línea con product_id = el producto de la categoría del gasto (el que resolvió
la propuesta), en el mismo create que price_unit, tax_ids, account_id y analytic_distribution. La verificación exige ese
producto o ninguno (factura de antes de la dev.13).

0.1.0-dev.14. `padron_proveedores: AR|UY` declara que la empresa no carga facturas sin un proveedor validado
en el padrón: el circuito fiscal rechaza el proveedor ausente y un contacto con el prefijo de alta pendiente.
`seguimiento: <proveedor>` resuelve `gastos.seguimiento/1`, con `async seguimiento(ctx,marco,plan,resultado)`;
se ejecuta tras guardar el recibo fiscal y también al recuperar con `gasto_estado`. No concede escrituras ni
aprobaciones al proveedor. Su fallo conserva los efectos y devuelve seguimiento pendiente, sin repetir facturas.

0.1.0-dev.15. El alta del proveedor es de un toolbelt de la casa, no del plugin, y `gasto_proveedor_alta` se retira.
`alta_proveedor: {"repo": ..., "script": ...}`, por empresa y junto con `padron_proveedores`, declara ese toolbelt. Sin
declararlo, la propuesta frena y el proveedor se da de alta en el ERP. El contrato del toolbelt está en `proveedores.py`.
El plugin lo corre con `broker.ejecutar_toolbelt`, que usa siempre el ERP y la credencial globales del bot: el alta solo
se ofrece en el destino que es ese ERP (mismos nombres de secreto y misma base); en otro, la propuesta frena.

Si falta el proveedor, la propuesta ensaya el alta, sin escribir, y la deja pedida en `fiscal.alta_proveedor`. La tarjeta
de la carga la muestra: un solo OK aprueba el alta y las facturas. La razón social todavía no se conoce, porque sale del
padrón. Con el OK, las altas corren antes de abrir el recibo y antes de la primera factura. Si una falla, o si un alta
nueva vuelve con un aviso (lo leído no se parece al padrón, falta un dato fiscal), no se carga ninguna factura y se puede
preparar de nuevo: el proveedor ya existe y la propuesta muestra su razón social. Lo que resuelve el alta no cambia el
archivo aprobado: el proveedor vive en el plan en memoria, y `estandar._payload` firma el comprobante sin él.
