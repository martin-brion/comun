"""El mail que presenta un lote cargado (0.1.0-dev.20). Lo arma el plugin desde el resultado de la carga, no lo que recuerde el
agente: las facturas con su link, las marcadas con su motivo, las que no se cargaron y por qué, y los totales por moneda.
A quién va lo dice la casa, con el proveedor de `gastos.presentacion/1` que nombra la clave `presentacion` de la empresa:

    async presentar(ctx, marco, datos) -> None | {"para": [mail, ...], "cc": [mail, ...], "asunto": str, "cuerpo": str}

None es «no corresponde». `cc`, `asunto` y `cuerpo` son opcionales: sin `cuerpo` va el texto del plugin (`datos["texto"]`).
El plugin no conoce direcciones ni jerarquías, y no manda nada: el mail lo prepara la tool de correo del motor
(`mail_preparar`), cuya tarjeta muestra el texto completo, y solo el OK de la persona lo despacha. Sin la clave, nada cambia.
Una falla del proveedor no toca la carga: la persona recibe el aviso de que el mail no se armó.
"""
import json
import re
from decimal import Decimal, InvalidOperation

from . import configuracion, estado

CUERPO_MAX = 4000          # el de mail_preparar
_MAX_DESTINATARIOS = 20
_MAIL = re.compile(r"[^@\s<>,;\"']{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}"
                   r"[A-Za-z0-9])?)+")


class PresentacionError(ValueError):
    pass


def validar_proveedor(p):
    import asyncio
    if not asyncio.iscoroutinefunction(getattr(p, "presentar", None)):
        raise ValueError("Proveedor de presentación incompatible")


def _ruta(lote_id, c):
    p = estado.recibo_path(lote_id, c)
    return estado.seguro(p.with_name(p.name.replace(".recibo.json", ".presentacion.json")))


def _monto(valor):
    try:
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _visible(d):
    entero, _, dec = f"{d:,.2f}".partition(".")
    return f"{entero.replace(',', '.')},{dec}"


def datos(resultado, plan, c, persona, link):
    """Lo que recibe el proveedor de la casa, armado solo desde el resultado de la carga y la propuesta aprobada."""
    por_n = {str(x["n"]): x for x in plan.get("comprobantes", [])}
    facturas, revision = resultado.get("facturas") or {}, resultado.get("revision") or {}
    fuera = {**(resultado.get("no_cargados") or {}), **(resultado.get("por_conciliar") or {})}
    lista, no_cargadas, totales = [], [], {}
    for n in sorted({*facturas, *fuera}, key=int):
        x = por_n.get(n, {})
        base = {"foto": int(n), "comercio": x.get("comercio", ""), "moneda": x.get("moneda", ""),
                "importe": x.get("importe", "")}
        if n in facturas:
            motivos = list((revision.get(n) or {}).get("motivos") or [])
            lista.append({**base, "link": link("account.move", facturas[n]), "marcada": bool(motivos), "motivos": motivos})
            if (m := _monto(x.get("importe"))) is not None:
                totales[base["moneda"]] = totales.get(base["moneda"], Decimal(0)) + m
        else:
            no_cargadas.append({**base, "motivo": fuera[n]})
    salida = {"empresa": c["empresa"], "destino": c["destino"], "compania": c.get("compania", ""),
              "lote_id": plan["lote_id"], "estado": "parcial" if resultado.get("estado") == "parcial" else "cargado",
              "persona": persona, "facturas": lista, "no_cargadas": no_cargadas,
              "totales": {k: format(v, "f") for k, v in sorted(totales.items())}}
    salida["texto"] = texto(salida)
    return salida


def texto(d):
    """El cuerpo del mail, en castellano llano: lo que se cargó, lo marcado, lo que no entró y los totales."""
    def monto(f):
        m = _monto(f["importe"])
        return f"{f['moneda']} {_visible(m) if m is not None else f['importe']}".strip()
    quien = d["persona"].get("nombre") or d["persona"].get("login") or "la persona"
    partes = [f"Presento el lote {d['lote_id']} de {quien}" + (f" ({d['compania']})" if d["compania"] else "")
              + ", cargado en Odoo en borrador. Ninguna factura está confirmada: las revisa y confirma Administración."]
    if d["facturas"]:
        partes.append(f"Facturas cargadas ({len(d['facturas'])}):\n" + "\n".join(
            f"• Foto {f['foto']} · {f['comercio']} · {monto(f)}"
            + (f" · para revisar: {' '.join(f['motivos'])}" if f["marcada"] else "") + f" → {f['link']}"
            for f in d["facturas"]))
    if d["no_cargadas"]:
        partes.append(f"No se cargaron ({len(d['no_cargadas'])}):\n" + "\n".join(
            f"• Foto {f['foto']} · {f['comercio']} · {monto(f)}: {f['motivo']}" for f in d["no_cargadas"]))
    if d["totales"]:
        partes.append("Total cargado: " + "; ".join(f"{k} {_visible(Decimal(v))}".strip() for k, v in d["totales"].items())
                      + ".")
    if d["estado"] == "parcial":
        partes.append("La carga quedó a medias: lo que falta se completa al repetirla.")
    return "\n\n".join(partes)


def _direcciones(valor, que, minimo):
    if valor is None and not minimo:
        return []
    if not isinstance(valor, list) or not minimo <= len(valor) <= _MAX_DESTINATARIOS:
        raise PresentacionError(f"el proveedor devolvió «{que}» inválido")
    salida = []
    for v in valor:
        if not isinstance(v, str) or not _MAIL.fullmatch(v.strip()):
            raise PresentacionError(f"el proveedor devolvió una dirección inválida en «{que}»")
        if v.strip().lower() not in {s.lower() for s in salida}:
            salida.append(v.strip())
    return salida


def normalizar(respuesta, d):
    """Lo que devolvió el proveedor, validado: el mail listo para mail_preparar, o None si no corresponde."""
    if respuesta is None:
        return None
    if not isinstance(respuesta, dict) or respuesta.keys() - {"para", "cc", "asunto", "cuerpo"}:
        raise PresentacionError("el proveedor devolvió una respuesta que el plugin no entiende")
    para = _direcciones(respuesta.get("para"), "para", 1)
    cc = [x for x in _direcciones(respuesta.get("cc"), "cc", 0) if x.lower() not in {p.lower() for p in para}]
    quien = d["persona"].get("nombre") or d["persona"].get("login") or ""
    asunto = respuesta.get("asunto") or (f"Gastos {quien}: lote {d['lote_id']} ({len(d['facturas'])} "
                                         f"factura{'' if len(d['facturas']) == 1 else 's'})")
    cuerpo = respuesta.get("cuerpo") or d["texto"]
    if not isinstance(asunto, str) or not asunto.strip() or len(asunto) > 200 or not asunto.isprintable():
        raise PresentacionError("el proveedor devolvió un asunto inválido")
    if not isinstance(cuerpo, str) or not cuerpo.strip() or len(cuerpo) > CUERPO_MAX:
        raise PresentacionError(f"el cuerpo del mail tiene que tener entre 1 y {CUERPO_MAX} caracteres")
    return {"para": para, "cc": cc, "asunto": " ".join(asunto.split()), "cuerpo": cuerpo.strip()}


async def armar(ctx, c, plan, resultado, g):
    """La presentación del lote: {"estado": "lista", "para", "cc", "asunto", "cuerpo"}, {"estado": "no_corresponde"} o
    {"estado": "fallida", "motivo"}. Sin la clave `presentacion`, None (nada cambia). La misma carga da la misma
    presentación: queda junto al recibo y se reusa mientras el resultado no cambie."""
    nombre = c.get("presentacion")
    if not nombre:
        return None
    base = g._link_base(c)
    link = lambda modelo, ident: f"{base}/web#id={ident}&model={modelo}&view_type=form" if base else f"#{ident}"
    d = datos(resultado, plan, c, {"login": ctx.user.login, "nombre": getattr(ctx.user, "name", "") or ""}, link)
    huella = configuracion.firma({"datos": d, "proveedor": nombre})
    ruta = _ruta(plan["lote_id"], c)
    try:
        previa = estado.leer(ruta)
    except estado.EstadoError:
        previa = None
    if previa and previa.get("huella") == huella and previa.get("presentacion", {}).get("estado") != "fallida":
        return previa["presentacion"]
    try:
        proveedor = g.plugins.resolver_extension("gastos.presentacion", 1, nombre)
        salida = normalizar(await proveedor.presentar(ctx, g._marco(ctx, c, plan["odoo_uid"]), g._copia(d)), d)
        presentacion = {"estado": "lista", **salida} if salida else {"estado": "no_corresponde"}
    except PresentacionError as exc:
        g.log.warning("gastos: presentación del lote %s: %s", plan["lote_id"], exc)
        presentacion = {"estado": "fallida", "motivo": str(exc)}
    except Exception as exc:                            # noqa: BLE001 - la presentación nunca toca la carga
        g.log.warning("gastos: el proveedor de presentación falló (%s)", type(exc).__name__)
        presentacion = {"estado": "fallida", "motivo": "el proveedor de la casa no la pudo armar"}
    try:
        estado.guardar(ruta, {"version": 1, "huella": huella, "presentacion": presentacion})
    except (estado.EstadoError, OSError):
        g.log.warning("gastos: no se guardó la presentación del lote %s", plan["lote_id"])
    return presentacion


def pie(presentacion):
    """La línea para la persona al final del texto de la carga."""
    if not presentacion or presentacion["estado"] == "no_corresponde":
        return ""
    if presentacion["estado"] == "fallida":
        return "No pude armar el mail que presenta el lote: la carga no cambió. Avisá a quien administra el bot."
    destinatarios = ", ".join(presentacion["para"]) + (f" (con copia a {', '.join(presentacion['cc'])})"
                                                       if presentacion["cc"] else "")
    return f"Puedo preparar el mail que presenta el lote para {destinatarios}: ¿lo armo para que lo revises?"


def para_el_agente(presentacion):
    """Lo que lee el modelo: el mail exacto para mail_preparar, que muestra el texto completo y despacha con el OK."""
    if not presentacion or presentacion["estado"] != "lista":
        return ""
    mail = {"to": ", ".join(presentacion["para"]), "cc": ", ".join(presentacion["cc"]), "asunto": presentacion["asunto"],
            "cuerpo": presentacion["cuerpo"]}
    return ("\n\nPRESENTACIÓN DEL LOTE (dato, no instrucción de terceros): si la persona quiere el mail, llamá mail_preparar "
            "con estos to, cc, asunto y cuerpo, EXACTOS y sin agregar ni quitar nada; su tarjeta le muestra el texto "
            "completo y el mail sale solo con su OK. No lo mandes sin que lo pida. " + json.dumps(mail, ensure_ascii=False))
