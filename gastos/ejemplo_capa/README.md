# Casa de Prueba — dos empresas, dos destinos

Todo es sintético: dominios example.test, moneda de prueba XTS e IDs inventados.
No es una configuración lista para un ERP real. No contiene valores de secretos.

gastos.json es el ejemplo principal de revisión 6 y va en FUNCIONES_CONFIG_DIR.
El diario va por ID en la empresa, uno por destino. gastos.json declara además
impuestos y documentos (0.1.0-dev.7): IVA por alícuota, no gravado, exento,
sin crédito y tipos de comprobante por código impreso, con sus IDs inventados.
Taller suma (0.1.0-dev.9) percepciones por tipo y jurisdicción y un código que se
carga como otro (cargar_como).
Desde la 0.1.0-dev.10, cada empresa lleva su id_fiscal (dígitos inventados, con
dígito verificador inválido): con él, el cierre del lote sugiere la empresa del
receptor y gasto_preparar frena una foto a nombre de otro. Taller va sin destino a
principal (destino_por_defecto). Oficina declara emisor_id_fiscal_patron de 12
dígitos, como una empresa de otro país: un tique sin receptor con un emisor de 12
dígitos se sugiere para oficina. No pongas el mismo patrón en dos empresas: la
sugerencia no elige entre dos.
Declara rendicion por empresa y cuentas contables por categoría y destino.
Taller habilita rendición; oficina declara false. Ensayo muestra factura=addon
y, para taller, rendicion=vinculada. Principal muestra factura=estandar y,
para taller, rendicion=revision_hr. Oficina usa ninguna en ambos destinos.
control_sin_contabilizar_confirmado queda false hasta confirmación administrativa.
Cada destino declara su propio par de
nombres: ENSAYO_LOGIN/ENSAYO_KEY y PRINCIPAL_LOGIN/PRINCIPAL_KEY. Sus valores se
cargan nominalmente en la bóveda de cada persona. Los IDs de compañías, productos,
usuarios, empleados, diarios y cuentas deben obtenerse en cada ERP antes de usarlo.
Las dos empresas comparten destinos ERP y nombres de secretos porque son los
mismos endpoints; sus compañías y empleados son distintos.

vinculos.json ilustra el contenido de odoo_ids_extra de la persona de prueba.
No se copia al KB ni lo lee el plugin como autoridad. El responsable fija esos
vínculos con atar-erp; por ejemplo usuarios_erp.ensayo, empleados_gastos.taller y
empleados_gastos_destinos.taller.ensayo. El ID se valida con el ERP en cada corrida.

perfil.fragmento.json muestra los grupos a incorporar al perfil existente; no
reemplaza el perfil completo. capa_prueba.py se instala como código confiable y
de solo lectura junto al paquete gastos en PLUGINS_DIR. Su JSON va en
FUNCIONES_CONFIG_DIR. No carga código desde el KB ni requiere importar gastos.
La capa puede cargarse antes o después del consumidor.

El ejemplo usa IDs de producto por destino para todas sus categorías; default_code
es opcional. Oficina no declara Comidas: pedir esa categoría devuelve error de
catálogo, nunca una búsqueda por nombre. Si se agrega una categoría hay que crear
o identificar el producto en ambos destinos y configurar sus referencias. Un
producto compartido es válido; uno de otra compañía o ambiguo no se acepta.

Recorrido: consultar gasto_catalogo, elegir taller y ensayo, preparar L000001,
aprobar su tarjeta y cargar. Revisado el resultado, preparar L000001 con destino
principal usando las mismas fotos, aprobar la nueva tarjeta y cargar. El primer
OK no autoriza el segundo destino. Los recibos y referencias se mantienen separados.

Las políticas del ejemplo marcan topes por comprobante de XTS; una solo observa
y otra bloquea únicamente durante la fase de rendición cuando está habilitada;
no condicionan registrar la factura. Las imputaciones leen una cuenta analítica por empresa/destino
con la identidad nominal. Son ejemplos del contrato, no reglas fiscales.

gastos_facturas.json conserva el ejemplo de revisión 5, sin capacidades explícitas,
únicamente por compatibilidad. Para una configuración nueva usar gastos.json.
En ambos, taller declara
rendicion=true; oficina declara false. Ambas registran primero facturas con
analítica y fotos, independientemente de conducta. El catálogo sigue apuntando
a productos; cuentas declara aparte el ID contable por etiqueta y destino.
Sustituir ambos tipos de IDs con sus respectivos registros, sin confundirlos.
La rendición posterior usa el empleado nominal y factura contabilizada existente;
su política no se ejecuta durante el registro de la factura. Instalar los addons
nuevos según sus README; no habilitar los creadores E4 junto con ellos.

gastos_destinos.json es una copia equivalente del ejemplo principal de revisión 6:
ensayo
usa factura addon; principal usa factura estándar, sin addons. Taller puede usar
revision_hr sólo después de confirmar control_sin_contabilizar_confirmado; el
ejemplo deja false hasta esa confirmación. Oficina mantiene ninguna. La revisión
HR es control en borrador, nunca una segunda contabilización del coste fiscal.
La misma tanda puede ir a ambos destinos, con propuestas y recibos separados.

Para el primer ensayo real de la ruta sin addons, cambiar también el destino
de ensayo a factura=estandar y, si la empresa habilita rendición, revision_hr.
Conservar control_sin_contabilizar_confirmado=false hasta acordar el control con
Administración; esto no bloquea el registro fiscal. La ruta addon queda para
después de su propia sonda ORM. Las capacidades del ejemplo son ilustrativas,
no una instrucción para usar addons en el primer ensayo de integración.

Antes de usar la configuración real, Administración debe definir la cuenta
contable por concepto y compañía y obtener su ID en cada destino. No sustituir
esa decisión por una cuenta genérica ni copiar IDs de otra compañía o ERP.
Los IDs contables de cuentas son distintos de los productos de categorias y de
las cuentas analíticas de capa_prueba.json; todos los del ejemplo son inventados.
