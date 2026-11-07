# Addons Odoo 18 del plugin `gastos`

La carga de gastos por foto crea el `hr.expense` con sus fotos y después la hoja `hr.expense.sheet`, todo en
borrador. Estos módulos agregan, **opcionalmente y por compañía**, el registro fiscal: un borrador de factura de
proveedor por gasto, vinculado con OCA `hr_expense_invoice`, con copias de los adjuntos verificadas por checksum.

| Módulo | Qué hace | Depende de |
|---|---|---|
| `hr_expense_receipt_bill` | El puente, sin país ni localización | `hr_expense_invoice` (OCA `hr-expense` 18.0) |
| `hr_expense_receipt_bill_latam` | Tipo y número de documento LATAM vacíos hasta la revisión y dentro de la huella | el anterior + `l10n_latam_invoice_document` (se instala solo) |

## Contrato con la carga

Lee del nombre únicamente los marcadores de `FORMATOS.md` del plugin: el gasto termina en `[Lnnnnnn/Fnn]` y la
hoja lleva su `[Lnnnnnn]` una sola vez. Los rótulos visibles pueden variar. El criterio de pago sale de los gastos
de la hoja (una hoja mezclada se rechaza), nunca del texto. No lee identidad ni destino de los nombres.

El borrador queda con **proveedor, referencia, tipo de documento e impuestos vacíos** y el total original como una
sola línea con la analítica del gasto. El comercio, el empleado y el texto leído de la foto no son identidad fiscal:
quien revisa la contabilidad clasifica el comprobante y descarta duplicados con lo que llegó por otra vía.

## Habilitación (todo apagado al instalar)

1. Parámetro de sistema `hr_expense_receipt_bill.database` = nombre exacto de la base. Una copia restaurada con
   otro nombre queda apagada.
2. En la compañía: `Borradores de proveedor desde gastos por foto`, el diario de compras y la lista de usuarios
   habilitados (los que cargan: necesitan además el rol de Facturación y ACL de crear/leer facturas; el módulo no
   eleva permisos contables).

Sin cron, sin recorrido de históricos, sin post-init. Una hoja marcada creada antes de habilitar se retoma con
**Preparar borradores de proveedor**.

## Guardas

- Sólo hojas en borrador; compañía, empleado, lote y criterio de pago tienen que coincidir; adjuntos binarios con
  checksum. Un default heredado que invente proveedor, documento o impuestos, o cambie el importe, aborta la hoja.
- Bloqueo por fila y claves únicas: un borrador por gasto y por marcador; la huella del gasto detiene el reintento
  si cambió.
- **Confirmar revisión fiscal** sólo registra quién revisó y cuándo, con una huella del borrador (proveedor,
  documento, importes, líneas, asientos, adjuntos). Cualquier cambio posterior la invalida; `_post` y una
  constraint bloquean contabilizar sin revisión vigente. No aprueba, envía ni paga.
- Los campos de origen y revisión no se pueden fijar por RPC ni por defaults de contexto.

## Pruebas

    python -m unittest tests/test_receipt_bill.py -v

Son de contrato y guardas, sin Odoo. La instalación y el recorrido ORM se prueban en una copia aislada de la base
con datos sintéticos; una prueba local no acredita la instalación.

## Recuperación

Apagar la compañía o borrar el parámetro detiene nuevas creaciones; no toca vínculos existentes. Un borrador
pendiente se corrige a mano; no se contabiliza para destrabar. Desinstalar quita las guardas: sólo en una copia
descartable.
