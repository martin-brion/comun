# Registro contable de comprobantes por foto (contrato 2)

Odoo 18 Community. Depende únicamente de `account`: la factura, su analítica y su
revisión son independientes de la aprobación de una rendición. No instala ni
consulta `hr_expense`. El registro inicial siempre queda en borrador, sin
proveedor, referencia fiscal, producto ni impuestos inferidos. El humano los
completa y revisa antes de contabilizar; el módulo no paga ni aprueba reembolsos.

## Instalación y habilitación

Instalar en una copia aislada primero. Parámetro `receipt_bill.database` igual al
nombre exacto de la base; en cada compañía habilitar `receipt_bill_enabled`,
`receipt_bill_user_ids` y un diario propio de compras `receipt_bill_journal_id`.
El creador necesita permisos contables nominales: grupo Facturación y ACL reales
de crear/leer facturas. Sólo se eleva la lectura de configuración protegida.
La copia con otro nombre permanece apagada hasta su habilitación explícita.

No coexistir con el addon creador legado `hr_expense_receipt_bill`: la presencia
del campo legado impide el ingreso directo, aunque su parámetro esté apagado.
La migración de facturas ya creadas requiere un procedimiento específico; no se
crean duplicados ni se convierten referencias automáticamente. OCA
`hr_expense_invoice` puede instalarse con el adaptador HR separado.

## API estándar de ingreso

`account.move.create` recibe `company_id`, `journal_id`, `currency_id`,
`invoice_date`, `move_type="in_invoice"`, y `invoice_line_ids` con una única orden
`(0, 0, {...})`: `name`, `account_id` explícito, `quantity=1`, `price_unit` positivo,
`tax_ids=[(6,0,[])]`, `analytic_distribution` no vacía (claves IDs de cuentas,
porcentajes finitos positivos hasta 100). No se resuelven cuentas por nombre ni
se agregan productos. Odoo valida compañías, planes y reglas contables.

Origen obligatorio: `receipt_bill_lot` (`Lnnnnnn`), `receipt_bill_photo` (1..30),
`receipt_bill_payment` (`own_account` o `company_account`),
`receipt_bill_payload_hash` (SHA256 hexadecimal minúscula). La unicidad es por
compañía+lote+foto; ante reintento el cliente busca y coteja la misma factura,
no interpreta un error de unicidad como autorización a crear otra.

`receipt_bill_source_uid` lo fija el servidor al creador nominal. El total y
analítica iniciales se guardan en `receipt_bill_original_total` y
`receipt_bill_original_analytic`; si se proporcionan deben coincidir. Son
evidencia inmutable, aunque el contador corrija luego las líneas revisables.
`receipt_bill_contract` es Integer computado, de sólo lectura: 2 para este origen
y 0 para facturas ordinarias.

Se permite adjuntar evidencia después de crear mediante `ir.attachment.create`
estándar, `res_model="account.move"`, `res_id` de esa factura. Debe ser binaria,
privada y tener checksum generado por Odoo. Una vez vinculada no se puede
eliminar, trasladar ni cambiar bytes; se agrega evidencia adicional. La revisión
exige al menos un adjunto. Un fallo al adjuntar conserva el borrador y el cliente
retoma esa factura, sin duplicarla.

## Revisión y límites

`action_receipt_bill_direct_review()` confirma revisión fiscal, sin contabilizar.
Los campos `receipt_bill_direct_review_by_id`, `receipt_bill_direct_review_at` y
`receipt_bill_direct_review_hash` sólo se escriben desde esa acción. La huella
incluye origen, factura, analítica, líneas contables y adjuntos. Editar factura,
líneas o nombre del adjunto invalida la revisión; `_post` verifica la huella y
una constraint impide saltar la guarda escribiendo el estado por RPC.

La política de reembolso de la empresa no habilita ni bloquea esta revisión;
rechazar una rendición no cancela la factura ni dispara pagos o transferencias.
El tratamiento contable de importes no reembolsables se revisa expresamente.
La clave de origen evita repetir un mismo ingreso; no sustituye la búsqueda de
duplicados fiscales ingresados por otros canales.

Apagar la compañía o borrar el parámetro detiene nuevos ingresos; las guardas de
revisión permanecen para documentos existentes. No desinstalar para volver atrás.

Este código nuevo requiere repetir sonda ORM: instalación sin HR, ambos pagos,
moneda extranjera, analítica, permisos, evidencia, revisión e idempotencia. Las
pruebas puras no acreditan instalación ni contabilización en un Odoo real.
