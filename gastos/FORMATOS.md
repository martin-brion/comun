# Formatos persistidos gastos/1

Base: v0.5.4 (171a045b490fef702d390f117c340fbad6b6ad3a).

- ID de lote: L y seis dígitos, por ejemplo L000001.
- Marcador: [L000001/F01]; F identifica la foto principal, no un contador nuevo.
- Nombre del gasto: <comercio> [L000001/F01].
- Nombre de hoja: Rendición <persona> YYYYMMDD< rótulo opcional> [L000001] · <pago>.
- YYYYMMDD es la fecha mínima de los comprobantes del lote, como en la base.
- company_account se muestra Empresa; own_account se muestra Propio.
- El rótulo opcional del imputador lleva un espacio anterior solo cuando existe.
- Principal: Comprobante [L000001/F01].jpg.
- Respaldo: Respaldo [L000001/F01] foto 02.jpg.
- El destino no se agrega a marcadores ni nombres: los recibos lo separan.

El addon debe reconocer marcadores y compañía y tolerar rótulos visibles variables.
No debe extraer identidad nominal ni destino de un texto de nombre de hoja.
SHA-256 identifica el original del lote; Odoo checksum contiene SHA-1 del adjunto
normalizado. No son intercambiables.

## Circuito directo (contrato 2)

La factura identifica el mismo comprobante mediante receipt_bill_lot=Lnnnnnn y
receipt_bill_photo=nn, con unicidad por compañía. El nombre de su línea conserva
<comercio> [Lnnnnnn/Fnn]. receipt_bill_payment conserva el pago confirmado;
receipt_bill_payload_hash identifica el contenido confirmado; source_uid y total/
analítica originales los fija el servidor. No se interpretan proveedor, destino
ni conducta a partir del texto del nombre.

El recibo fiscal conserva <hash-ERP>.recibo.json. La propuesta y recibo de rendición
posterior son <hash-ERP>.rendicion.propuesta.json y <hash-ERP>.rendicion.recibo.json
en la misma carpeta de empresa/lote. Sus huellas y OK no se intercambian.
La rendición enlaza la factura existente por invoice_id y receipt_bill_source_id;
no modifica marcadores ni crea otra factura. Los formatos E4 siguen vigentes
únicamente en su circuito heredado, sin conversión automática.

## Destino estándar (E6)

invoice_origin contiene exactamente [Lnnnnnn/Fnn]; ref queda vacío para la
referencia fiscal. La línea conserva <comercio> [Lnnnnnn/Fnn]. No se escriben
campos receipt_bill_* ni invoice_id en un destino sin esos addons.
Con impuestos (CONTRATO 9) cada línea es <comercio> [Lnnnnnn/Fnn] · <etiqueta>:
«IVA <alícuota> %» (la primera suma los ítems leídos), «No gravado», «Exento»,
«<descripción>: pasar a impuesto» para percepciones sin impuesto configurado y
«<descripción>: revisar» para otros tributos; con IVA incluido, los ítems leídos.
Una percepción con impuesto (0.1.0-dev.9) no tiene línea propia: va en la línea de
impuesto que arma Odoo. Cada percepción leída lleva tipo y jurisdiccion, vacíos en
una lectura de antes. narration lleva
«Lectura del bot (sin verificar): …» con el desglose leído, también sin impuestos,
y al final «Pago: <pago_detalle>» si la persona lo dio (0.1.0-dev.10). Desde la dev.12,
antes del pago, «Redondeo del comprobante: …», «Corregido por la persona sobre lo leído: …» y
«Receptor: …» cuando corresponden; ref es la imputación y date la fecha del comprobante. Desde la dev.13, cada
línea lleva el producto de la categoría. La lectura
suma anotaciones y fiscal.receptor_id_fiscal, vacíos en una lectura de antes;
empresa_sugerida y empresa_motivo van solo en el material del turno, no en
<lote>.lecturas.json. La línea del plan lleva pago_detalle solo si vino. La
description de un gasto con pago_detalle intercala «Pago: <pago_detalle>.» después
del comprobante o del aviso de control. analytic_distribution admite claves "a,b"
(combinaciones de 2 a 5 IDs, en el orden del imputador).
l10n_latam_document_number va como PPPPP-NNNNNNNN si el comprobante trae punto de
venta y número; se compara sin separadores ni ceros a la izquierda.
<lote>.lecturas.json: {version: 1, lote, fotos: {"<n>": {sha256, lectura}}}.
Una propuesta reemplazada lleva reemplazado_por=<lote nuevo>.
<hash-ERP>.estandar.intenciones.json conserva huella, autor nominal, ID conocido,
resultado incierto y estado de adjuntos por compañía/foto, y percepciones=ajustadas
cuando la línea de impuesto de cada percepción ya lleva su importe. No se borra ni reinicia
para forzar una creación; un caso incierto se concilia con el registro remoto.

revision_hr mantiene nombres de gastos y hojas E4. La relación con la factura
es local en el recibo de rendición, no un enlace contable ORM inventado. Las
intenciones y correlaciones por foto forman parte de ese recibo separado.
La propuesta y tarjeta incluyen siempre el aviso obligatorio de hoja de control.
