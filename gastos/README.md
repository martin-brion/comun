# Plugin gastos

Extracción del circuito de gastos por foto: propuesta de solo lectura, confirmación
L2 ligada a huella y carga idempotente de borradores con fotos verificadas. No envía,
aprueba ni contabiliza. Código genérico; datos y configuración pertenecen a la capa
de cada casa. Cada versión cruza como entrega sin historia y la despliega quien la corre.

El plugin selecciona empresa y destino por corrida. El mismo lote admite propuestas
sucesivas en ensayo y principal, cada una con su OK L2 y recibo independiente.
Conserva las fotos mientras haya destinos declarados pendientes, hasta el plazo
configurable. CONTRATO.md define selección, compatibilidad y persistencia;
FORMATOS.md fija los marcadores y nombres que usa el addon.

## Instalación para validar

Usar motor v0.6.0. Ya incluye los cambios E2/E3: no volver a aplicar sus patches.
Copiar plugin/gastos dentro del PLUGINS_DIR de solo lectura de la instancia;
activar PLUGINS_INSTANCIA. El cargador descubre el paquete y registra sus circuitos
en la activación transaccional, nunca durante el import. El perfil debe habilitar
el grupo gastos. Configuración en FUNCIONES_CONFIG_DIR/gastos.json; secretos en
la bóveda nominal. Cada destino explícito declara obligatoriamente sus nombres
de secreto_login y secreto_api_key; no usa credenciales heredadas como fallback.
Vínculos nominales con atar-erp: usuarios_erp.<destino>, empleados_gastos.<empresa>
y, cuando cambia el empleado entre ERPs, empleados_gastos_destinos.<empresa>.<destino>.
El plugin valida usuario, empleado y compañía con el ERP elegido en cada corrida.

## Factura y rendición independientes (E5)

Cada empresa nueva declara rendicion=true o false. Ambas opciones registran
facturas con analítica y evidencia sin consultar RRHH ni evaluar políticas de
conducta. El OK de carga sólo autoriza registrar datos, no aprueba reembolso.
false no requiere instalar hr_expense ni OCA. true permite además preparar la
rendición como otra acción, cuando contabilidad haya revisado y contabilizado
las facturas, con vínculo único a ellas y un OK diferente.

La rendición puede quedar bloqueada o ser rechazada sin borrar, cancelar ni
alterar facturas. Una excepción o cambio de su política tampoco impide registrar
el comprobante. Los controles técnicos, de evidencia e imputación y la revisión
fiscal siguen siendo obligatorios y son independientes de la conducta.

Para el circuito nuevo, instalar únicamente receipt_bill (depende account) y
receipt_bill_latam si corresponde. Para habilitar rendición, instalar además
receipt_bill_expense y OCA hr_expense_invoice de una revisión fijada. Leer los
README de cada nuevo addon. Los creadores fiscales legados no se coinstalan:
los addons recibidos en E4 se conservan para compatibilidad, no son la ruta nueva.

El nuevo circuito exige imputador y cuentas por categoría/destino, con IDs de
account.account explícitos. El catálogo de productos sigue separado: sus IDs
no se reinterpretan como cuentas. Ejemplo: ejemplo_capa/gastos_facturas.json,
con una empresa con rendición y otra sin ella, ambas con ensayo y principal.

Herramientas: gasto_preparar/gasto_cargar registran facturas. Después, si se
habilita, gasto_rendicion_preparar/gasto_rendir crean gastos y hojas enlazados,
sin volver a crear la factura ni aprobar, contabilizar o pagar. Sus propuestas
y recibos se separan de los fiscales. La política se ejecuta sólo en esta fase.

Ausencia de rendicion conserva el circuito E4 exclusivamente para compatibilidad.
No hay migración automática de propuestas, recibos, módulos ni datos contables.
El nuevo comportamiento es preliminar y requiere repetir instalación y sonda ORM;
las pruebas sin Odoo no acreditan ese recorrido real. E4 publicado queda intacto.

## Capacidades por destino (E6)

Cada destino puede declarar capacidades con factura=addon o estandar, y
rendicion=vinculada, revision_hr o ninguna. Son opciones del plugin; el objeto
de conexión del motor recibe únicamente sus campos públicos. Las capacidades
ausentes conservan la ruta E5 para compatibilidad; no se autodetectan ni degradan.

estandar registra account.move en borrador sin addons propios ni OCA: cuenta,
analítica y fotos, con marcador en invoice_origin y ref libre para el documento
fiscal. Metadatos/tipos/permisos y campos releídos se validan antes de adoptar
registros. Guarda intenciones e IDs de forma durable y coordina procesos por
archivo en DATA_DIR. No hacer correr escritores separados sin compartir ese
almacenamiento y su origen. Un resultado incierto se concilia, no se reinicia.

revision_hr es sólo control: deja gastos y hojas en borrador, sin enlace contable
de addons y sin aprobar ni contabilizar. Exige confirmación administrativa por
destino, control_sin_contabilizar_confirmado=true, después de acordar el
procedimiento con el responsable. Su default false permite registrar facturas
y bloquea sólo el paso HR. La propuesta, tarjeta y descripción cuando existe
dicen «Hoja de control: no se aprueba ni se contabiliza. La factura ya llevó el
coste.» El plugin verifica antes la factura contabilizada. La empresa confirma
que esas hojas nunca se aprueban ni se contabilizan para evitar duplicar coste.
El plugin no puede impedir acciones manuales fuera de su circuito sin un addon.

ejemplo_capa/gastos_destinos.json demuestra ensayo con addon y principal estándar
en el mismo lote, y una empresa sin rendición. El flag administrativo queda
false a propósito hasta confirmar el procedimiento. No cambiar IDs de producto
por IDs contables: son catálogos separados. Ver CONTRATO.md revisión 6 y FORMATOS.

## Factura con impuestos y diario por ID (0.1.0-dev.7)

El diario va por ID en la empresa, uno por destino: `"diario": {"ids": {"ensayo":
ID, "principal": ID}}`. Un diario por nombre o dentro del destino se rechaza: el
nombre se traduce con el idioma de Odoo.

Con `impuestos` y `documentos` (ver ejemplo_capa/gastos.json y CONTRATO 9), la ruta
estandar arma la factura como la ve el fisco cuando el desglose leído de la foto
cuadra al centavo: proveedor por CUIT, tipo y número de documento, una línea por
alícuota con su IVA de Odoo, no gravado y exento, y percepciones en una línea
«pasar a impuesto» que Administración convierte. Si no cuadra, va sin impuestos
con el desglose leído en la factura. Todas las líneas llevan la imputación. Antes
de crear se busca el mismo comprobante, incluidos los borradores. Las fotos
repetidas en otro lote no se vuelven a cargar.

0.1.0-dev.8: si el CUIT del proveedor se repite en Odoo, el bot usa el que tiene
más facturas de proveedor publicadas en la compañía (a igual cantidad, el de ID
menor) y lo dice en la propuesta: «Había N proveedores con el CUIT/RUT …; usé
<nombre>, id X…». Antes, un CUIT repetido dejaba la factura sin proveedor.

0.1.0-dev.15: si falta el proveedor y la empresa declara `alta_proveedor`, la tarjeta
de la carga suma el alta y un solo OK aprueba las dos cosas. El alta corre primero,
con el toolbelt de la casa; si falla o deja un aviso, no se carga ninguna factura.

0.1.0-dev.16: `gasto_cargar` y `gasto_rendir` devuelven `publico` para un motor que trae
`broker.texto_publico`: 0.6.23, 0.6.24 y desde la 0.6.26. La 0.6.25 sale de una línea
anterior y no lo trae: ahí carga igual, sin ese texto, y la prueba del resultado público
falla porque llama a esa función. Tras un OK diferido, la persona ve sus links y el motivo de un rechazo, no
la confirmación genérica. Un error de Odoo o de disco no se muestra: llega un motivo fijo.
Carga también en motores previos.

0.1.0-dev.17: el plugin es el mismo de la dev.16, archivo por archivo. Cambian dos
pruebas. El `fields_get` de Odoo 18 ya no es la copia de una base en producción: lo
arman los tipos de Odoo 18 que piden los contratos del plugin, sin salir de ninguna
base. Y las identificaciones fiscales de `test_fiscal_plugin.py` son inventadas, con
dígito verificador inválido. Por eso esa prueba ya no detecta un tipo que una base
real tenga distinto: eso lo ve la sonda del destino.

También en la dev.8, el cierre del lote por inactividad sale de `gastos.json`:
`"lote": {"cierre_inactividad_min": null}`, a nivel plugin. null, el default, no
cierra solo: el lote espera el «sí» de la persona. Un entero >= 1 cierra el lote
tras esos minutos sin material nuevo. Antes cerraba fijo a los 30 minutos. Rige
al reiniciar el bot (CONTRATO, junto a retencion_dias).

## Tipo de carga, otros tributos y percepciones (0.1.0-dev.9)

Un código de `documentos` puede ir como `{"cargar_como": "<otro código>"}`: el
comprobante se carga con el tipo, los IDs y el IVA de ese código, que tiene que
estar en la misma empresa con sus `ids` e `iva` (no ser otro `cargar_como`). Por
ejemplo, una casa que carga los tiques factura como factura. La propuesta dice
los dos («Tique Factura A (código 81), se carga como FACTURAS A …»), la nota de la
factura también, y el duplicado se busca con el tipo de carga.

Sin el código impreso, un código de `documentos` con sus `ids` puede declarar
`"sin_codigo": {"letra": "A", "tipo": "<expresión regular>"}` (0.1.0-dev.11). El
comprobante sin código se carga con ese código si la letra leída es esa y el patrón
encuentra el tipo leído, en minúsculas y sin acentos. Con dos candidatos no elige, y
la factura queda sin impuestos como antes. El patrón sirve para dejar afuera notas de
crédito o débito con la misma letra. La propuesta y la nota de la factura dicen que el
tipo salió de la letra.

## Redondeo, receptor, correcciones y lo que ve la persona (0.1.0-dev.12)

Lo impreso cuadra con hasta **un peso** de diferencia por redondeo (`fiscal.TOLERANCIA`). Un IVA que difiere del neto
por la alícuota en hasta un peso se carga con el importe impreso (la carga escribe esa línea de impuesto, como la de una
percepción). Si las partes difieren del total en hasta un peso, la diferencia va al neto de la línea mayor, y la factura
suma el total del comprobante. Un importe impreso con tres o cuatro decimales se redondea al centavo. Si la diferencia es
mayor, la factura queda sin impuestos, como antes. La nota de la factura dice qué se redondeó.

El receptor es un punto de control: solo puede ser la empresa (`id_fiscal`). El de la empresa leído con un dígito mal se
toma como suyo, porque dos CUIT o RUT válidos nunca difieren en un solo dígito. Si se leyó otro, la propuesta pide que
la persona mire el comprobante: si confirma que es el de la empresa, el gasto va con `receptor_es_la_empresa: true`. Nunca
se pide ni se carga otro CUIT. La nota de la factura dice lo que se leyó.

Lo que la persona corrige mirando el comprobante (el CUIT/RUT del emisor o el número) va en `correccion` del gasto,
manda sobre la lectura y queda en la nota de la factura. La lectura guardada no cambia.

`gasto_cargar` devuelve una línea por factura en borrador con su link (`link_url` del destino), y marca la que quedó sin
impuestos con su motivo. Ya no devuelve el JSON. La factura lleva la fecha contable del comprobante (sin ella, Odoo le pone
el último día del mes) y la imputación como referencia. Un lote cargado no se vuelve a preparar, y el cierre del lote
siguiente le dice al agente qué lotes de la sala ya están cargados: el OK aplicado fuera de su turno no le llega en una
sala personal.

## El producto de la categoría en cada línea (0.1.0-dev.13)

Cada línea de la factura lleva el producto de su categoría (`categorias.<nombre>.ids`), como las carga Administración.
Va en la misma escritura que el precio, los impuestos, la cuenta y la analítica, así Odoo no los recalcula desde el
producto. La verificación acepta además una línea sin producto: es una factura cargada antes de la dev.13 que se
retoma o se rinde.

Ninguna línea va sin IVA: los otros tributos (impuestos internos, tasas, ajuste
por redondeo) van con `impuestos.no_gravado` si el comprobante discrimina IVA y
con `sin_credito` si lo incluye. Sin `no_gravado`, la factura va sin impuestos.

`impuestos.percepciones` lleva el impuesto de compra de cada percepción, por tipo
(`"iva": {ids}`) o por tipo y jurisdicción (`"iibb": {"B": {ids}}`, con la letra
ISO 3166-2:AR de la provincia). El lector da el tipo y la jurisdicción de cada
percepción; la clave sale de ahí, sin inferir nada. Una percepción con impuesto
va como impuesto de Odoo, en todas las líneas del comprobante: el impuesto tiene
que ser de monto fijo (por ejemplo, de 1,00), Odoo arma su línea con N × ese
monto y la carga le escribe el importe leído; Odoo recalcula total y vencimiento.
La verificación compara cada línea de impuesto (IVA por alícuota y percepciones)
con lo leído. Sin impuesto configurado, la percepción queda en una línea aparte,
sin impuesto, como antes: Odoo no deja publicar la factura hasta que Administración
la pasa a su impuesto. Es la única escritura del plugin sobre algo ya creado: el
importe de líneas existentes de su factura, en borrador.

Y corrige un error de la dev.7 que frenaba la primera carga real. En Odoo 18,
`ir.attachment.res_id` es `many2one_reference`, pero los dos contratos (estándar y
control de RRHH) pedían `integer`. Ahora aceptan los dos, con una sola definición,
y se prueban contra un `fields_get` de Odoo 18
(`tests/datos/fields_get_odoo18.json`, inventado desde la dev.17). El error de contrato dice que es del
plugin y que lo arregla quien mantiene el bot.

Base del motor v0.6.0. No requiere otro patch ni instalar addons en el destino
estándar. Los addons de E5 quedan idénticos para el destino con capacidades addon.
Se requiere la nueva sonda de ambos destinos, sin atribuirla a las pruebas locales.

Cada empresa declara su catálogo: nombre visible -> ids por destino y/o
default_code opcional. Los IDs por destino son el caso habitual y prevalecen.
Productos compartidos o de la compañía elegida son válidos si están activos y
habilitados para gastos en la ruta RRHH. La ruta directa sólo exige productos
activos de la compañía o compartidos. Ausencia o ambigüedad falla cerrado. Un catálogo vacío,
incompleto o sin la categoría pedida devuelve un error claro; no busca por nombre.
El modelo consulta gasto_catalogo y ofrece exclusivamente sus nombres visibles.

ejemplo_capa contiene una Casa de Prueba con dos empresas y dos destinos, IDs
por destino, vínculos nominales y proveedores de imputación y política. Su README
explica la instalación de cada archivo. Son datos inventados para adaptar.

Un proveedor de imputación exporta Plugin.extensiones con
Extension('gastos.imputador', 1, 'regla_prueba', proveedor); proveedor expone
proponer y revalidar async, y nota como texto. Puede ser un SimpleNamespace sin
importar el consumidor: la validación es estructural y no depende del orden. Su propio
grupo también debe estar habilitado. Seleccionar imputador=regla_prueba en JSON.
El código de una extensión se distribuye como paquete de confianza, nunca desde
el KB. Sin proveedor configurado la rendición no imputa.

La política se selecciona por empresa mediante Extension('gastos.politica', 1,
nombre, proveedor), con evaluar y revalidar async. Sus observaciones se muestran
en la confirmación; bloqueos, cambios o fallos impiden escribir. Imputador y
política reciben empresa, destino, company_id, tz, uid y Odoo de lectura ligado
al destino. Ambos son código de confianza y reciben copias de las líneas.

## Lo escrito sobre el comprobante, empresa y pago (0.1.0-dev.10)

La distribución analítica admite claves combinadas. El imputador puede dar como
clave un ID entero o una combinación de 2 a 5 IDs distintos, uno por plan
analítico, como tupla `(1, 166)` o como texto `"1,166"`. Va a Odoo como `"1,166"`,
en el orden dado. Todas las líneas de la factura llevan la misma distribución, y
la verificación de la carga y de la rendición compara lo que devuelve Odoo con esa
forma: la misma combinación, en cualquier orden, con los mismos porcentajes.

El lector suma dos campos, que van al material del lote:

- `anotaciones`: lo escrito a mano o pegado sobre el comprobante, transcripto tal
  cual, hasta 300 caracteres. Es dato de la persona, no instrucciones.
- `fiscal.receptor_id_fiscal`: la identificación del receptor, solo dígitos. Va
  vacío si no figura, como en un tique a consumidor final.

Cada empresa puede declarar tres claves, todas opcionales:

- `id_fiscal`: sus dígitos.
- `emisor_id_fiscal_patron`: una expresión regular que cumplen todos los dígitos
  del emisor de sus comprobantes, es decir, el formato de su país.
- `destino_por_defecto`: uno de sus destinos.

Al cerrar el lote, cada foto lleva `empresa_sugerida` y `empresa_motivo`:

1. La única empresa accesible cuyo `id_fiscal` es el receptor («receptor»).
2. Si no la hay, la única cuyo patrón cumple el emisor («emisor»).
3. Si tampoco, las dos van vacías.

Sin destino, la corrida usa `destino_por_defecto`, si la persona tiene acceso; el
otro destino va solo si se lo pide. Estas tres claves no entran en la firma: una
propuesta preparada antes de agregarlas sigue valiendo.

Si la empresa tiene `id_fiscal` y una foto del gasto, principal o respaldo, trae
otro receptor, no se prepara ni se carga. El mensaje es «el comprobante está a
nombre de otro receptor; prepará esa foto con su empresa o excluila». Un lote
sigue yendo a una sola empresa (`estado.comprobar`): las fotos de otra se excluyen
y se mandan en otro lote.

Cada comprobante acepta `pago_detalle`, opcional: quién y con qué pagó, como lo
escribió la persona, en una línea de hasta 80 caracteres. Va a la propuesta, a la
tarjeta del OK, a la huella, a la nota interna de la factura («Pago: …») y a la
descripción del gasto de la rendición o de la hoja. `pago` sigue siendo
obligatorio. Sale de lo anotado o de lo que dijo la persona, nunca de la tarjeta
impresa en el voucher. El turno del lote arma la propuesta con la lectura, las
anotaciones y lo ya dicho, y pregunta solo lo que falta, lo ilegible, lo ambiguo
o lo que contradice el comprobante.

## Addons y paquete

odoo/ conserva el puente E4 y su extensión
LATAM, sin cambios respecto de la fuente recibida. Ver odoo/README.md para
dependencias OCA, habilitación por base/compañía/usuario, permisos y recuperación.
El addon no reemplaza la revisión fiscal. No se instala automáticamente desde el bot.

Construir fuera de esta carpeta con
`python -B scripts/empaquetar.py --salida ../gastos-paquete.zip`.
El ZIP incluye fuente, addons, ejemplo, pruebas y SHA256SUMS.txt por archivo.
Orden, fechas y permisos son constantes; usa ZIP sin compresión para evitar
dependencia de zlib. Verificar con
`python -B scripts/verificar_paquete.py ../gastos-paquete.zip`.
Reconstruir desde una extracción intacta produce exactamente los mismos bytes.

## Pruebas

MOTOR_REPO debe apuntar al repositorio del motor; MOTOR_REF es una ref exacta y
PYTHON un intérprete Linux con sus dependencias. Ejecutar bash scripts/test.sh.
El runner obtiene git archive de esa ref, copia las suites del plugin, aísla el
entorno y carga el paquete con plugins.cargar_instancia. No modifica el checkout.
Las pruebas utilizan la Casa de Prueba y datos inventados. No llaman al ERP real.
El runner ejecuta además las pruebas de los addons, sin Odoo; también se pueden
ejecutar con `python -B -m unittest discover -s odoo/tests -p 'test_receipt*.py' -v`.

Los resultados y hashes de cada entrega están en su manifiesto. La versión de
VERSION es preliminar; no implica release ni compatibilidad con el motor base sin
el patch de extracción. No instalar simultáneamente con el antiguo módulo del núcleo.
