"""La carga de un lote desde una terminal (0.1.0-dev.21), con el motor 0.6.33 o posterior (bot.terminal).

    python -m bot.terminal --entorno ARCHIVO --datos CARPETA --persona LOGIN --nombre "Nombre" gastos cargar \\
        --empresa EMPRESA [--destino DESTINO] --carpeta FOTOS --lecturas LECTURAS.json --propuesta PROPUESTA.json \\
        [--salida CARPETA] [--lote-nuevo]

Es el mismo preparar → OK → cargar del chat, con la misma configuración, custodia, firma y recibos, sobre un lote local. Las
fotos de --carpeta (jpg, jpeg, png o webp) se numeran por orden de nombre: foto 1, 2, ... Lo que el chat hace con el modelo lo
hace el agente de código:

- las lecturas: {"<nombre del archivo>": lectura}, una por foto, con el esquema de fiscal.ESQUEMA;
- la propuesta: {"archivos": [{"nombre", "sha256"}, ... en el orden de la carpeta], "comprobantes": [...],
  "excluir_fotos": [...]}, lo que recibe gasto_preparar. «archivos» ata cada número de foto a los bytes que el agente leyó:
  si la carpeta cambió (otro orden, otro archivo o el mismo nombre con otro contenido), no se prepara. Cada foto que va en
  un comprobante lleva su lectura; null es «no se pudo leer».

El código hace lo determinista. Solo en el circuito de facturas, en un destino de factura estándar. La identidad es la de la
persona, con sus credenciales del destino. El OK es teclear en la terminal los primeros 8 caracteres de la huella, después de
ver el resumen de la tarjeta. Antes de preparar se normalizan todas las fotos (vision_service): si una no se puede adjuntar,
no se escribe nada. El número de lote es de terminal (L900000 a L999999); si ya tiene facturas en Odoo, el lote local se
descarta y se toma otro, salvo que ese lote ya se haya usado en otra empresa o destino. Una factura con el marcador de un lote
de terminal que no creó esa corrida no se adopta. No hay alta de proveedor. Repetir con el mismo material retoma la misma
corrida; --lote-nuevo descarta el lote local y empieza otro. Al final, la presentación del lote (si la empresa la declara)
queda en <salida>/<lote>.eml, sin enviar.

Sale con 0 si la carga quedó completa y sin comprobantes afuera, 1 si no se cargó, quedó a medias o dejó alguno afuera, y 2
por uso.
"""
import argparse
import json
from email.message import EmailMessage
from pathlib import Path

EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp")       # las que normaliza vision_service; un PDF no se adjunta como foto


class Uso(Exception):
    pass


class _Argumentos(argparse.ArgumentParser):
    def error(self, message):
        raise Uso(message)


def _argumentos(argv):
    p = _Argumentos(prog="gastos cargar", description="Carga un lote de comprobantes desde la terminal.")
    p.add_argument("--empresa", required=True)
    p.add_argument("--destino", default="")
    p.add_argument("--carpeta", required=True, help="las fotos del lote, numeradas por orden de nombre")
    p.add_argument("--lecturas", required=True, help='JSON {"<archivo>": lectura} con la lectura de cada foto')
    p.add_argument("--propuesta", required=True,
                   help='JSON {"archivos": [{"nombre", "sha256"}], "comprobantes": [...], "excluir_fotos": [...]}')
    p.add_argument("--salida", default="", help="carpeta donde queda el .eml de la presentación (por defecto, --carpeta)")
    p.add_argument("--lote-nuevo", action="store_true", help="descartar el lote local de este material y empezar otro")
    return p.parse_args(argv)


def _json(ruta, que):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Uso(f"No pude leer {que} ({Path(ruta).name}): {type(exc).__name__}") from None


def archivos(carpeta):
    d = Path(carpeta).expanduser()
    if not d.is_dir():
        raise Uso(f"--carpeta {carpeta} no es una carpeta")
    todos = sorted((p for p in d.iterdir() if p.is_file() and not p.is_symlink()), key=lambda p: p.name)
    salida = [p for p in todos if p.suffix.lower() in EXTENSIONES]
    if not salida:
        raise Uso(f"--carpeta {carpeta} no tiene fotos ({', '.join(EXTENSIONES)})")
    if any(p.suffix.lower() == ".pdf" for p in todos):
        raise Uso("La carpeta tiene un PDF: el bot adjunta fotos. Pasalo a imagen (una por página) o sacalo de la carpeta.")
    return salida


def huellas(fotos):
    import hashlib
    return [{"nombre": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()} for f in fotos]


def lecturas_de(lecturas, fotos, fiscal, usadas=()):
    """{n: lectura normalizada} desde {"<archivo>": lectura}. Uso si una lectura no es un objeto ni null, si nombra otro
    archivo o si falta la de una foto que va en un comprobante (`usadas`)."""
    if not isinstance(lecturas, dict):
        raise Uso('Las lecturas son un objeto {"<archivo>": lectura}')
    nombres = [f.name for f in fotos]
    ajenas = sorted(set(lecturas) - set(nombres))
    if ajenas:
        raise Uso(f"Hay lecturas de archivos que no están en la carpeta: {', '.join(ajenas)}")
    salida = {}
    for n, nombre in enumerate(nombres, 1):
        if nombre not in lecturas:
            if n in usadas:
                raise Uso(f"Falta la lectura de {nombre} (foto {n}), que va en un comprobante; si no se pudo leer, va null")
            continue
        if lecturas[nombre] is None:
            continue
        if not isinstance(lecturas[nombre], dict):
            raise Uso(f"La lectura de {nombre} (foto {n}) no es un objeto ni null")
        salida[n] = fiscal.normalizar(lecturas[nombre])
    return salida


def _texto(r):
    return "\n".join(x.get("text", "") for x in (r or {}).get("content") or [] if isinstance(x, dict))


def eml(presentacion, carpeta, lote_id):
    """El borrador del mail de presentación, para abrir con el cliente de correo: sin remitente (lo pone el cliente) y
    marcado como no enviado. No pisa un archivo que ya está (otro intento, o uno que la persona editó)."""
    m = EmailMessage()
    m["To"] = ", ".join(presentacion["para"])
    if presentacion.get("cc"):
        m["Cc"] = ", ".join(presentacion["cc"])
    m["Subject"] = presentacion["asunto"]
    m["X-Unsent"] = "1"
    m.set_content(presentacion["cuerpo"])
    carpeta = Path(carpeta).expanduser()
    if carpeta.is_symlink():
        raise OSError("la carpeta de salida es un enlace")
    carpeta.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile
    fd, temporal = tempfile.mkstemp(prefix=f".{lote_id}.", suffix=".eml.tmp", dir=carpeta)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bytes(m))
            f.flush()
            os.fsync(f.fileno())
        for i in range(1, 100):
            ruta = carpeta / (f"{lote_id}.eml" if i == 1 else f"{lote_id}-{i}.eml")
            try:
                os.link(temporal, ruta)           # recién completo, y sin pisar uno que ya está
                return ruta
            except FileExistsError:
                continue
        raise OSError("ya hay demasiados borradores de este lote")
    finally:
        Path(temporal).unlink(missing_ok=True)


def _con_recibo(estado, lote_id):
    """El lote tiene algún recibo, en cualquier empresa o destino (una carga empezada)."""
    carpeta = estado.raiz(lote_id) / lote_id
    return carpeta.is_dir() and any(carpeta.glob("*/*.recibo.json"))


async def _numero_libre(g, ctx, c, lote_id):
    """El número de lote no tiene facturas en la compañía: el marcador de cada factura estándar es [<lote>/Fnn]."""
    filas = await g._odoo(ctx, "account.move", "search_read", [
        ("company_id", "=", c["company_id"]), ("invoice_origin", "ilike", f"[{lote_id}/")], fields=["id"], limit=1)
    return not filas


async def _fotos_adjuntables(fotos):
    """Cada foto se puede normalizar para adjuntarla, antes de escribir nada: el adjunto va después de crear la factura."""
    from bot import config, imagen
    for p in fotos:
        try:
            await imagen.normalizar(p.read_bytes(), max_px=config.ODOO_FOTO_MAX_PX, calidad=config.ODOO_FOTO_CALIDAD)
        except imagen.ImagenError as exc:
            raise Uso(f"{p.name}: {exc}") from None
        except Exception as exc:                        # noqa: BLE001 - vision_service no responde
            raise Uso(f"No pude normalizar {p.name}: la terminal necesita vision_service (VISION_SOCKET) corriendo en esta "
                      f"máquina ({type(exc).__name__})") from None


def _publico(g, exc):
    """El motivo de un rechazo, como lo vería la persona en el chat: sin rutas, nombres internos ni detalle técnico."""
    r = g._rechazo(exc)
    return r.get("publico") or _texto(r)


async def cargar(t, argv, g):
    """El comando `gastos cargar` de bot.terminal. Devuelve el código de salida (ver el docstring del módulo)."""
    from bot import config, lotes
    from . import configuracion, estado, fiscal, presentacion
    try:
        a = _argumentos(argv)
        if not getattr(t.ctx.user, "nombre_declarado", True):
            raise Uso("Falta --nombre: el nombre de la persona va en la propuesta y en la presentación, como en el chat")
        fotos = archivos(a.carpeta)
        propuesta = _json(a.propuesta, "la propuesta")
        if not isinstance(propuesta, dict) or not isinstance(propuesta.get("comprobantes"), list):
            raise Uso('La propuesta es {"archivos": [...], "comprobantes": [...], "excluir_fotos": [...]}')
        if propuesta.get("archivos") != huellas(fotos):
            raise Uso("«archivos» de la propuesta no es la lista de fotos de la carpeta, en orden y con su sha256: la "
                      f"carpeta cambió desde que se armó. Hoy es: {json.dumps(huellas(fotos))}")
        usadas = {n for x in propuesta["comprobantes"] if isinstance(x, dict)
                  for n in (x.get("fotos") or [x.get("n")]) if type(n) is int}
        leidas = lecturas_de(_json(a.lecturas, "las lecturas"), fotos, fiscal, usadas)
        cfg = configuracion.validar(config.config_funcion(g.FUNCION))
    except (Uso, configuracion.ConfigError) as exc:
        t.decir(f"error: {exc}")
        return 2
    empresa = cfg["empresas"].get(a.empresa)
    if empresa is None:
        t.decir(f"error: la empresa {a.empresa} no está en la configuración")
        return 2
    destino = a.destino or empresa.get("destino_por_defecto") or (next(iter(empresa["destinos"]))
                                                                  if len(empresa["destinos"]) == 1 else "")
    if destino not in empresa["destinos"]:
        t.decir(f"error: elegí el destino con --destino ({', '.join(empresa['destinos'])})")
        return 2
    if "rendicion" not in empresa or (empresa["_capacidades"].get(destino) or {}).get("factura") != "estandar":
        t.decir("error: desde la terminal se cargan facturas en un destino de factura estándar (circuito de facturas)")
        return 2
    try:
        await t.vincular_erp(destino, empresa["destinos"][destino])
    except Exception as exc:                            # noqa: BLE001 - lo que diga Odoo, sin credenciales (bot.odoo)
        g.log.warning("gastos: la terminal no entró a Odoo (%s): %s", type(exc).__name__, t.ctx.tapar(exc)[:300])
        motivo = "credenciales rechazadas" if "rechazadas" in str(exc) else "Odoo no respondió como se esperaba"
        t.decir(f"error: no pude entrar a Odoo con tus credenciales de {destino}: {motivo}")
        return 2
    try:
        await _fotos_adjuntables(fotos)
    except Uso as exc:
        t.decir(f"error: {exc}")
        return 2
    args = {"empresa": a.empresa, "destino": destino}
    store = lotes.store()
    for intento in range(3):
        lote = t.lote_local("gastos", fotos)
        if a.lote_nuevo and intento == 0:
            if estado.cargados([lote["lote_id"]]) or _con_recibo(estado, lote["lote_id"]):
                t.decir(f"error: el lote {lote['lote_id']} tiene una carga empezada o hecha: --lote-nuevo lo dejaría sin "
                        "poder retomarla. Para otro destino o empresa, copiá las fotos a otra carpeta.")
                return 1
            store.set_extra(lote["id"], {**(lote.get("extra") or {}), "descartado": True})
            lote = t.lote_local("gastos", fotos)
        args["lote_id"] = lote["lote_id"]
        try:
            with g._contexto(t.ctx, args) as c:
                recibo = estado.recibo(lote["lote_id"], c)
                usado = estado.leer(estado.indice_path(lote["lote_id"]))
                if recibo or await _numero_libre(g, t.ctx, c, lote["lote_id"]):
                    break
        except (g.GastoError, estado.EstadoError, configuracion.ConfigError) as exc:
            t.decir(f"error: {_publico(g, exc)}")
            return 1
        if usado:
            t.decir(f"error: el lote {lote['lote_id']} ya se usó en otra empresa o destino y en este su número tiene "
                    "facturas en Odoo: repetí con --lote-nuevo")
            return 1
        store.set_extra(lote["id"], {**(lote.get("extra") or {}), "descartado": True})
        t.decir(f"El número {lote['lote_id']} ya tiene facturas en Odoo: tomo otro.")
    else:
        t.decir("error: no encontré un número de lote libre; probá de nuevo")
        return 1
    lote_id = args["lote_id"]
    t.decir(f"Lote {lote_id}: " + ", ".join(f"foto {n} = {f.name}" for n, f in enumerate(fotos, 1)) + ".")
    if not recibo:
        fiscal.guardar_lecturas(lote_id, {str(n): {"sha256": i["sha256"], "lectura": leidas[n]}
                                          for n, i in enumerate((i for i in lote["items"] if i["tipo"] == "foto"), 1)
                                          if n in leidas})
        try:
            datos = await g.preparar(t.ctx, {**args, "comprobantes": propuesta["comprobantes"],
                                             "excluir_fotos": propuesta.get("excluir_fotos", [])})
        except (g.GastoError, OSError) as exc:
            t.decir(f"No se preparó: {_publico(g, exc)}")
            return 1
        if datos.get("estado") == "pendiente_correccion":
            t.decir("La imputación pide corregir o elegir:\n" + json.dumps(
                {k: datos[k] for k in ("conflictos", "opciones")}, ensure_ascii=False, indent=1))
            return 1
        t.decir("Propuesta:\n" + json.dumps(datos["propuesta"], ensure_ascii=False, indent=1))
    else:
        t.decir("Esta corrida ya empezó: retomo la propuesta original, sin duplicar.")
    r = await t.ejecutar("gasto_cargar", args)
    if r is None:
        t.decir("No se cargó nada.")
        return 1
    with g._contexto(t.ctx, args) as c:
        recibo = estado.recibo(lote_id, c)
        guardada = {}
        if c.get("presentacion"):
            try:
                guardada = (estado.leer(presentacion._ruta(lote_id, c)) or {}).get("presentacion") or {}
            except estado.EstadoError:
                guardada = {}
    # Lo que vio la persona en el chat, sin el ofrecimiento del mail: en la terminal queda como archivo.
    ofrecido = presentacion.pie(guardada) if guardada.get("estado") == "lista" else ""
    texto = r.get("publico") or _texto(r).split("\n\nPRESENTACIÓN DEL LOTE")[0]
    t.decir("\n".join(x for x in texto.split("\n") if not ofrecido or x.strip() != ofrecido))
    if r.get("is_error"):
        return 1
    if guardada.get("estado") == "lista":
        try:
            ruta = eml({**guardada, "asunto": t.ctx.tapar(guardada["asunto"]), "cuerpo": t.ctx.tapar(guardada["cuerpo"])},
                       a.salida or a.carpeta, lote_id)
            t.decir(f"El mail que presenta el lote quedó en {ruta}, sin enviar: abrilo con tu cliente de correo, revisalo y "
                    "mandalo vos.")
        except OSError as exc:
            t.decir(f"La carga quedó hecha, pero no pude escribir el mail de presentación ({exc}).")
    resultado = (recibo or {}).get("resultado") or {}
    completa = ((recibo or {}).get("estado") == "cargado" and not resultado.get("no_cargados")
                and not resultado.get("por_conciliar"))
    return 0 if completa else 1
