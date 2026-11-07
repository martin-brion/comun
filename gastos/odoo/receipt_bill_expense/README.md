# Rendición opcional sobre comprobantes existentes

Este adaptador se instala únicamente si se usa la rendición de Recursos Humanos.
Depende de `receipt_bill` y de `hr_expense_invoice` de OCA, rama 18.0. La carga
contable directa no lo necesita ni depende de `hr_expense`.

Cada `hr.expense` nuevo declara `receipt_bill_source_id` e `invoice_id` con el
mismo ID de factura canónica contrato 2, ya contabilizada. Es una acción distinta
de la carga de la factura: nunca crea, aprueba, contabiliza ni paga esa factura.
La factura conserva su analítica cuando una rendición se rechaza.

Se exige usuario nominal de origen, empleado de esa persona en la compañía,
compañía y base habilitadas y lectura contable permitida por las ACL existentes.
No se conceden permisos contables al instalar el adaptador. El usuario que sólo
tiene RRHH no obtiene acceso contable ni puede crear un vínculo ocultando una
denegación de acceso; la revisión de conducta sigue siendo una acción de RRHH.

Los datos de la rendición deben coincidir con compañía, fecha de comprobante,
moneda, importe y analítica de la factura, además del pago y evidencia originales.
Si la revisión fiscal cambió importe o analítica, la operación se detiene con un
error explícito: no se reemplaza silenciosamente el importe aprobado. Se requiere
revisar la divergencia antes de volver a proponer la rendición.

El vínculo es único, inmutable y no se puede eliminar ni copiar para obtener un
gasto ordinario. La contabilización de la hoja comprueba otra vez el vínculo
antes de delegar al circuito normal de OCA, que excluye las facturas existentes
del asiento ordinario de gastos. La factura canónica nunca puede figurar como
asiento generado por la hoja ni ser anulada por rechazo o reinicio de ésta.

Pruebas sin Odoo: `python -m unittest discover -s odoo/tests -p test_receipt_expense.py`.
Verifican contrato puro y guardas anteriores a efectos; no sustituyen una sonda
ORM en Odoo 18 con las versiones exactas de las dependencias del despliegue.
