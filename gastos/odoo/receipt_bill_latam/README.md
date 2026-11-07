# Documentos LATAM del registro contable

Extiende `receipt_bill` con `l10n_latam_invoice_document`, sin dependencia HR.
El ingreso conserva tipo y número fiscal vacíos, sin inferirlos de la foto.
Cuando el diario usa documentos, la revisión exige completarlos y los incorpora
a la huella. Una edición posterior invalida la revisión. No instala ni configura
la localización contable de un país.

Requiere repetir la sonda ORM de la combinación concreta de localizaciones.
