"""Ingreso estándar: evidencia local durable, sin extensiones instaladas en el ERP.

No existe una restricción UNIQUE remota. El llamador mantiene exclusión de
escritores sobre el almacenamiento compartido durante toda la operación.
"""
import base64
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path

from . import configuracion, estado, fiscal


# Los tipos se comparan con lo que dice fields_get. Un campo puede cambiar de tipo según la versión de Odoo: ahí el contrato
# lleva una tupla con los que valen. ir.attachment.res_id es many2one_reference en Odoo 18 (fields_get de un inquilino en
# producción, 30/10; ver tests/datos/fields_get_odoo18.json) e integer en versiones anteriores.
RES_ID = ("many2one_reference", "integer")
# Los campos del adjunto que exigen los dos contratos: este y el de control de RRHH (facturas._contrato_control).
ADJUNTO = {"name": "char", "checksum": "char", "res_model": "char", "res_id": RES_ID, "datas": "binary"}
PREFIJO_PLUGIN = "Plugin de gastos (versión del bot): "
SUFIJO_PLUGIN = (" Lo arregla quien mantiene el bot. No es un problema de tus datos ni de la configuración, y cambiar de "
                 "destino no lo resuelve.")

_CAMPOS = {
    "account.move": {
        "state": "selection", "move_type": "selection", "company_id": "many2one",
        "currency_id": "many2one", "journal_id": "many2one", "invoice_date": "date",
        "amount_total": "monetary", "invoice_origin": "char", "invoice_line_ids": "one2many",
        "partner_id": "many2one", "ref": "char", "auto_post": "selection", "create_uid": "many2one",
    },
    "account.move.line": {
        "account_id": "many2one", "product_id": "many2one", "analytic_distribution": "json",
        "quantity": "float", "price_unit": "float", "discount": "float", "tax_ids": "many2many",
        "display_type": "selection",
    },
    "ir.attachment": {**ADJUNTO, "type": "selection", "public": "boolean", "access_token": "char"},
}
_LATAM = {"l10n_latam_document_type_id": "many2one", "l10n_latam_document_number": "char"}
# Para una factura con impuestos hacen falta, además, los campos LATAM, el IVA total y las líneas de impuesto (se verifican
# contra lo leído y la de cada percepción lleva su importe); la narración (el desglose leído) se escribe solo si el destino la
# tiene.
_FISCAL = {"amount_tax": "monetary", "line_ids": "one2many"}
_FISCAL_LINEA = {"tax_line_id": "many2one", "amount_currency": "monetary"}
_NARRACION = ("narration", ("html", "text"))
# La fecha contable es la del comprobante, como carga Administración. Sin escribirla, Odoo le pone a una factura de proveedor
# de un mes anterior el último día de ese mes (o la fecha de hoy si es del mes en curso). Se escribe si el destino la expone.
_FECHA = ("date", "date")


def referencia(x):
    """La referencia de la factura: la imputación como la vio la persona («Gastos de representación», el proyecto), que es
    como Administración las rotula; vacía sin imputación."""
    return " ".join(str((x.get("imputacion") or {}).get("texto") or "").split())[:80]


def tipo_valido(meta, tipo):
    """El campo de fields_get (`meta`) es del tipo del contrato: uno solo o cualquiera de una tupla."""
    return isinstance(meta, dict) and meta.get("type") in ((tipo,) if isinstance(tipo, str) else tuple(tipo))


def diferencias(modelo, campos, requeridos):
    """Lo que no cumple el contrato, para el mensaje: «ir.attachment.res_id es many2one_reference», «… falta»."""
    if not isinstance(campos, dict):
        return [f"{modelo} sin descripción de campos"]
    return [f"{modelo}.{n} " + (f"es {campos[n].get('type')}" if isinstance(campos.get(n), dict) else "falta")
            for n, t in requeridos.items() if not tipo_valido(campos.get(n), t)]


def error_contrato(g, faltas):
    """El destino no tiene el contrato que esta versión del plugin necesita: es del plugin, no de los datos ni de gastos.json
    (en la prueba real del 29/10, el agente lo leyó como configuración y ofreció probar en otro destino)."""
    return g.GastoTecnico(f"{PREFIJO_PLUGIN}el Odoo de destino no tiene lo que esta versión necesita "
                        f"({'; '.join(faltas)}).{SUFIJO_PLUGIN}")


async def contrato(ctx, g):
    """Preflight explícito; la ausencia de un campo no cambia de estrategia."""
    c = g._CORRIDA.get()
    resultado = {}
    # La única modificación es el importe impreso en una línea de impuesto (percepción, o IVA que difiere por redondeo): write
    # solo hace falta si la empresa tiene impuestos configurados.
    escribe = ("write",) if c.get("impuestos") else ()
    for modelo, requeridos in _CAMPOS.items():
        operaciones = {"account.move": ("read", "create", *escribe), "account.move.line": ("read",)}.get(
            modelo, ("read", "create"))
        for operacion in operaciones:
            if await g._odoo(ctx, modelo, "check_access_rights", operacion, raise_exception=False) is not True:
                raise g.GastoError(f"La identidad nominal no tiene permiso {operacion} sobre {modelo}")
        campos = await g._odoo(ctx, modelo, "fields_get", attributes=["type"])
        if faltas := diferencias(modelo, campos, requeridos):
            raise error_contrato(g, faltas)
        resultado[modelo] = campos
    move = resultado["account.move"]
    if set(_LATAM) & move.keys() and (faltas := diferencias("account.move", move, _LATAM)):
        raise error_contrato(g, faltas)
    c["_estandar_campos"] = resultado
    return resultado


def _metadata(c):
    return c.get("_estandar_campos", {}).get("account.move", {})


def admite_fiscal(c):
    """El destino expone documento, número, IVA total y líneas de impuesto de la factura (localización LATAM)."""
    return (not diferencias("account.move", _metadata(c), {**_LATAM, **_FISCAL})
            and not diferencias("account.move.line", c.get("_estandar_campos", {}).get("account.move.line", {}),
                                _FISCAL_LINEA))


def _admite_narracion(c):
    campo, tipos = _NARRACION
    return tipo_valido(_metadata(c).get(campo), tipos)


def campos_factura(c):
    campos = list(_CAMPOS["account.move"])
    metadata = _metadata(c)
    return (campos + [nombre for nombre in _LATAM if nombre in metadata]
            + (list(_FISCAL) if admite_fiscal(c) else []))


def _con_impuestos(x):
    f = x.get("fiscal")
    return bool(f) and f["modo"] == fiscal.CON_IMPUESTOS


def _payload(plan, x):
    return configuracion.firma({"company": plan["company_id"], "uid": plan["odoo_uid"], "linea": fiscal.sin_alta(x)})


def _ruta(lote_id, c):
    p = estado.recibo_path(lote_id, c)
    return estado.seguro(p.with_name(p.name.replace(".recibo.json", ".estandar.intenciones.json")))


def _guardar(path, valor):
    """No se escribe al ERP hasta confirmar la intención durable local."""
    estado.guardar(path, valor)


def _intenciones(plan, c, g):
    ruta = _ruta(plan["lote_id"], c)
    encabezado = {"version": 1, "lote": plan["lote_id"], "empresa": c["empresa"],
                  "erp": c["erp_id"], "company_id": plan["company_id"], "uid": plan["odoo_uid"]}
    datos = estado.leer(ruta)
    if datos is None:
        datos = {**encabezado, "fotos": {}}
    if any(datos.get(k) != v for k, v in encabezado.items()) or not isinstance(datos.get("fotos"), dict):
        raise g.GastoError("La evidencia local del destino no coincide; requiere conciliación")
    return ruta, datos


def _foto_entrada(datos, plan, x, g):
    clave = f"{plan['company_id']}/{x['n']}"
    entrada = datos["fotos"].get(clave)
    if entrada is not None and (not isinstance(entrada, dict) or entrada.get("payload") != _payload(plan, x)
                                or not isinstance(entrada.get("adjuntos"), dict)):
        raise g.GastoError("Cambió el comprobante con una intención previa; requiere conciliación")
    return clave, entrada


def _importe(valor, g):
    try:
        numero = Decimal(str(valor))
        if not numero.is_finite():
            raise ValueError()
        return numero
    except (ValueError, InvalidOperation):
        raise g.GastoError("El ERP devolvió un importe inválido; no se continúa") from None


async def comprobar(ctx, b, plan, x, g, fase="registro"):
    c = g._CORRIDA.get()
    if fase not in ("registro", "rendicion"):
        raise g.GastoError("Fase de comprobación estándar inválida")
    _, datos = _intenciones(plan, c, g)
    _, entrada = _foto_entrada(datos, plan, x, g)
    if not entrada or entrada.get("id") != b.get("id"):
        raise g.GastoError("Falta la evidencia local del ID de factura; requiere conciliación")
    if (g._rel(b.get("company_id")) != plan["company_id"] or b.get("move_type") != "in_invoice"
            or g._rel(b.get("create_uid")) != plan["odoo_uid"]
            or b.get("invoice_origin") != x["marker"]
            or g._rel(b.get("currency_id")) != x["currency_id"]
            or g._rel(b.get("journal_id")) != plan["journal_id"] or b.get("invoice_date") != x["fecha"]
            or b.get("state") != ("draft" if fase == "registro" else "posted")
            or _importe(b.get("amount_total"), g) != _importe(x["importe"], g)):
        raise g.GastoError("La factura estándar no coincide con el comprobante; revisar sin duplicarla")
    con_impuestos = _con_impuestos(x)
    f = x.get("fiscal") or {}
    if fase == "registro":
        latam = [nombre for nombre in _LATAM if nombre in campos_factura(c)]
        if (g._rel(b.get("partner_id")) != (f.get("partner_id") or 0 if con_impuestos else 0)
                or (b.get("ref") or "") != referencia(x) or b.get("auto_post") != "no" or (not con_impuestos and any(b.get(nombre) for nombre in latam))):
            raise g.GastoError("Un default completó identidad o documento fiscal; revisar la factura sin duplicarla")
        if con_impuestos and (
                g._rel(b.get("l10n_latam_document_type_id")) != f["documento_id"]
                or fiscal.numero_canonico(b.get("l10n_latam_document_number") or "") != fiscal.numero_canonico(f["numero"])
                or _importe(b.get("amount_tax"), g) != fiscal.impuesto_total(f)):
            raise g.GastoError("El documento, el número o el IVA de la factura no son los del comprobante; revisar sin "
                               "duplicarla")
        esperado = fiscal.lineas_impuesto(f) if con_impuestos else None
        if esperado is not None and await _lineas_impuesto(ctx, b, g) != esperado:
            raise g.GastoError("Las líneas de impuesto de la factura (IVA por alícuota y percepciones) no son las del "
                               "comprobante; revisar sin duplicarla")
    # Con impuestos, Administración pasa las percepciones a impuesto antes de confirmar: en la rendición puede haber menos
    # líneas, pero las que quedan conservan cuenta y analítica.
    esperadas = fiscal.lineas_factura(x)
    ids = b.get("invoice_line_ids")
    if (not isinstance(ids, list) or not ids
            or (len(ids) != len(esperadas) if fase == "registro" or not con_impuestos else len(ids) > len(esperadas))):
        raise g.GastoError("La factura estándar no tiene las líneas esperadas")
    lineas = await g._odoo(ctx, "account.move.line", "read", ids, fields=list(_CAMPOS["account.move.line"]))
    if sorted(linea.get("id") for linea in lineas) != sorted(ids):
        raise g.GastoError("No se pudo verificar las líneas de la factura estándar")
    lineas.sort(key=lambda linea: linea["id"])
    for linea in lineas:
        if (g._rel(linea.get("account_id")) != x["account_id"]
                or not g._misma_distribucion(linea.get("analytic_distribution"), x["imputacion"]["distribucion"])
                # Sin producto: una factura cargada antes de la dev.13, que se retoma o se rinde.
                or g._rel(linea.get("product_id")) not in (0, x.get("product_id") or 0)
                or linea.get("display_type") not in (False, "product")
                or _importe(linea.get("quantity"), g) != 1 or _importe(linea.get("discount"), g) != 0):
            raise g.GastoError("La cuenta, analítica o importe de la factura cambió; revisar sin duplicar")
    if fase == "registro":
        for linea, (_, monto, impuestos) in zip(lineas, esperadas):
            if (sorted(linea.get("tax_ids") or []) != sorted(impuestos)
                    or _importe(linea.get("price_unit"), g) != _importe(monto, g)):
                raise g.GastoError("La cuenta, analítica, impuesto o importe de la factura cambió; revisar sin duplicar")
    return b


async def _lineas_de(ctx, b, campos, g):
    ids = b.get("line_ids")
    lineas = await g._odoo(ctx, "account.move.line", "read", ids, fields=campos) if isinstance(ids, list) and ids else []
    if not lineas or sorted(linea.get("id") for linea in lineas) != sorted(ids):
        raise g.GastoError("No se pudo verificar las líneas de impuesto de la factura estándar")
    return [linea for linea in lineas if linea.get("display_type") == "tax"]


async def _lineas_impuesto(ctx, b, g):
    """{impuesto: importe} de las líneas de impuesto de la factura, sin las de importe cero."""
    suma = {}
    for linea in await _lineas_de(ctx, b, ["display_type", "tax_line_id", "amount_currency"], g):
        impuesto = g._rel(linea.get("tax_line_id"))
        suma[impuesto] = suma.get(impuesto, Decimal(0)) + _importe(linea.get("amount_currency"), g)
    return {i: m for i, m in suma.items() if m}


async def _percepciones(ctx, ident, plan, x, entrada, ruta, datos, c, g):
    """La línea de impuesto de cada percepción lleva el importe del comprobante: Odoo la arma con el monto fijo del impuesto
    por cada línea que lo tiene, y al escribirle el importe recalcula el total y el vencimiento. La del IVA, el importe
    impreso si difiere por redondeo del que calcula Odoo (fiscal.TOLERANCIA). Escribir el mismo importe de nuevo no cambia
    nada, así que después de un corte se vuelve a escribir, salvo que la evidencia local diga que ya se hizo: desde ahí, un
    importe distinto lo frena la verificación."""
    f = x.get("fiscal") or {}
    if not _con_impuestos(x) or entrada.get("percepciones") == "ajustadas":
        return
    ivas = [(l["impuestos"][0], l["iva"], l["etiqueta"]) for l in f["lineas"] if l["tipo"] == "iva"]
    if not f.get("percepciones") and not ivas:
        return
    filas = await g._odoo(ctx, "account.move", "read", [ident], fields=["state", "create_uid", "invoice_origin", "line_ids"])
    if (len(filas) != 1 or filas[0].get("id") != ident or filas[0].get("state") != "draft"
            or g._rel(filas[0].get("create_uid")) != plan["odoo_uid"] or filas[0].get("invoice_origin") != x["marker"]):
        raise g.GastoError("La factura estándar no coincide con el comprobante; revisar sin duplicarla")
    lineas = await _lineas_de(ctx, filas[0], ["display_type", "tax_line_id", "amount_currency"], g)
    cambios = []
    for p in f.get("percepciones", []):
        propias = [linea["id"] for linea in lineas if g._rel(linea.get("tax_line_id")) == p["impuesto"]]
        if len(propias) != 1:
            raise g.GastoError(f"La factura no tiene una línea de impuesto para «{p['etiqueta']}»; revisar sin duplicarla")
        cambios.append((1, propias[0], {"amount_currency": float(Decimal(p["importe"]))}))
    for impuesto, monto, etiqueta in ivas:
        propias = [linea for linea in lineas if g._rel(linea.get("tax_line_id")) == impuesto]
        if len(propias) != 1:
            raise g.GastoError(f"La factura no tiene una línea de impuesto para «{etiqueta.split(' · ')[0]}»; revisar sin "
                               "duplicarla")
        actual = _importe(propias[0].get("amount_currency"), g)
        # Solo el redondeo: si Odoo calcula un IVA más lejos que la tolerancia, no se tapa y lo frena la verificación.
        if actual != Decimal(monto) and abs(actual - Decimal(monto)) <= fiscal.TOLERANCIA:
            cambios.append((1, propias[0]["id"], {"amount_currency": float(Decimal(monto))}))
    if not cambios:
        return
    g._vigencia(ctx, c)
    await g._odoo(ctx, "account.move", "write", [ident], {"line_ids": cambios},
                  context={"allowed_company_ids": [c["company_id"]]})
    entrada["percepciones"] = "ajustadas"
    _guardar(ruta, datos)


async def _leer_factura(ctx, ident, plan, x, c, g):
    filas = await g._odoo(ctx, "account.move", "read", [ident], fields=campos_factura(c))
    if len(filas) != 1 or filas[0].get("id") != ident:
        raise g.GastoError("No se pudo recuperar el ID de factura conocido; requiere conciliación")
    await comprobar(ctx, filas[0], plan, x, g)
    return filas[0]


async def _adjunto(ctx, lote, x, respaldo, factura, entrada, ruta, datos, c, g):
    from bot import config, imagen
    n = respaldo["n"]
    fotos = [item for item in lote["items"] if item["tipo"] == "foto"]
    original = Path(fotos[n - 1]["ruta"]).read_bytes()
    if hashlib.sha256(original).hexdigest() != respaldo["sha256"]:
        raise g.GastoError(f"Foto {n}: cambió desde la propuesta; no se adjunta")
    normal = await imagen.normalizar(original, max_px=config.ODOO_FOTO_MAX_PX, calidad=config.ODOO_FOTO_CALIDAD)
    checksum = hashlib.sha1(normal.datos).hexdigest()
    nombre = (f"Comprobante {x['marker']}.jpg" if n == x["n"] else
              f"Respaldo {x['marker']} foto {n:02d}.jpg")
    previo = entrada["adjuntos"].get(str(n))
    if previo and (not isinstance(previo, dict) or previo.get("payload") != checksum):
        raise g.GastoError("Cambió el adjunto con intención previa; requiere conciliación")
    campos = ["name", "checksum", "res_model", "res_id", "type", "public", "access_token"]
    if previo and previo.get("id"):
        ident = previo["id"]
    else:
        filas = await g._odoo(ctx, "ir.attachment", "search_read", [
            ("res_model", "=", "account.move"), ("res_id", "=", factura), ("name", "=", nombre)],
            fields=campos, limit=2)
        if len(filas) > 1:
            raise g.GastoError("Hay adjuntos ambiguos para el comprobante; requiere conciliación")
        if filas:
            ident = filas[0]["id"]
            previo = {"payload": checksum, "estado": "conocido", "id": ident}
            entrada["adjuntos"][str(n)] = previo
            _guardar(ruta, datos)
        elif previo:
            raise g.GastoError("El resultado del adjunto es incierto; requiere conciliación antes de crear otro")
        else:
            previo = {"payload": checksum, "estado": "pendiente"}
            entrada["adjuntos"][str(n)] = previo
            _guardar(ruta, datos)
            try:
                g._vigencia(ctx, c)
                ident = await g._odoo(ctx, "ir.attachment", "create", {
                    "name": nombre, "res_model": "account.move", "res_id": factura, "type": "binary",
                    "public": False, "access_token": False, "mimetype": "image/jpeg",
                    "datas": base64.b64encode(normal.datos).decode("ascii")},
                    context={"allowed_company_ids": [c["company_id"]]})
                if not configuracion.positivo(ident):
                    raise g.GastoError("El ERP no devolvió el ID del adjunto")
                previo.update(id=ident, estado="conocido")
                _guardar(ruta, datos)
            except BaseException:
                previo["estado"] = "resultado_incierto"
                _guardar(ruta, datos)
                raise
    filas = await g._odoo(ctx, "ir.attachment", "read", [ident], fields=campos)
    if (len(filas) != 1 or filas[0].get("id") != ident or filas[0].get("name") != nombre
            or filas[0].get("checksum") != checksum or filas[0].get("res_model") != "account.move"
            or filas[0].get("res_id") != factura or filas[0].get("type") != "binary"
            or filas[0].get("public") or filas[0].get("access_token")):
        raise g.GastoError("El adjunto conocido no coincide con la evidencia; revisar sin duplicarlo")
    previo["estado"] = "verificado"
    _guardar(ruta, datos)
    return ident


async def cargar(ctx, lote, c, plan, g):
    if "_estandar_campos" not in c:
        await contrato(ctx, g)
    ruta, datos = _intenciones(plan, c, g)
    facturas = {}
    for x in plan["comprobantes"]:
        g._vigencia(ctx, c)
        clave, entrada = _foto_entrada(datos, plan, x, g)
        if entrada and entrada.get("id"):
            ident = entrada["id"]
        else:
            filas = await g._odoo(ctx, "account.move", "search_read", [
                ("company_id", "=", c["company_id"]), ("move_type", "=", "in_invoice"),
                ("invoice_origin", "=", x["marker"])], fields=campos_factura(c), limit=2)
            if len(filas) > 1:
                raise g.GastoError("Hay facturas ambiguas para el comprobante; requiere conciliación")
            if filas:
                ident = filas[0]["id"]
                entrada = {"payload": _payload(plan, x), "estado": "conocido", "id": ident,
                           "adjuntos": entrada.get("adjuntos", {}) if entrada else {}}
                datos["fotos"][clave] = entrada
                _guardar(ruta, datos)
            elif entrada:
                raise g.GastoError("El resultado de la factura es incierto; requiere conciliación antes de crear otra")
            else:
                f = x.get("fiscal")
                if f and admite_fiscal(c):
                    # Odoo recién frena un número repetido al confirmar: se busca antes, con los borradores.
                    previo = await fiscal.ya_cargado(ctx, c, f, g)
                    if previo:
                        raise g.GastoError(fiscal.aviso_duplicado(x, f, previo))
                entrada = {"payload": _payload(plan, x), "estado": "pendiente", "adjuntos": {}}
                datos["fotos"][clave] = entrada
                _guardar(ruta, datos)
                # Todas las líneas llevan la imputación del comprobante, también la de percepción: la casa puede exigir
                # la analítica en cada línea de gasto para confirmar.
                values = {
                    "move_type": "in_invoice", "state": "draft", "company_id": c["company_id"],
                    "journal_id": plan["journal_id"], "currency_id": x["currency_id"], "invoice_date": x["fecha"],
                    "partner_id": (f.get("partner_id") or False) if _con_impuestos(x) else False,
                    "ref": referencia(x) or False, "auto_post": "no", "invoice_origin": x["marker"],
                    "invoice_line_ids": [(0, 0, {"name": nombre,
                        "account_id": x["account_id"], "quantity": 1, "price_unit": float(Decimal(monto)),
                        # El producto de la categoría, como carga Administración. Va en la misma escritura que el
                        # precio, los impuestos, la cuenta y la analítica: así Odoo no los recalcula desde el producto.
                        "product_id": x["product_id"], "discount": 0, "tax_ids": [(6, 0, impuestos)],
                        "analytic_distribution": x["imputacion"]["distribucion"]})
                        for nombre, monto, impuestos in fiscal.lineas_factura(x)],
                }
                if _con_impuestos(x):
                    values.update(l10n_latam_document_type_id=f["documento_id"],
                                  l10n_latam_document_number=f["numero_odoo"])
                else:
                    values.update({nombre: False for nombre in _LATAM if nombre in campos_factura(c)})
                if _admite_narracion(c) and (nota := fiscal.nota_factura(x)):
                    values["narration"] = nota
                if tipo_valido(_metadata(c).get(_FECHA[0]), _FECHA[1]):
                    values[_FECHA[0]] = x["fecha"]
                try:
                    g._vigencia(ctx, c)
                    ident = await g._odoo(ctx, "account.move", "create", values,
                                         context={"allowed_company_ids": [c["company_id"]]})
                    if not configuracion.positivo(ident):
                        raise g.GastoError("El ERP no devolvió el ID de la factura")
                    entrada.update(id=ident, estado="conocido")
                    _guardar(ruta, datos)
                except BaseException:
                    entrada["estado"] = "resultado_incierto"
                    _guardar(ruta, datos)
                    raise
        await _percepciones(ctx, ident, plan, x, entrada, ruta, datos, c, g)
        await _leer_factura(ctx, ident, plan, x, c, g)
        estado.efecto(plan["lote_id"], c, "account.move", ident)
        for respaldo in g._respaldos(x):
            aid = await _adjunto(ctx, lote, x, respaldo, ident, entrada, ruta, datos, c, g)
            estado.efecto(plan["lote_id"], c, "ir.attachment", aid)
        await _leer_factura(ctx, ident, plan, x, c, g)
        entrada["estado"] = "verificado"
        _guardar(ruta, datos)
        facturas[str(x["n"])] = ident
    return {"estado": "borrador", "facturas": facturas,
            "rendicion": "disponible como acción separada" if c["rendicion"] else "deshabilitada",
            "aviso": ("Facturas estándar en borrador con analítica y fotos registradas. Las que van con impuestos llevan "
                      "proveedor, documento, número, IVA y percepciones del comprobante; las que van sin impuestos, el "
                      "desglose leído. "
                      "Administración las revisa y las confirma. No aprueba reembolsos."
                      if any(x.get("fiscal") for x in plan["comprobantes"]) else
                      "Facturas estándar con analítica y fotos registradas. Revisión fiscal independiente; no aprueba reembolsos."),
            "pendiente_administracion": {str(x["n"]): fiscal.completa_administracion(x["fiscal"])
                                         for x in plan["comprobantes"] if x.get("fiscal")}}
