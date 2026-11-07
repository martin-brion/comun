"""Desglose fiscal del comprobante: qué se lee de la foto, cuándo cuadra al centavo y qué líneas lleva la factura.

El bot no decide nada fiscal. La alícuota, el tipo de documento, el número y el emisor salen del comprobante leído, y los
impuestos de Odoo, de la configuración. Lo impreso cuadra con hasta TOLERANCIA de diferencia por redondeo (el IVA contra el
neto por la alícuota y las partes contra el total): la factura lleva el IVA impreso y la diferencia de la suma va al neto,
así el total es el del comprobante. Si algo no cuadra, la factura queda sin impuestos, como antes, con el desglose leído a la
vista para que Administración la complete. No hay un tercer estado. El bot solo da de alta un proveedor por el toolbelt
de la casa, con el OK de la carga (proveedores.py), y nunca confirma la factura.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import html
import re
import unicodedata

from . import configuracion, estado

CENTAVO = Decimal("0.01")
# Lo que puede diferir lo impreso por el redondeo del comprobante (un peso, decisión del inquilino 01/11): los comercios calculan
# el IVA como el total menos el neto, o suman importes de tres decimales redondeados por separado.
TOLERANCIA = Decimal("1.00")
CON_IMPUESTOS, SIN_IMPUESTOS = "con_impuestos", "sin_impuestos"
_TEXTO = {"type": "string"}
_TOPE_TEXTO = 200
_TOPES_LISTA = {"lineas": 60, "iva": 6, "percepciones": 10, "otros_tributos": 10}
_MAX_REPETIDOS = 20       # proveedores con el mismo CUIT/RUT que se comparan
_CAMPOS_LECTURA = ("fecha", "importe", "moneda", "comercio", "referencia", "tipo", "categoria_sugerida", "pago_visible",
                   "dudas", "anotaciones")
_TOPE_CAMPO = {"dudas": 500, "anotaciones": 300}
_CAMPOS_FISCAL = ("emisor_id_fiscal", "documento_codigo", "documento_letra", "documento_numero", "cae", "no_gravado",
                  "exento", "receptor_id_fiscal")
_LISTAS_FISCAL = {"iva": ("alicuota", "neto", "importe"), "percepciones": ("tipo", "jurisdiccion", "descripcion", "importe"),
                  "otros_tributos": ("descripcion", "importe")}
# Las jurisdicciones de Ingresos Brutos, con la letra de ISO 3166-2:AR (la del código de provincia de la localización): el
# lector las da así y la configuración las nombra igual (impuestos.percepciones.iibb.<letra>).
_JURISDICCIONES = ("C Ciudad de Buenos Aires (AGIP), B provincia de Buenos Aires (ARBA), K Catamarca, X Córdoba, "
                   "W Corrientes, E Entre Ríos, Y Jujuy, M Mendoza, F La Rioja, A Salta, J San Juan, D San Luis, S Santa Fe, "
                   "G Santiago del Estero, T Tucumán, H Chaco, U Chubut, P Formosa, N Misiones, Q Neuquén, L La Pampa, "
                   "R Río Negro, Z Santa Cruz, V Tierra del Fuego")


def _objeto(campos):
    return {"type": "object", "additionalProperties": False, "properties": {c: _TEXTO for c in campos},
            "required": list(campos)}


ESQUEMA = {"type": "object", "additionalProperties": False, "properties": {
    **{k: _TEXTO for k in _CAMPOS_LECTURA},
    "lineas": {"type": "array", "items": _objeto(("descripcion", "cantidad", "importe"))},
    "fiscal": {"type": "object", "additionalProperties": False, "properties": {
        **{k: _TEXTO for k in _CAMPOS_FISCAL},
        **{k: {"type": "array", "items": _objeto(campos)} for k, campos in _LISTAS_FISCAL.items()}},
        "required": [*_CAMPOS_FISCAL, *_LISTAS_FISCAL]}},
    "required": [*_CAMPOS_LECTURA, "lineas", "fiscal"]}

INSTRUCCIONES = (
    "Fecha ISO; importe = total final en moneda original; moneda = código ISO (ARS, UYU, USD); comercio; referencia = número de documento; tipo; medio de "
    "pago si aparece. "
    "lineas: cada ítem del detalle con su descripción, cantidad e importe de la línea, como figuran; si la línea trae "
    "importe sin IVA y con IVA, el sin IVA; vacío si no hay detalle legible. "
    "anotaciones: transcripción literal de todo lo agregado a mano o pegado sobre el comprobante (birome, marcador, "
    "post-it, sello con texto), en cualquier lugar de la foto, sin interpretar, corregir ni completar; vacío si no hay. "
    "fiscal: emisor_id_fiscal = CUIT o RUT del emisor, solo dígitos; receptor_id_fiscal = CUIT o RUT impreso del "
    "receptor o cliente, solo dígitos, vacío si no figura (tique a consumidor final); documento_codigo = código impreso del tipo de "
    "comprobante (por ejemplo «COD. 01» es 1); documento_letra = A, B, C, M o vacío; documento_numero = punto de venta y "
    "número (por ejemplo 00012-00034567); cae. iva: una entrada por alícuota con IVA discriminado (alícuota en %, neto "
    "gravado e importe del IVA, como figuran en el pie); en comprobantes B, C o tiques el IVA va incluido: dejá iva vacío "
    "aunque figure «IVA contenido». no_gravado y exento: importes del pie. percepciones: cada percepción con su importe y "
    "su descripción como figura; tipo = iva, iibb (Ingresos Brutos) o ganancias, en minúsculas, u otra si no es ninguna; "
    "jurisdiccion = solo en iibb, la letra de la provincia que figura (" + _JURISDICCIONES + "), vacía si no figura. "
    "otros_tributos: impuestos internos, al combustible u otros que no sean percepciones. "
    "Importes con punto decimal y sin separador de miles. Dejá vacío lo que no figure o no se lea.")


def _texto(valor, tope=_TOPE_TEXTO):
    """Una línea: un salto o una tabulación separan como un espacio (dos renglones escritos a mano no se pegan); el resto de
    lo no imprimible se quita."""
    if not isinstance(valor, str):
        return ""
    return " ".join("".join(ch if ch.isprintable() else " " if ch.isspace() else "" for ch in valor).split())[:tope]


def _lista(valor, campos, tope):
    if not isinstance(valor, list):
        return []
    return [{c: _texto(x.get(c)) for c in campos} for x in valor[:tope] if isinstance(x, dict)]


def normalizar(data):
    """La salida del lector es dato no confiable: solo textos cortos y listas acotadas, con todas las claves."""
    data = data if isinstance(data, dict) else {}
    fiscal = data.get("fiscal") if isinstance(data.get("fiscal"), dict) else {}
    return {**{k: _texto(data.get(k), _TOPE_CAMPO.get(k, _TOPE_TEXTO)) for k in _CAMPOS_LECTURA},
            "lineas": _lista(data.get("lineas"), ("descripcion", "cantidad", "importe"), _TOPES_LISTA["lineas"]),
            "fiscal": {**{k: _texto(fiscal.get(k)) for k in _CAMPOS_FISCAL},
                       **{k: _lista(fiscal.get(k), campos, _TOPES_LISTA[k]) for k, campos in _LISTAS_FISCAL.items()}}}


# ------------------------------------------------------------------------------------------- lecturas por lote
def _ruta_lecturas(lote_id):
    return estado.seguro(estado.raiz(lote_id) / f"{lote_id}.lecturas.json")


def guardar_lecturas(lote_id, lecturas):
    """{n: {"sha256", "lectura"}} de las fotos del lote, leídas al cerrarlo: de acá salen los números fiscales, no de lo
    que escribe el agente."""
    estado.guardar(_ruta_lecturas(lote_id), {"version": 1, "lote": lote_id, "fotos": lecturas})


def lectura(lote_id, n, sha256):
    """La lectura guardada de la foto n si es de esa misma foto; None si no hay (lote de antes, o lector caído)."""
    try:
        datos = estado.leer(_ruta_lecturas(lote_id))
    except estado.EstadoError:
        return None
    entrada = ((datos or {}).get("fotos") or {}).get(str(n))
    if not isinstance(entrada, dict) or entrada.get("sha256") != sha256 or not isinstance(entrada.get("lectura"), dict):
        return None
    return normalizar(entrada["lectura"])


# ------------------------------------------------------------------------------------------- números
def importe(valor, *, cero=True):
    """Texto del comprobante -> Decimal al centavo (vacío = 0 si `cero`). None si no se puede leer. Un importe impreso con
    tres o cuatro decimales (algunos comercios los imprimen así) se redondea al centavo."""
    texto = re.sub(r"[\s$]", "", valor or "")
    if not texto:
        return Decimal(0) if cero else None
    if "," in texto and "." in texto:
        texto = (texto.replace(".", "").replace(",", ".") if texto.rfind(",") > texto.rfind(".")
                 else texto.replace(",", ""))
    else:
        texto = texto.replace(",", ".")
    try:
        d = Decimal(texto)
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d.as_tuple().exponent < -4:
        return None
    return d.quantize(CENTAVO, ROUND_HALF_UP)


def digitos(valor):
    return re.sub(r"\D", "", valor or "")


def codigo_documento(valor):
    """«001», «COD. 06», «111» -> «1», «6», «111»; vacío si no hay un código."""
    d = digitos(valor)
    return str(int(d)) if d and len(d) <= 4 else ""


def numero_canonico(valor):
    """«00012-00034567», «12 34567», «A0001234» -> «12-34567», «A-1234»: sin separadores ni ceros a la izquierda."""
    partes = re.findall(r"[A-Z]+|[0-9]+", (valor or "").upper())
    return "-".join(p if p.isalpha() else str(int(p)) for p in partes)


def numero_odoo(valor):
    """El número como se escribe en Odoo: punto de venta y número separados (PPPPP-NNNNNNNN) cuando el comprobante los
    trae así; si no, el texto leído sin espacios. Odoo lo valida y la verificación lo compara sin ceros ni separadores."""
    partes = re.findall(r"[A-Z]+|[0-9]+", (valor or "").upper())
    if len(partes) == 2 and all(p.isdigit() for p in partes) and len(str(int(partes[0]))) <= 5 \
            and len(str(int(partes[1]))) <= 8:
        return f"{int(partes[0]):05d}-{int(partes[1]):08d}"
    return re.sub(r"\s", "", valor or "").upper()


def _monto(d):
    return format(d.quantize(CENTAVO), "f")


def _items(lineas):
    textos = []
    for x in lineas:
        descripcion = x.get("descripcion", "")
        if not descripcion:
            continue
        cantidad = x.get("cantidad", "").strip()
        textos.append(f"{cantidad} × {descripcion}" if cantidad and importe(cantidad) not in (None, Decimal(1)) else descripcion)
    return "; ".join(textos)[:300]


def desglose(lectura):
    """El desglose leído en texto, para la factura sin impuestos y para auditar."""
    if not lectura:
        return "Sin lectura automática del comprobante."
    f = lectura["fiscal"]
    partes = [f"Lectura del bot (sin verificar): {lectura.get('tipo') or 'comprobante'}"]
    if f["documento_letra"] or f["documento_codigo"]:
        partes.append(f"tipo {f['documento_letra']} código {f['documento_codigo']}".replace("  ", " "))
    for clave, rotulo in (("documento_numero", "número"), ("emisor_id_fiscal", "CUIT/RUT del emisor"), ("cae", "CAE")):
        if f[clave]:
            partes.append(f"{rotulo} {f[clave]}")
    partes += [f"neto {x['alicuota']} % {x['neto']} + IVA {x['importe']}" for x in f["iva"]]
    partes += [f"{rotulo} {f[clave]}" for clave, rotulo in (("no_gravado", "no gravado"), ("exento", "exento")) if f[clave]]
    partes += [f"{x['descripcion'] or rotulo} {x['importe']}" for clave, rotulo in
               (("percepciones", "percepción"), ("otros_tributos", "otro tributo")) for x in f[clave]]
    partes.append(f"total {lectura.get('moneda', '')} {lectura.get('importe', '')}".replace("  ", " "))
    items = _items(lectura["lineas"])
    if items:
        partes.append(f"ítems: {items}")
    return " · ".join(partes)[:1500]


def narracion(texto):
    return f"<p>{html.escape(texto)}</p>"


def nota(f):
    """La nota de la factura: el desglose leído, con qué tipo se carga si la configuración lo cambia y, si el CUIT/RUT se
    repetía, qué proveedor se eligió."""
    return "".join(narracion(t) for t in (f["desglose"], aviso_carga(f), f.get("proveedor_aviso"), aviso_redondeo(f),
                                          f.get("correccion_aviso")) if t)


def aviso_redondeo(f):
    return f"Redondeo del comprobante: {f['redondeo']}." if f.get("redondeo") else ""


def nota_factura(x):
    """La nota interna de la factura: la nota fiscal, si hay plan fiscal, y quién y con qué pagó, si la persona lo dijo.
    Vacía si no hay ninguna de las dos."""
    f = x.get("fiscal")
    return ((nota(f) if f else "") + (narracion(x["receptor_aviso"]) if x.get("receptor_aviso") else "")
            + (narracion(f"Pago: {x['pago_detalle']}") if x.get("pago_detalle") else ""))


def leido_distinto(f):
    """El código leído del comprobante si la configuración lo carga con otro tipo (cargar_como); si no, vacío."""
    leido = f.get("documento_leido", "")
    return leido if leido and leido != f["documento"] else ""


def aviso_carga(f):
    carga = f"tipo {f['documento']}" + (f" ({f['documento_nombre']})" if f.get("documento_nombre") else "")
    if f.get("documento_por_letra"):
        return f"El comprobante no trae impreso el código de su tipo: por su letra {f['letra']} y su tipo se carga con el {carga}."
    leido = leido_distinto(f)
    if not leido:
        return ""
    return f"El comprobante leído es de tipo {leido}: por la configuración del bot se carga con el {carga}."


def nombre_documento(f):
    """El tipo y el número como los ve la persona; si se carga con otro tipo, también el leído."""
    carga = f"{f.get('documento_nombre') or 'tipo ' + f['documento']} {f['numero']}"
    leido = leido_distinto(f)
    if not leido:
        return carga
    return f"{_texto((f.get('lectura') or {}).get('tipo'), 60) or 'Comprobante'} (código {leido}), se carga como {carga}"


def _sin_acentos(texto):
    return "".join(ch for ch in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(ch)).lower()


def codigo_por_letra(c, lec):
    """Sin código impreso: el código de documentos cuyo sin_codigo tiene la letra leída y cuyo patrón encuentra el tipo leído
    (en minúsculas y sin acentos). Si no hay uno solo, vacío: con dos candidatos no elige."""
    f = lec["fiscal"]
    letra, tipo = f["documento_letra"].strip().upper(), _sin_acentos(lec.get("tipo"))
    if not letra:
        return ""
    candidatos = [k for k, v in c["documentos"].items()
                  if v.get("sin_codigo") and v["sin_codigo"]["letra"] == letra and re.search(v["sin_codigo"]["tipo"], tipo)]
    return candidatos[0] if len(candidatos) == 1 else ""


def de_carga(c, codigo):
    """El código con el que se carga un tipo leído: el de su cargar_como o, si no lo tiene, el mismo."""
    return (c["documentos"].get(codigo) or {}).get("cargar_como", codigo)


# ------------------------------------------------------------------------------------------- de quién es el comprobante
def empresa_sugerida(cfg, accesibles, lec):
    """(empresa, motivo) de una foto: la única empresa accesible con id_fiscal igual al receptor leído («receptor»); si no
    hay una sola, la única cuyo emisor_id_fiscal_patron cumple el emisor leído («emisor»); si no, ("", ""). No elige
    entre dos."""
    if not lec:
        return "", ""
    f = lec["fiscal"]
    receptor, emisor = digitos(f["receptor_id_fiscal"]), digitos(f["emisor_id_fiscal"])
    empresas = {e: cfg["empresas"][e] for e in accesibles}
    for motivo, cumple in (("receptor", lambda c: receptor and c.get("id_fiscal") == receptor),
                           ("emisor", lambda c: emisor and c.get("emisor_id_fiscal_patron")
                            and re.fullmatch(c["emisor_id_fiscal_patron"], emisor))):
        candidatas = [e for e, c in empresas.items() if cumple(c)]
        if len(candidatas) == 1:
            return candidatas[0], motivo
    return "", ""


def un_digito(a, b):
    """Dos identificaciones del mismo largo que difieren en un solo dígito. Dos CUIT o RUT válidos nunca difieren en uno solo
    (cambiar un dígito cambia el verificador), así que la leída es la otra mal leída."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def receptor_ajeno(c, lec):
    """Los dígitos del receptor leído si la empresa tiene id_fiscal y el comprobante parece a nombre de otro; si no, vacío.
    El de la empresa leído con un dígito mal no es de otro (receptor_mal_leido)."""
    receptor = digitos(lec["fiscal"]["receptor_id_fiscal"]) if lec else ""
    return (receptor if c.get("id_fiscal") and receptor and receptor != c["id_fiscal"]
            and not un_digito(receptor, c["id_fiscal"]) else "")


def receptor_mal_leido(c, lec):
    """Los dígitos del receptor leído si son los de la empresa con un dígito mal leído; si no, vacío."""
    receptor = digitos(lec["fiscal"]["receptor_id_fiscal"]) if lec else ""
    return receptor if c.get("id_fiscal") and receptor and un_digito(receptor, c["id_fiscal"]) else ""


# Lo que la persona corrige de la lectura mirando el comprobante (gasto_preparar, «correccion» de cada gasto). El receptor no
# se corrige: solo puede ser la empresa (receptor_es_la_empresa).
CORREGIBLES = {"emisor_id_fiscal": "CUIT/RUT del emisor", "documento_numero": "número"}


def corregida(lec, correccion):
    """La lectura con lo que corrigió la persona; la guardada no cambia."""
    if not lec or not correccion:
        return lec
    lec = {**lec, "fiscal": {**lec["fiscal"], **correccion}}
    return {**lec, "referencia": correccion.get("documento_numero", lec.get("referencia", ""))}


def aviso_correccion(leida, correccion):
    """Qué corrigió la persona, para la nota de la factura."""
    if not correccion:
        return ""
    f = (leida or {}).get("fiscal") or {}
    return "Corregido por la persona sobre lo leído: " + "; ".join(
        f"{CORREGIBLES[k]} {v} (se leyó {f.get(k) or 'nada'})" for k, v in correccion.items()) + "."


# ------------------------------------------------------------------------------------------- armado (sin Odoo)
def sin_impuestos(motivo, lec, documento="", numero="", cuit="", documento_leido=""):
    return {"modo": SIN_IMPUESTOS, "motivo": motivo, "desglose": desglose(lec), "documento": documento,
            "documento_leido": documento_leido, "numero": numero, "cuit": cuit, "lectura": lec}


def armar(lec, x, c):
    """El plan fiscal del comprobante x (ya confirmado por la persona) a partir de su lectura guardada.
    Devuelve CON_IMPUESTOS con sus líneas, o SIN_IMPUESTOS con el motivo; nunca algo intermedio."""
    if not lec:
        return sin_impuestos("no hay lectura automática del comprobante", lec)
    f = lec["fiscal"]
    codigo, numero, cuit = codigo_documento(f["documento_codigo"]), f["documento_numero"], digitos(f["emisor_id_fiscal"])
    # Sin código impreso, el que la configuración declara para esa letra y ese tipo (documentos.<código>.sin_codigo).
    por_letra = "" if codigo else codigo_por_letra(c, lec)
    codigo = codigo or por_letra
    # El plan lleva los dos códigos: el leído y el de carga (el mismo, salvo un cargar_como), con el que se valida el
    # tipo en Odoo, se busca el duplicado y se escribe la factura.
    carga = de_carga(c, codigo)
    previo = {"documento": carga, "documento_leido": codigo, "numero": numero, "cuit": cuit}
    total = Decimal(x["importe"])
    leido = importe(lec.get("importe"), cero=False)
    if leido != total:
        return sin_impuestos(f"el total confirmado ({x['importe']}) no es el leído en la foto "
                              f"({lec.get('importe') or 'ilegible'})", lec, **previo)
    moneda = (lec.get("moneda") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", moneda) and moneda != x["moneda"]:    # «$» u otro símbolo no se compara
        return sin_impuestos(f"la moneda confirmada ({x['moneda']}) no es la leída ({moneda})", lec, **previo)
    if not codigo:
        return sin_impuestos("no leí el tipo de comprobante", lec, **previo)
    if codigo not in c["documentos"]:
        return sin_impuestos(f"el tipo de comprobante {codigo} no está configurado", lec, **previo)
    documento = c["documentos"][carga]
    if not numero_canonico(numero):
        return sin_impuestos("no leí el número del comprobante", lec, **previo)
    no_gravado, exento = importe(f["no_gravado"]), importe(f["exento"])
    otros = [(clave, leida, importe(leida.get("importe"), cero=False))
             for clave in ("percepciones", "otros_tributos") for leida in f[clave]]
    ivas = [(configuracion.alicuota(x.get("alicuota")), importe(x.get("neto"), cero=False),
             importe(x.get("importe"), cero=False)) for x in f["iva"]]
    if (no_gravado is None or exento is None or any(m is None or m <= 0 for *_, m in otros)
            or any(None in v for v in ivas)):
        return sin_impuestos("no pude leer un importe del desglose", lec, **previo)
    imp = c["impuestos"]
    # Ninguna línea sin IVA (la localización no deja publicar una): los otros tributos van con el no gravado si el
    # comprobante discrimina IVA y con el sin crédito si lo incluye, como el resto del comprobante.
    sin_iva = imp.get("no_gravado") if documento["iva"] == "discriminado" else imp["sin_credito"]
    if not sin_iva and any(clave == "otros_tributos" for clave, *_ in otros):
        return sin_impuestos("el comprobante trae otros tributos y no hay impuesto no gravado configurado", lec, **previo)
    items = _items(lec["lineas"])
    lineas, iva_total, redondeos = [], Decimal(0), []
    if documento["iva"] == "discriminado":
        if not ivas:
            return sin_impuestos("el comprobante discrimina IVA y no leí el desglose", lec, **previo)
        if len({a for a, *_ in ivas}) != len(ivas):
            return sin_impuestos("el desglose repite una alícuota", lec, **previo)
        for a, neto, iva in ivas:
            clave = configuracion.clave_alicuota(a)
            if clave not in imp["iva"]:
                return sin_impuestos(f"la alícuota {clave} % no está configurada", lec, **previo)
            calculado = (neto * a / 100).quantize(CENTAVO, ROUND_HALF_UP)
            if neto <= 0 or abs(calculado - iva) > TOLERANCIA:
                return sin_impuestos(f"el IVA {clave} % impreso ({_monto(iva)}) no es el neto por la alícuota", lec,
                                      **previo)
            if calculado != iva:
                redondeos.append(f"el IVA {clave} % impreso es {_monto(iva)} y el neto por la alícuota da {_monto(calculado)}")
            etiqueta = f"IVA {clave} %" + (f" · {items}" if items and not lineas else "")
            # El primer impuesto de la línea es su IVA; "iva", el importe impreso, que la verificación busca en la línea de
            # impuesto de la factura.
            lineas.append({"tipo": "iva", "etiqueta": etiqueta, "importe": _monto(neto), "impuestos": [imp["iva"][clave]],
                           "iva": _monto(iva)})
            iva_total += iva
        for campo, monto, rotulo in (("no_gravado", no_gravado, "No gravado"), ("exento", exento, "Exento")):
            if monto > 0:
                if campo not in imp:
                    return sin_impuestos(f"el comprobante trae {rotulo.lower()} y no hay impuesto configurado", lec,
                                          **previo)
                lineas.append({"tipo": campo, "etiqueta": rotulo, "importe": _monto(monto), "impuestos": [imp[campo]]})
        suma = sum((Decimal(l["importe"]) for l in lineas), iva_total) + sum(m for *_, m in otros)
        if abs(total - suma) > TOLERANCIA:
            return sin_impuestos(f"las partes del desglose suman {_monto(suma)} y el total es {x['importe']}", lec,
                                  **previo)
        if suma != total:
            # El redondeo va al neto de la línea mayor: la factura suma el total impreso y el IVA queda el impreso.
            mayor = max(lineas, key=lambda l: Decimal(l["importe"]))
            mayor["importe"] = _monto(Decimal(mayor["importe"]) + total - suma)
            redondeos.append(f"las partes impresas suman {_monto(suma)} y el total es {x['importe']}: la diferencia va al "
                             "neto")
    else:
        base = total - sum((m for *_, m in otros), Decimal(0))
        if base <= 0:
            return sin_impuestos("las percepciones y tributos leídos superan el total", lec, **previo)
        lineas.append({"tipo": "sin_credito", "etiqueta": items or "Comprobante", "importe": _monto(base),
                       "impuestos": [imp["sin_credito"]]})
    percepciones = []       # las que tienen impuesto configurado: van en su línea de impuesto, no en una de producto
    for clave, leida, monto in otros:
        descripcion = leida.get("descripcion", "")
        if clave == "otros_tributos":
            lineas.append({"tipo": "tributo", "etiqueta": f"{descripcion or 'Otro tributo'}: revisar",
                           "importe": _monto(monto), "impuestos": [sin_iva]})
        elif impuesto := impuesto_percepcion(imp, leida):
            previa = next((p for p in percepciones if p["impuesto"] == impuesto), None)
            if previa:      # dos percepciones con el mismo impuesto van en su única línea de impuesto
                previa.update(etiqueta=f"{previa['etiqueta']} + {descripcion or 'Percepción'}",
                              importe=_monto(Decimal(previa["importe"]) + monto))
            else:
                percepciones.append({"impuesto": impuesto, "etiqueta": descripcion or "Percepción", "importe": _monto(monto)})
        else:
            # Sin impuesto configurado, como antes: una línea sin impuesto, que Odoo no deja publicar hasta que
            # Administración la pasa a su impuesto.
            lineas.append({"tipo": "percepcion", "etiqueta": f"{descripcion or 'Percepción'}: pasar a impuesto",
                           "importe": _monto(monto), "impuestos": []})
    # El impuesto de cada percepción va en todas las líneas del comprobante: Odoo arma su línea de impuesto con su monto fijo
    # por cada línea, y la carga le pone el importe leído (estandar._percepciones).
    con_percepcion = sorted({p["impuesto"] for p in percepciones})
    for linea in lineas:
        if linea["tipo"] != "percepcion":
            linea["impuestos"] = linea["impuestos"] + con_percepcion
    return {"modo": CON_IMPUESTOS, "documento": carga, "documento_leido": codigo, "documento_id": documento["id"],
            "iva_documento": documento["iva"],
            "letra": f["documento_letra"][:2].upper(), "numero": numero, "numero_odoo": numero_odoo(numero), "cuit": cuit,
            "lineas": lineas, "percepciones": percepciones, "iva": _monto(iva_total), "desglose": desglose(lec),
            "lectura": lec, **({"documento_por_letra": True} if por_letra else {}),
            **({"redondeo": "; ".join(redondeos)} if redondeos else {})}


def impuesto_percepcion(imp, leida):
    """El impuesto configurado para una percepción leída, por tipo o por tipo y jurisdicción; 0 si no hay. No se infiere
    nada: sin el tipo o la jurisdicción leídos, no hay impuesto."""
    por_tipo = imp.get("percepciones", {}).get(leida.get("tipo", "").strip().lower())
    if isinstance(por_tipo, dict):
        por_tipo = por_tipo.get(leida.get("jurisdiccion", "").strip().upper())
    return por_tipo or 0


def lineas_impuesto(f):
    """{impuesto: importe} de las líneas de impuesto que tiene que tener la factura: el IVA de cada alícuota y cada
    percepción con impuesto, como se leyeron. None en un plan de antes de la dev.9, que no las lleva."""
    if "percepciones" not in f:
        return None
    esperado = {}
    for impuesto, monto in ([(l["impuestos"][0], l["iva"]) for l in f["lineas"] if l["tipo"] == "iva"]
                            + [(p["impuesto"], p["importe"]) for p in f["percepciones"]]):
        esperado[impuesto] = esperado.get(impuesto, Decimal(0)) + Decimal(monto)
    return {i: m for i, m in esperado.items() if m}


def impuesto_total(f):
    """El impuesto de la factura en Odoo (amount_tax): el IVA y las percepciones que van como impuesto."""
    return Decimal(f["iva"]) + sum((Decimal(p["importe"]) for p in f.get("percepciones", [])), Decimal(0))


def nombre_linea(x, linea):
    return f"{x['comercio']} {x['marker']} · {linea['etiqueta']}"[:400]


def lineas_factura(x):
    """[(nombre, importe, [impuestos])] de la factura, en orden. Sin plan fiscal o sin impuestos: la única línea de antes."""
    f = x.get("fiscal")
    if not f or f["modo"] != CON_IMPUESTOS:
        return [(f"{x['comercio']} {x['marker']}", x["importe"], [])]
    return [(nombre_linea(x, l), l["importe"], list(l["impuestos"])) for l in f["lineas"]]


def completa_administracion(f):
    if f["modo"] != CON_IMPUESTOS:
        return [f"Cargar proveedor, tipo y número de documento e impuestos (va sin impuestos porque {f['motivo']}); "
                "el desglose leído está en la factura."]
    tareas = ["Revisar contra la foto y confirmar."]
    if not f.get("partner_id") and not f.get("alta_proveedor"):
        tareas.append(f"Dar de alta al proveedor CUIT/RUT {f['cuit'] or 'ilegible'} y elegirlo.")
    elif f.get("proveedor_aviso"):
        tareas.append("Confirmar el proveedor elegido entre los que tienen el mismo CUIT/RUT.")
    if any(l["tipo"] == "percepcion" for l in f["lineas"]):
        tareas.append("Pasar cada percepción sin impuesto configurado a su impuesto y borrar su línea: Odoo no deja publicar "
                      "la factura antes.")
    if f.get("percepciones"):
        tareas.append("Las percepciones van como impuesto con el importe del comprobante. Si cambiás una línea, Odoo las "
                      "recalcula: revisá que sigan con ese importe.")
    if any(l["tipo"] == "tributo" for l in f["lineas"]):
        tareas.append("Decidir cómo se registra cada otro tributo.")
    return tareas


def proveedor_nuevo(f):
    a = f["alta_proveedor"]
    return f"proveedor nuevo, CUIT/RUT {a['id_fiscal']}, con los datos de {'ARCA' if a['pais'] == 'AR' else 'DGI'}"


def sin_alta(x):
    """El comprobante tal como se aprobó: el proveedor que resolvió el alta no forma parte de lo firmado."""
    f = x.get("fiscal")
    if not (f and f.get("alta_proveedor")):
        return x
    return {**x, "fiscal": {k: v for k, v in {**f, "partner_id": 0, "proveedor": ""}.items() if k != "proveedor_aviso"}}


def visible(f):
    """Lo que ve la persona de la factura en la propuesta."""
    if f["modo"] != CON_IMPUESTOS:
        return {"impuestos": "no", "motivo": f["motivo"], "completa_administracion": completa_administracion(f)}
    return {"impuestos": "sí", "documento": nombre_documento(f),
            "proveedor": (f.get("proveedor") or (proveedor_nuevo(f) if f.get("alta_proveedor") else
                                                 f"sin proveedor en Odoo (CUIT/RUT {f['cuit'] or 'ilegible'})")),
            **({"aviso_proveedor": f["proveedor_aviso"]} if f.get("proveedor_aviso") else {}),
            "lineas": [{"texto": l["etiqueta"], "importe": l["importe"]} for l in f["lineas"]],
            "iva": f["iva"],
            **({"percepciones": [{"texto": p["etiqueta"], "importe": p["importe"]} for p in f["percepciones"]]}
               if f.get("percepciones") else {}),
            "completa_administracion": completa_administracion(f)}


def resumen(f):
    """Una línea para la tarjeta del OK."""
    if f["modo"] != CON_IMPUESTOS:
        return f"sin impuestos ({f['motivo']})"
    partes = " + ".join(f"{l['etiqueta'].split(' · ')[0]} {l['importe']}" for l in f["lineas"])
    proveedor = f.get("proveedor") or (proveedor_nuevo(f) if f.get("alta_proveedor") else
                                       f"proveedor a dar de alta (CUIT/RUT {f['cuit'] or 'ilegible'})")
    if f.get("proveedor_aviso"):
        proveedor += f" (id {f['partner_id']}, elegido entre varios con el mismo CUIT/RUT)"
    percepciones = "".join(f" · {p['etiqueta']} {p['importe']}" for p in f.get("percepciones", []))
    return f"{nombre_documento(f)} · {proveedor} · {partes} · IVA {f['iva']}{percepciones}"


# ------------------------------------------------------------------------------------------- Odoo (solo lectura)
async def validar_impuestos(ctx, c, f, g):
    """Cada impuesto que usa la factura: de compra, activo, de la compañía, sin precio incluido y con la alícuota
    configurada (0 para no gravado, exento y sin crédito); el de una percepción, de monto fijo mayor que cero, para que Odoo
    arme su línea de impuesto. Un ID mal configurado es error: no cae a sin impuestos."""
    imp = c["impuestos"]
    esperado = {i: (Decimal(a), f"IVA {a} %") for a, i in imp["iva"].items()}
    for campo in configuracion.IMPUESTOS_SIN_ALICUOTA:
        if campo in imp:
            esperado.setdefault(imp[campo], (Decimal(0), campo.replace("_", " ")))
    for tipo, por_tipo in imp.get("percepciones", {}).items():
        for jurisdiccion, i in (por_tipo.items() if isinstance(por_tipo, dict) else [("", por_tipo)]):
            esperado.setdefault(i, (None, f"percepción {tipo}" + (f".{jurisdiccion}" if jurisdiccion else "")))
    usados = sorted({i for l in f["lineas"] for i in l["impuestos"]})
    if not usados:
        return
    filas = {r.get("id"): r for r in await g._odoo(ctx, "account.tax", "read", usados, fields=[
        "name", "type_tax_use", "company_id", "active", "amount_type", "amount", "price_include"])}
    for ident in usados:
        alic, rotulo = esperado[ident]
        r = filas.get(ident)
        try:
            monto = Decimal(str(r.get("amount"))) if r else None
        except InvalidOperation:
            monto = None
        if alic is None:
            propio, condicion = r and r.get("amount_type") == "fixed" and monto is not None and monto > 0, "de monto fijo"
        else:
            propio, condicion = monto == alic and (alic == 0 or r.get("amount_type") == "percent"), "con esa alícuota"
        if (not r or r.get("type_tax_use") != "purchase" or r.get("active") is not True or r.get("price_include")
                or g._rel(r.get("company_id")) != c["company_id"] or not propio):
            raise g.GastoError(f"El impuesto {ident} configurado como «{rotulo}» en la configuración del bot (gastos.json) no "
                               f"es un impuesto de compra activo de la compañía {c['company_id']} {condicion}, sin precio "
                               "incluido: corregí el ID en gastos.json o, si el ID es el correcto, revisá el impuesto en Odoo.")


async def validar_documento(ctx, c, f, g):
    ident = f["documento_id"]
    filas = await g._odoo(ctx, "l10n_latam.document.type", "read", [ident], fields=["name", "code", "active", "internal_type"])
    if (len(filas) != 1 or filas[0].get("id") != ident or filas[0].get("active") is not True
            or codigo_documento(str(filas[0].get("code") or "")) != f["documento"]
            or filas[0].get("internal_type") == "credit_note"):
        raise g.GastoError(f"El tipo de documento {ident} configurado para el código {f['documento']} en la configuración del "
                           "bot (gastos.json) no es un tipo activo de Odoo con ese código: corregí el ID en gastos.json.")
    f["documento_nombre"] = _texto(filas[0].get("name") or "", 60)


async def proveedor(ctx, c, f, g, *, mantener=0):
    """El proveedor por CUIT/RUT (empresa, no contacto). Acá no se crea: si falta, sin padrón la factura va sin proveedor y
    Administración lo da de alta; con padrón lo resuelve proveedores.py. Si el CUIT/RUT se repite, va el que tiene más
    facturas de proveedor publicadas en la compañía (a igual cantidad, el de ID menor) y la propuesta lo dice. Con mantener
    (la revalidación del OK), el elegido
    sigue valiendo mientras siga entre los del CUIT/RUT: una factura publicada en el medio no cambia la elección."""
    cuit = f["cuit"]
    f["partner_id"], f["proveedor"] = 0, ""
    f.pop("proveedor_aviso", None)
    if not cuit:
        return
    candidatos = [cuit] + ([f"{cuit[:2]}-{cuit[2:10]}-{cuit[10]}"] if len(cuit) == 11 else [])
    filas = await g._odoo(ctx, "res.partner", "search_read", [
        ("vat", "in", candidatos), ("parent_id", "=", False), "|",
        ("company_id", "=", c["company_id"]), ("company_id", "=", False)], fields=["name", "vat"],
        limit=_MAX_REPETIDOS, order="id")
    filas = sorted((r for r in filas if digitos(r.get("vat")) == cuit), key=lambda r: r["id"])
    if c.get('padron_proveedores'):
        from .proveedores import PENDIENTE
        filas = [r for r in filas if not (r.get('name') or '').startswith(PENDIENTE)]
    if not filas:
        return
    elegido = next((r for r in filas if mantener and r["id"] == mantener), None)
    if elegido is None and len(filas) == 1:
        elegido = filas[0]
    elif elegido is None:
        publicadas = {}
        for r in filas:
            publicadas[r["id"]] = await g._odoo(ctx, "account.move", "search_count", [
                ("company_id", "=", c["company_id"]), ("move_type", "=", "in_invoice"), ("state", "=", "posted"),
                ("commercial_partner_id", "=", r["id"])])
        elegido = max(filas, key=lambda r: (publicadas[r["id"]], -r["id"]))
        n = publicadas[elegido["id"]]
        empate = sum(1 for v in publicadas.values() if v == n) > 1
        cuantas = f"{n} factura{'' if n == 1 else 's'} de proveedor publicada{'' if n == 1 else 's'}"
        f["proveedor_aviso"] = (
            f"Había {len(filas)} proveedores con el CUIT/RUT {cuit}; usé {_texto(elegido.get('name') or '', 120)}, id "
            f"{elegido['id']}, " + (f"el de ID menor entre los que tienen {cuantas}." if empate else
                                    f"el que tiene más facturas de proveedor publicadas ({n})."))
    f["partner_id"], f["proveedor"] = elegido["id"], _texto(elegido.get("name") or "", 120)


async def ya_cargado(ctx, c, f, g, *, propio=0):
    """Una factura de proveedor de la compañía con el mismo tipo y número del mismo emisor, incluidos los borradores (Odoo
    recién frena el repetido al confirmar). El tipo es el de carga: con un cargar_como, el comprobante ya cargado lleva
    ese tipo y no el leído. Devuelve {id, name, state} o None."""
    if not f.get("documento") or not numero_canonico(f.get("numero")):
        return None
    documento = c["documentos"].get(f["documento"])
    if documento is None:
        return None
    canon = numero_canonico(f["numero"])
    cola = next((p for p in reversed(canon.split("-")) if p.isdigit()), canon)
    filas = await g._odoo(ctx, "account.move", "search_read", [
        ("company_id", "=", c["company_id"]), ("move_type", "=", "in_invoice"),
        ("l10n_latam_document_type_id", "=", documento["id"]), ("state", "!=", "cancel"),
        ("l10n_latam_document_number", "ilike", cola)],
        fields=["name", "state", "partner_id", "l10n_latam_document_number"], limit=50)
    filas = [r for r in filas if r.get("id") != propio and numero_canonico(r.get("l10n_latam_document_number")) == canon]
    if not filas:
        return None
    socios = sorted({g._rel(r.get("partner_id")) for r in filas} - {0})
    cuits = {}
    if socios and f.get("cuit"):
        cuits = {r["id"]: digitos(r.get("vat")) for r in await g._odoo(ctx, "res.partner", "read", socios, fields=["vat"])}
    for r in filas:
        socio = g._rel(r.get("partner_id"))
        if not socio or socio == f.get("partner_id") or (f.get("cuit") and cuits.get(socio) == f["cuit"]):
            return {"id": r["id"], "name": r.get("name") or "", "state": r.get("state") or ""}
    return None


def aviso_duplicado(x, f, previo):
    return (f"Foto {x['n']}: el comprobante {f.get('documento_nombre') or 'tipo ' + f['documento']} {f['numero']} ya está "
            f"en Odoo (factura #{previo['id']} {previo['name']}, {previo['state']}): no se vuelve a cargar. Excluí la foto "
            "o revisá esa factura.")

