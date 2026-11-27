"""La carga de un lote desde una terminal (0.1.0-dev.21), con el motor 0.6.33 o posterior (bot.terminal).

    python -m bot.terminal --entorno ARCHIVO --datos CARPETA --persona LOGIN --nombre "Nombre" gastos cargar \\
        --empresa EMPRESA [--destino DESTINO] --carpeta FOTOS --lecturas LECTURAS.json --propuesta PROPUESTA.json \\
        [--salida CARPETA]

Es el mismo preparar → OK → cargar del chat, con la misma configuración, custodia, firma y recibos, sobre un lote local. Las
fotos o PDF de --carpeta se numeran por orden de nombre (foto 1, 2, ...). Lo que el chat hace con el modelo lo hace el agente
de código: las lecturas (un objeto {"<n>": lectura}, con el esquema de fiscal.ESQUEMA) y la propuesta (lo que recibe
gasto_preparar: {"comprobantes": [...], "excluir_fotos": [...]}). El código hace lo determinista.

- Solo en el circuito de facturas (la empresa declara `rendicion`). La identidad es la de la persona, con sus credenciales
  del destino (las variables de entorno con los nombres de secreto del destino, o se piden).
- El OK es teclear en la terminal los primeros 8 caracteres de la huella, después de ver el resumen de la tarjeta.
- El número de lote es de terminal (L900000 a L999999); si ya tiene facturas en Odoo, el lote local se descarta y se toma otro.
- No hay alta de proveedor: con ante_error=borrador la factura va con el proveedor a regularizar, marcada; sin eso, frena.
- Repetir el comando con el mismo material retoma la misma corrida, sin duplicar.
- Al final, la presentación del lote (si la empresa la declara) queda en <salida>/<lote>.eml, sin enviar.
"""
import argparse
import json
from email.message import EmailMessage
from pathlib import Path

EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp", ".pdf")


class Uso(Exception):
    pass


class _Argumentos(argparse.ArgumentParser):
    def error(self, message):
        raise Uso(message)


def _argumentos(argv):
    p = _Argumentos(prog="gastos cargar", description="Carga un lote de comprobantes desde la terminal.")
    p.add_argument("--empresa", required=True)
    p.add_argument("--destino", default="")
    p.add_argument("--carpeta", required=True, help="las fotos o PDF del lote, numeradas por orden de nombre")
    p.add_argument("--lecturas", required=True, help='JSON {"<n>": lectura} con la lectura de cada foto')
    p.add_argument("--propuesta", required=True, help='JSON {"comprobantes": [...], "excluir_fotos": [...]}')
    p.add_argument("--salida", default="", help="carpeta donde queda el .eml de la presentación (por defecto, --carpeta)")
    return p.parse_args(argv)


def _json(ruta, que):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Uso(f"No pude leer {que} ({ruta}): {exc}") from None


def archivos(carpeta):
    d = Path(carpeta).expanduser()
    if not d.is_dir():
        raise Uso(f"--carpeta {carpeta} no es una carpeta")
    salida = sorted((p for p in d.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONES),
                    key=lambda p: p.name)
    if not salida:
        raise Uso(f"--carpeta {carpeta} no tiene fotos ni PDF ({', '.join(EXTENSIONES)})")
    return salida


def _texto(r):
    return "\n".join(x.get("text", "") for x in (r or {}).get("content") or [] if isinstance(x, dict))


def eml(presentacion, ruta):
    """El borrador del mail de presentación, para abrir con el cliente de correo: sin remitente (lo pone el cliente) y
    marcado como no enviado."""
    m = EmailMessage()
    m["To"] = ", ".join(presentacion["para"])
    if presentacion.get("cc"):
        m["Cc"] = ", ".join(presentacion["cc"])
    m["Subject"] = presentacion["asunto"]
    m["X-Unsent"] = "1"
    m.set_content(presentacion["cuerpo"])
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(bytes(m))
    return ruta


async def _numero_libre(g, ctx, c, lote_id):
    """El número de lote no tiene facturas en la compañía: el marcador de cada factura es [<lote>/Fnn]."""
    filas = await g._odoo(ctx, "account.move", "search_read", [
        ("company_id", "=", c["company_id"]), ("invoice_origin", "ilike", f"[{lote_id}/")], fields=["id"], limit=1)
    return not filas


async def cargar(t, argv, g):
    """El comando `gastos cargar` de bot.terminal. Devuelve el código de salida: 0 cargado, 1 sin cargar o a medias, 2 uso."""
    from bot import config, lotes
    from . import configuracion, estado, fiscal, presentacion
    try:
        a = _argumentos(argv)
        fotos = archivos(a.carpeta)
        lecturas = _json(a.lecturas, "las lecturas")
        propuesta = _json(a.propuesta, "la propuesta")
        if not isinstance(lecturas, dict) or not isinstance(propuesta, dict) or not isinstance(
                propuesta.get("comprobantes"), list):
            raise Uso('Las lecturas son {"<n>": lectura} y la propuesta {"comprobantes": [...], "excluir_fotos": [...]}')
        cfg = configuracion.validar(config.config_funcion(g.FUNCION))
    except (Uso, configuracion.ConfigError) as exc:
        t.decir(f"error: {exc}")
        return 2
    empresa = cfg["empresas"].get(a.empresa)
    if empresa is None:
        t.decir(f"error: la empresa {a.empresa} no está en la configuración")
        return 2
    if "rendicion" not in empresa:
        t.decir("error: desde la terminal se cargan facturas: la empresa tiene que declarar el circuito de facturas (rendicion)")
        return 2
    destino = a.destino or empresa.get("destino_por_defecto") or (next(iter(empresa["destinos"]))
                                                                  if len(empresa["destinos"]) == 1 else "")
    if destino not in empresa["destinos"]:
        t.decir(f"error: elegí el destino con --destino ({', '.join(empresa['destinos'])})")
        return 2
    try:
        await t.vincular_erp(destino, empresa["destinos"][destino])
    except Exception as exc:                            # noqa: BLE001 - lo que diga Odoo, sin credenciales (bot.odoo)
        t.decir(f"error: no pude entrar a Odoo con tus credenciales de {destino}: {exc}")
        return 2
    args = {"empresa": a.empresa, "destino": destino}
    for _ in range(3):
        lote = t.lote_local("gastos", fotos)
        args["lote_id"] = lote["lote_id"]
        with g._contexto(t.ctx, args) as c:
            recibo = estado.recibo(lote["lote_id"], c)
            if recibo or await _numero_libre(g, t.ctx, c, lote["lote_id"]):
                break
        lotes.store().set_extra(lote["id"], {**(lote.get("extra") or {}), "descartado": True})
        t.decir(f"El número {lote['lote_id']} ya tiene facturas en Odoo: tomo otro.")
    else:
        t.decir("error: no encontré un número de lote libre; probá de nuevo")
        return 1
    lote_id = args["lote_id"]
    t.decir(f"Lote {lote_id}: {len(fotos)} archivo{'s' if len(fotos) != 1 else ''} de {a.carpeta}.")
    if not recibo:
        guardadas = {}
        for item_n, item in enumerate((i for i in lote["items"] if i["tipo"] == "foto"), 1):
            lec = lecturas.get(str(item_n))
            if isinstance(lec, dict):
                guardadas[str(item_n)] = {"sha256": item["sha256"], "lectura": fiscal.normalizar(lec)}
        fiscal.guardar_lecturas(lote_id, guardadas)
        r = await t.ejecutar("gasto_preparar", {**args, "comprobantes": propuesta["comprobantes"],
                                                "excluir_fotos": propuesta.get("excluir_fotos", [])})
        if r.get("is_error"):
            t.decir(f"No se preparó: {_texto(r)}")
            return 1
        datos = json.loads(_texto(r))
        if datos.get("estado") == "pendiente_correccion":
            t.decir("La imputación pide corregir o elegir:\n" + json.dumps(datos, ensure_ascii=False, indent=1))
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
        ruta = eml(guardada, Path(a.salida or a.carpeta).expanduser() / f"{lote_id}.eml")
        t.decir(f"El mail que presenta el lote quedó en {ruta}, sin enviar: abrilo con tu cliente de correo, revisalo y "
                "mandalo vos.")
    return 0 if (recibo or {}).get("estado") == "cargado" else 1
