"""Configuración de empresas y referencias nominales; no consulta el ERP."""
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from bot import odoo


class ConfigError(ValueError):
    pass


# Un error de `validar` lo corrige quien administra la configuración del bot, no Odoo: el texto lo dice para que nadie
# lo escale a contabilidad.
PREFIJO_CONFIG = "Configuración del bot (gastos.json): "
SUFIJO_CONFIG = " Lo corrige quien administra el bot; no es un problema de Odoo."


def positivo(v):
    return type(v) is int and v > 0


def clave(v):
    return isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", v)


def firma(v):
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def ids_por_destino(valor, destinos):
    """{"ids": {destino: id}} con un ID positivo para cada destino declarado, ni más ni menos; si no, None."""
    if (not isinstance(valor, dict) or set(valor) != {"ids"} or not isinstance(valor["ids"], dict)
            or set(valor["ids"]) != set(destinos) or any(not positivo(i) for i in valor["ids"].values())):
        return None
    return dict(valor["ids"])


def validar(raiz):
    try:
        return _validar(raiz)
    except ConfigError as exc:
        texto = str(exc)
        raise ConfigError(texto if texto.startswith(PREFIJO_CONFIG) else
                          f"{PREFIJO_CONFIG}{texto.rstrip('.')}.{SUFIJO_CONFIG}") from None


def _validar(raiz):
    if not isinstance(raiz, dict) or not raiz:
        raise ConfigError("Falta la configuración de gastos")
    raiz = copy.deepcopy(raiz)
    plana = "empresas" not in raiz
    campos = {"company_id", "zona_horaria", "diario", "etiqueta", "compania", "comprobante",
              "workflow", "imputador", "politica", "seguimiento", "padron_proveedores", "alta_proveedor", "textos", "destinos", "categorias", "rendicion", "cuentas",
              "impuestos", "documentos", *SIN_FIRMA}
    if raiz.keys() - (campos | {"retencion_dias", "lote"} if plana else {"empresas", "retencion_dias", "textos", "lote"}):
        raise ConfigError("La configuración de gastos contiene claves desconocidas")
    dias = raiz.get("retencion_dias", 90)
    if type(dias) is not int or not 30 <= dias <= 3650:
        raise ConfigError("retencion_dias debe ser un entero entre 30 y 3650")
    lote = _lote(raiz.get("lote", {}))
    empresas = ({"principal": {k: v for k, v in raiz.items() if k not in ("retencion_dias", "lote")}} if plana
                else raiz["empresas"])
    if not isinstance(empresas, dict) or not empresas:
        raise ConfigError("Declarar al menos una empresa")
    aliases, endpoints, secretos = {}, {}, {}
    for nombre, c in empresas.items():
        if not clave(nombre) or not isinstance(c, dict) or c.keys() - campos:
            raise ConfigError("Empresa inválida o con claves desconocidas")
        if not positivo(c.get("company_id")):
            raise ConfigError(f"Empresa {nombre}: company_id debe ser entero positivo")
        try:
            ZoneInfo(c.get("zona_horaria", ""))
        except (ValueError, KeyError, TypeError):
            raise ConfigError(f"Empresa {nombre}: zona_horaria IANA inválida") from None
        for campo in ("etiqueta", "compania", "comprobante", "workflow", "imputador", "politica"):
            if campo in c and not isinstance(c[campo], str):
                raise ConfigError(f"Empresa {nombre}: {campo} debe ser texto")
        if c.get('padron_proveedores') not in (None, 'AR', 'UY'):
            raise ConfigError('Padrón de proveedores no compatible')
        alta = c.get("alta_proveedor")
        if alta is not None and not (
                c.get("padron_proveedores") and isinstance(alta, dict) and alta.keys() == {"repo", "script"}
                and isinstance(alta["repo"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", alta["repo"])
                and isinstance(alta["script"], str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.py", alta["script"])
                and ".." not in alta["script"].split("/")):
            raise ConfigError(f"Empresa {nombre}: alta_proveedor declara repo y script, y requiere padron_proveedores")
        for campo in ("imputador", "politica", "seguimiento"):
            if c.get(campo) and not clave(c[campo]):
                raise ConfigError(f"Empresa {nombre}: nombre de {campo} inválido")
        textos = {**raiz.get("textos", {}), **c.get("textos", {})} if isinstance(raiz.get("textos", {}), dict) and isinstance(c.get("textos", {}), dict) else None
        if (textos is None or textos.keys() - {"gastos_requiere_acceso", "gastos_credenciales"}
                or any(not isinstance(v, str) or not v.strip() for v in textos.values())):
            raise ConfigError(f"Empresa {nombre}: textos inválidos")
        c["textos"] = textos
        if "rendicion" in c and type(c["rendicion"]) is not bool:
            raise ConfigError(f"Empresa {nombre}: rendicion debe ser true o false")
        if "cuentas" in c and "rendicion" not in c:
            raise ConfigError("cuentas requiere seleccionar explícitamente rendicion=true o false")
        destinos = c.get("destinos")
        implicito = "destinos" not in c and plana
        if "rendicion" in c and implicito:
            raise ConfigError("El circuito de facturas requiere destinos explícitos con sus nombres de secreto")
        if implicito:
            destinos = {"heredado": None}
        if not isinstance(destinos, dict) or not destinos:
            raise ConfigError(f"Empresa {nombre}: declarar destinos completos")
        capacidades_por_destino = {}
        for alias, d in destinos.items():
            if not clave(alias):
                raise ConfigError("Nombre de destino inválido")
            if d is None and implicito and alias == "heredado":
                continue
            if isinstance(d, dict) and "diario" in d:
                raise ConfigError(f"Empresa {nombre}, destino {alias}: el diario va en la empresa, con su ID por destino, "
                                  "no dentro del destino")
            if isinstance(d, dict) and "capacidades" in d:
                if "rendicion" not in c:
                    raise ConfigError("Las capacidades requieren seleccionar el circuito de facturas con rendicion=true o false")
                cap = d["capacidades"]
                if (not isinstance(cap, dict) or not {"factura", "rendicion"} <= cap.keys()
                        or cap.keys() - {"factura", "rendicion", "control_sin_contabilizar_confirmado"}
                        or cap["factura"] not in ("addon", "estandar")
                        or cap["rendicion"] not in ("vinculada", "revision_hr", "ninguna")
                        or type(cap.get("control_sin_contabilizar_confirmado", False)) is not bool):
                    raise ConfigError("Capacidades del destino inválidas o incompletas")
                if (not c["rendicion"] and cap["rendicion"] != "ninguna") or (cap["factura"] == "estandar" and cap["rendicion"] == "vinculada"):
                    raise ConfigError("La rendición del destino contradice la empresa o requiere un creador con addon")
                capacidades_por_destino[alias] = {**cap, "control_sin_contabilizar_confirmado": cap.get("control_sin_contabilizar_confirmado", False)}
                d = {k:v for k,v in d.items() if k != "capacidades"}
            try:
                d = odoo.validar_destino(d)
            except odoo.OdooError as exc:
                raise ConfigError(f"Empresa {nombre}, destino {alias}: {exc}") from None
            canon = odoo.identidad_destino(d)
            if alias in aliases and aliases[alias] != canon:
                raise ConfigError("Una clave de destino no puede nombrar dos ERPs distintos")
            if canon in endpoints and endpoints[canon] != alias:
                raise ConfigError("Dos alias no pueden nombrar el mismo ERP")
            aliases[alias], endpoints[canon] = canon, alias
            par = (d["secreto_login"], d["secreto_api_key"])
            for secreto in par:
                if secreto in secretos and secretos[secreto] != canon:
                    raise ConfigError("Cada destino ERP exige su propio par de nombres de secreto")
                secretos[secreto] = canon
            destinos[alias] = d
        c["destinos"] = destinos
        c["_capacidades"] = capacidades_por_destino
        _identificacion(nombre, c, destinos)
        diario = ids_por_destino(c.get("diario"), destinos)
        if diario is None:
            ejemplo = ", ".join(f'"{alias}": <ID>' for alias in destinos)
            raise ConfigError(f'Empresa {nombre}: el diario va por ID de Odoo, uno por destino ("diario": {{"ids": '
                              f'{{{ejemplo}}}}}), no por nombre: el nombre cambia con el idioma de Odoo')
        c["diario"] = {"ids": diario}
        if ("impuestos" in c or "documentos" in c) and "rendicion" not in c:
            raise ConfigError(f"Empresa {nombre}: impuestos y documentos son del circuito de facturas "
                              "(declarar rendicion=true o false)")
        if "impuestos" in c or "documentos" in c:
            c["impuestos"], c["documentos"] = _fiscal(nombre, c.get("impuestos"), c.get("documentos"), destinos)
        categorias = c.get("categorias", {})
        if not isinstance(categorias, dict):
            raise ConfigError(f"Empresa {nombre}: categorias debe ser un catálogo")
        for visible, ref in categorias.items():
            if (not isinstance(visible, str) or not visible.strip() or visible != visible.strip() or len(visible) > 120
                    or not visible.isprintable() or not isinstance(ref, dict)
                    or ref.keys() - {"default_code", "ids"}):
                raise ConfigError(f"Empresa {nombre}: referencia de categoría inválida")
            codigo, ids = ref.get("default_code"), ref.get("ids", {})
            if codigo is not None and (not isinstance(codigo, str) or not codigo.strip()):
                raise ConfigError(f"Categoría {visible}: default_code debe ser texto no vacío")
            if codigo is not None:
                ref["default_code"] = codigo.strip()
            if not isinstance(ids, dict) or any(k not in destinos or not positivo(v) for k, v in ids.items()):
                raise ConfigError(f"Categoría {visible}: IDs por destino inválidos")
            if not codigo and any(d not in ids for d in destinos):
                raise ConfigError(f"Categoría {visible}: catálogo incompleto; indicar ID para cada destino sin default_code")
        c["categorias"] = categorias
        if "rendicion" in c:
            cuentas = c.get("cuentas", {})
            if (not isinstance(cuentas, dict) or set(cuentas) != set(categorias)
                    or any(not isinstance(v, dict) or set(v) != {"ids"}
                           or not isinstance(v["ids"], dict) or set(v["ids"]) != set(destinos)
                           or any(not positivo(i) for i in v["ids"].values()) for v in cuentas.values())):
                raise ConfigError(f"Empresa {nombre}: cuentas debe declarar ID contable de cada categoría en cada destino")
            c["cuentas"] = cuentas
    return {"empresas": empresas, "plana": plana, "retencion_dias": dias, "lote": lote}


def _lote(valor):
    """El lote de gastos: uno solo para el plugin (el consumidor de lotes es uno), no uno por empresa.
    cierre_inactividad_min: null (el default) es sin cierre por inactividad, y el lote queda abierto hasta que la persona contesta
    «¿Es lo último?»; o los minutos sin material nuevo tras los que se cierra solo, un entero >= 1."""
    if not isinstance(valor, dict) or valor.keys() - {"cierre_inactividad_min"}:
        raise ConfigError("lote debe ser un objeto con cierre_inactividad_min")
    minutos = valor.get("cierre_inactividad_min")
    if minutos is not None and (type(minutos) is not int or minutos < 1):
        raise ConfigError("lote.cierre_inactividad_min debe ser null (sin cierre por inactividad) o un entero de minutos >= 1")
    return {"cierre_inactividad_min": minutos}


# Lo que dice de quién es un comprobante y adónde va sin preguntar: no cambia lo que se escribe en Odoo, así que no entra en
# la firma de la corrida (una propuesta preparada antes de agregarlas sigue valiendo).
SIN_FIRMA = ("id_fiscal", "emisor_id_fiscal_patron", "destino_por_defecto")


def _identificacion(nombre, c, destinos):
    """id_fiscal: los dígitos de la identificación fiscal de la empresa, como figura en el comprobante a su nombre.
    emisor_id_fiscal_patron: expresión regular que cumplen todos los dígitos del emisor de un comprobante de esa empresa (el
    formato del país), para cuando el receptor no figura. destino_por_defecto: el destino que se usa si no se pide otro."""
    if "id_fiscal" in c and (not isinstance(c["id_fiscal"], str) or not re.fullmatch(r"[0-9]{5,20}", c["id_fiscal"])):
        raise ConfigError(f"Empresa {nombre}: id_fiscal va con los dígitos de su identificación fiscal, sin guiones ni "
                          "espacios (por ejemplo «30999999990»)")
    if "emisor_id_fiscal_patron" in c:
        patron = c["emisor_id_fiscal_patron"]
        try:
            valido = isinstance(patron, str) and 0 < len(patron) <= 200 and re.compile(patron) is not None
        except re.error:
            valido = False
        if not valido:
            raise ConfigError(f"Empresa {nombre}: emisor_id_fiscal_patron tiene que ser una expresión regular sobre los "
                              "dígitos del emisor (por ejemplo «[0-9]{11}»)")
    if "destino_por_defecto" in c and (not isinstance(c["destino_por_defecto"], str)
                                       or c["destino_por_defecto"] not in destinos):
        raise ConfigError(f"Empresa {nombre}: destino_por_defecto tiene que ser uno de sus destinos "
                          f"({', '.join(destinos)})")


def cierre_inactividad(raiz):
    """Los minutos que se registran en el consumidor de lotes: float("inf") sin cierre por inactividad (el motor no acepta None:
    lotes._tick compara contra cierre_min * 60, y con infinito no cierra nunca)."""
    minutos = validar(raiz)["lote"]["cierre_inactividad_min"]
    return float("inf") if minutos is None else minutos


IVA_DOCUMENTO = ("discriminado", "incluido")
IMPUESTOS_SIN_ALICUOTA = ("no_gravado", "exento", "sin_credito")


def alicuota(valor):
    """«21», «10.5» -> Decimal; None si no es un porcentaje de hasta dos decimales, de 0 a menos de 100."""
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite() or d < 0 or d >= 100 or d.as_tuple().exponent < -2:
        return None
    return d


def clave_alicuota(d):
    """Decimal("10.50") -> "10.5"; Decimal("21") -> "21": la forma de las claves de impuestos.iva."""
    return format(d.normalize(), "f")


def _fiscal(nombre, impuestos, documentos, destinos):
    """impuestos: {"iva": {"<alícuota>": {"ids"}}, "no_gravado"/"exento"/"sin_credito": {"ids"}}; documentos:
    {"<código impreso>": {"ids", "iva": "discriminado"|"incluido"}}. Los dos van juntos, con un ID por destino.
    Un código también puede ir como {"cargar_como": "<otro código>"}: el comprobante se carga con el tipo, los IDs y el IVA
    de ese otro código, que tiene que estar en la misma empresa con sus ids (no ser otro cargar_como)."""
    if (not isinstance(impuestos, dict) or not isinstance(documentos, dict) or not documentos
            or impuestos.keys() - {"iva", *IMPUESTOS_SIN_ALICUOTA, "percepciones"}
            or not isinstance(impuestos.get("iva"), dict) or not impuestos["iva"]):
        raise ConfigError(f"Empresa {nombre}: impuestos (con «iva» por alícuota) y documentos van juntos")
    iva = {}
    for clave_iva, valor in impuestos["iva"].items():
        d = alicuota(clave_iva) if isinstance(clave_iva, str) else None
        ids = ids_por_destino(valor, destinos)
        if d is None or ids is None or clave_alicuota(d) != clave_iva:
            raise ConfigError(f"Empresa {nombre}: impuestos.iva.{clave_iva}: la alícuota va en % como texto "
                              "(«21», «10.5») con el ID del impuesto de compra en cada destino")
        iva[clave_iva] = {"ids": ids}
    salida = {"iva": iva}
    for campo in IMPUESTOS_SIN_ALICUOTA:
        if campo in impuestos:
            ids = ids_por_destino(impuestos[campo], destinos)
            if ids is None:
                raise ConfigError(f"Empresa {nombre}: impuestos.{campo}: falta el ID del impuesto en cada destino")
            salida[campo] = {"ids": ids}
    if "percepciones" in impuestos:
        salida["percepciones"] = _percepciones(nombre, impuestos["percepciones"], destinos)
        de_iva = {(d, v["ids"][d]) for v in (*iva.values(), *(salida[k] for k in IMPUESTOS_SIN_ALICUOTA if k in salida))
                  for d in destinos}
        for ruta, ids in hojas_percepciones(salida["percepciones"]):
            if any((d, i) in de_iva for d, i in ids.items()):
                raise ConfigError(f"Empresa {nombre}: impuestos.percepciones.{ruta}: el ID ya es de un impuesto de IVA; la "
                                  "percepción va con su propio impuesto")
    docs, como = {}, {}
    for codigo, valor in documentos.items():
        if (isinstance(codigo, str) and re.fullmatch(r"[1-9][0-9]{0,3}", codigo) and isinstance(valor, dict)
                and set(valor) == {"cargar_como"}):
            como[codigo] = valor["cargar_como"]
            continue
        if (not isinstance(codigo, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", codigo) or not isinstance(valor, dict)
                or set(valor) - {"sin_codigo"} != {"ids", "iva"} or valor["iva"] not in IVA_DOCUMENTO
                or ids_por_destino({"ids": valor["ids"]}, destinos) is None):
            raise ConfigError(f"Empresa {nombre}: documentos.{codigo}: el código impreso del tipo de comprobante, sin ceros "
                              "a la izquierda, con «iva» discriminado o incluido y el ID del tipo en cada destino, o "
                              "«cargar_como» con otro código de la empresa")
        docs[codigo] = {"ids": dict(valor["ids"]), "iva": valor["iva"]}
        if "sin_codigo" in valor:
            docs[codigo]["sin_codigo"] = _sin_codigo(nombre, codigo, valor["sin_codigo"])
    if any(d["iva"] == "incluido" for d in docs.values()) and "sin_credito" not in salida:
        raise ConfigError(f"Empresa {nombre}: un documento con IVA incluido necesita impuestos.sin_credito "
                          "(el impuesto de la empresa para comprobantes sin crédito fiscal)")
    for codigo, destino in como.items():
        if isinstance(destino, str) and destino in como:
            raise ConfigError(f"Empresa {nombre}: documentos.{codigo}.cargar_como: «{destino}» también es un cargar_como; "
                              "apuntá al código con el que se carga, que lleva sus ids e iva")
        if not isinstance(destino, str) or destino not in docs:
            raise ConfigError(f"Empresa {nombre}: documentos.{codigo}.cargar_como: tiene que ser otro código de documentos "
                              "de la misma empresa, con sus ids e iva")
        docs[codigo] = {"cargar_como": destino}
    return salida, docs


def _sin_codigo(nombre, codigo, valor):
    """{"letra", "tipo"}: el comprobante sin código impreso se carga con este código si su letra es «letra» y el patrón «tipo»
    (expresión regular sobre el tipo leído, en minúsculas y sin acentos) lo encuentra (fiscal.codigo_por_letra)."""
    ok = (isinstance(valor, dict) and set(valor) == {"letra", "tipo"} and isinstance(valor["letra"], str)
          and re.fullmatch(r"[A-Z]{1,2}", valor["letra"]) and isinstance(valor["tipo"], str) and 0 < len(valor["tipo"]) <= 200)
    if ok:
        try:
            re.compile(valor["tipo"])
        except re.error:
            ok = False
    if not ok:
        raise ConfigError(f"Empresa {nombre}: documentos.{codigo}.sin_codigo: «letra» (A, B, C…) y «tipo», una expresión "
                          "regular válida sobre el tipo leído, en minúsculas y sin acentos")
    return {"letra": valor["letra"], "tipo": valor["tipo"]}


def _percepciones(nombre, valor, destinos):
    """{"<tipo>": {"ids"}} o {"<tipo>": {"<jurisdicción>": {"ids"}}}: el impuesto de compra de cada percepción, por tipo
    (iva, ganancias) o por tipo y jurisdicción (iibb, uno por provincia), con un ID por destino. La clave sale de lo leído
    en el comprobante; una percepción sin clave configurada va en una línea aparte, sin impuesto."""
    error = ConfigError(f"Empresa {nombre}: impuestos.percepciones: por tipo en minúsculas («iva», «ganancias»), con el ID "
                        "del impuesto de compra en cada destino, o por tipo y jurisdicción en mayúsculas («iibb»: {«B»: …})")
    if not isinstance(valor, dict) or not valor:
        raise error
    salida = {}
    for tipo, por_tipo in valor.items():
        if not clave(tipo) or not isinstance(por_tipo, dict) or not por_tipo:
            raise error
        if "ids" in por_tipo:
            if (ids := ids_por_destino(por_tipo, destinos)) is None:
                raise error
            salida[tipo] = {"ids": ids}
            continue
        salida[tipo] = {}
        for jurisdiccion, ref in por_tipo.items():
            ids = ids_por_destino(ref, destinos)
            if not isinstance(jurisdiccion, str) or not re.fullmatch(r"[A-Z0-9]{1,3}", jurisdiccion) or ids is None:
                raise error
            salida[tipo][jurisdiccion] = {"ids": ids}
    return salida


def hojas_percepciones(percepciones):
    """[("iva", {"ids"}), ("iibb.B", {"ids"}), …]: cada impuesto de percepción configurado, con su ruta."""
    hojas = []
    for tipo, v in percepciones.items():
        if "ids" in v:
            hojas.append((tipo, v["ids"]))
        else:
            hojas += [(f"{tipo}.{jurisdiccion}", w["ids"]) for jurisdiccion, w in v.items()]
    return hojas


def empleado(user, empresa, destino, plana=False):
    extra = getattr(user, "odoo_ids_extra", None) or {}
    if not isinstance(extra, dict):
        raise ConfigError("Vínculos ERP inválidos")
    mapa = extra.get("empleados_gastos_destinos", {})
    comunes = extra.get("empleados_gastos", {})
    if not isinstance(mapa, dict) or not isinstance(mapa.get(empresa, {}), dict) or not isinstance(comunes, dict):
        raise ConfigError("Vínculos ERP inválidos")
    v = mapa.get(empresa, {}).get(destino)
    if v is None:
        v = comunes.get(empresa)
    if v is None and plana and destino == "heredado":
        v = extra.get("empleado_gastos")
    return v if positivo(v) else 0


def accesibles(cfg, user):
    extra = getattr(user, "odoo_ids_extra", None) or {}
    nominales = extra.get("usuarios_erp", {}) if isinstance(extra, dict) else None
    if not isinstance(nominales, dict):
        raise ConfigError("Vínculos nominales ERP inválidos")
    resultado = {}
    for e, c in cfg["empresas"].items():
        ds = [d for d in c["destinos"] if
              (positivo(nominales.get(d)) if "rendicion" in c else empleado(user, e, d, cfg["plana"]))]
        if ds:
            resultado[e] = ds
    return resultado


def identidad_heredada():
    from bot import config
    return odoo.identidad_destino({"url": config.ODOO_URL, "db": config.ODOO_DB,
        "link_url": config.ODOO_LINK_URL or config.ODOO_URL,
        "secreto_login": "ODOO_LOGIN", "secreto_api_key": "ODOO_API_KEY"})


def seleccionar(raiz, user, args):
    cfg = validar(raiz)
    permitidas = accesibles(cfg, user)
    empresa = args.get("empresa")
    if not empresa and len(permitidas) == 1:
        empresa = next(iter(permitidas))
    if not empresa:
        raise ConfigError("Elegí empresa con gasto_catalogo; no se infiere entre varias empresas")
    if not clave(empresa) or empresa not in permitidas:
        raise ConfigError("Empresa no disponible para tu empleado nominal")
    c = cfg["empresas"][empresa]
    destino = args.get("destino")
    if not destino and len(c["destinos"]) == 1:
        destino = next(iter(c["destinos"]))
    if not destino and c.get("destino_por_defecto") in permitidas[empresa]:
        destino = c["destino_por_defecto"]      # el otro destino, solo si se lo pide
    if not destino:
        raise ConfigError("Elegí destino; cada corrida requiere su propuesta y OK propios")
    if not clave(destino) or destino not in permitidas[empresa]:
        raise ConfigError("Destino no disponible o sin empleado nominal vinculado")
    d = c["destinos"][destino]
    # El destino heredado conserva la URL/base global, nunca la identidad de otro ERP.
    if d is None:
        try:
            identidad = identidad_heredada()
        except odoo.OdooError:
            raise ConfigError("Configurar el destino ERP heredado antes de rendir") from None
    else:
        identidad = odoo.identidad_destino(d)
    referencias = {n: ({"id": ref["ids"][destino]} if destino in ref.get("ids", {}) else
                       {"default_code": ref.get("default_code")}) for n, ref in c["categorias"].items()}
    efectivo = {k: copy.deepcopy(v) for k, v in c.items() if k not in ("destinos", "categorias", "_capacidades")}
    if d and "company_id" in d:
        efectivo["company_id"] = d["company_id"]
    efectivo["diario"] = c["diario"]["ids"][destino]
    if "impuestos" in c:
        imp = c["impuestos"]
        efectivo["impuestos"] = {"iva": {k: v["ids"][destino] for k, v in imp["iva"].items()},
                                 **{k: imp[k]["ids"][destino] for k in IMPUESTOS_SIN_ALICUOTA if k in imp}}
        if "percepciones" in imp:
            efectivo["impuestos"]["percepciones"] = {
                t: v["ids"][destino] if "ids" in v else {j: w["ids"][destino] for j, w in v.items()}
                for t, v in imp["percepciones"].items()}
        efectivo["documentos"] = {k: dict(v) if "cargar_como" in v else
                                  {"id": v["ids"][destino], "iva": v["iva"],
                                   **({"sin_codigo": dict(v["sin_codigo"])} if "sin_codigo" in v else {})}
                                  for k, v in c["documentos"].items()}
    efectivo.update(empresa=empresa, destino=destino, categorias=referencias, erp=list(identidad), endpoint=d)
    if "rendicion" in c:
        efectivo["cuentas"] = {n: v["ids"][destino] for n, v in c["cuentas"].items()}
    # La política y la activación de RRHH no forman parte de la aprobación fiscal.
    fuera = (*SIN_FIRMA, "politica", "rendicion") if "rendicion" in c else SIN_FIRMA
    para_firmar = {k: v for k, v in efectivo.items() if k not in fuera}
    if "rendicion" in c:
        para_firmar = {**para_firmar, "circuito": "facturas/1"}
    cap = c["_capacidades"].get(destino, {"factura":"addon", "rendicion":"vinculada" if c.get("rendicion") else "ninguna", "control_sin_contabilizar_confirmado":False})
    if cap["factura"] != "addon":
        para_firmar = {**para_firmar, "capacidad_factura":cap["factura"]}
    efectivo["firma"] = firma(para_firmar)
    efectivo["capacidades"] = cap
    efectivo.update(tz=ZoneInfo(c["zona_horaria"]), plana=cfg["plana"],
                    legacy=cfg["plana"] and destino == "heredado" and d is None,
                    retencion_dias=cfg["retencion_dias"],
                    declarados={n: firma(list(odoo.identidad_destino(obj))) if obj is not None else firma(list(identidad))
                                for n, obj in c["destinos"].items()})
    efectivo["erp_id"] = firma(efectivo["erp"])
    efectivo["employee_id"] = 0 if "rendicion" in c else empleado(user, empresa, destino, cfg["plana"])
    efectivo.setdefault("etiqueta", "")
    efectivo.setdefault("compania", efectivo["etiqueta"] or empresa)
    efectivo.setdefault("comprobante", "")
    return efectivo
