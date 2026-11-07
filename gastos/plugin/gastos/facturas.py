"""Factura canónica sin RRHH; rendición posterior con aprobación y recibo propios."""
import asyncio
from decimal import Decimal
import hashlib
import uuid
from . import configuracion, estado, custodia, estandar

AVISO_CONTROL = "Hoja de control: no se aprueba ni se contabiliza. La factura ya llevó el coste."


def capacidades(c):
    return {"factura": "addon", "rendicion": "vinculada",
            "control_sin_contabilizar_confirmado": False, **c.get("capacidades", {})}


def aviso_rendicion(c):
    return AVISO_CONTROL if capacidades(c)["rendicion"] == "revision_hr" else (
        "La decisión de rendición no altera ni cancela las facturas registradas.")


async def cuenta(ctx, c, categoria, g):
    ident = c["cuentas"].get(categoria)
    if not configuracion.positivo(ident):
        raise g.GastoError("Configurar la cuenta contable por categoría y destino")
    filas = await g._odoo(ctx, "account.account", "read", [ident],
        fields=["company_ids", "deprecated", "account_type"])
    if (len(filas) != 1 or filas[0]["id"] != ident or filas[0].get("deprecated")
            or c["company_id"] not in filas[0].get("company_ids", [])
            or filas[0].get("account_type") not in ("expense", "expense_direct_cost", "expense_depreciation")):
        raise g.GastoError("La cuenta contable no es una cuenta de gasto vigente de la compañía")
    return ident


async def contrato(ctx, g):
    campos = await g._odoo(ctx, "account.move", "fields_get", attributes=["type"])
    necesarios = {"receipt_bill_contract", "receipt_bill_lot", "receipt_bill_photo", "receipt_bill_payment",
        "receipt_bill_payload_hash", "receipt_bill_source_uid", "receipt_bill_original_total", "receipt_bill_original_analytic"}
    if not necesarios <= set(campos):
        raise g.GastoError("Instalar el addon receipt_bill del nuevo circuito antes de registrar facturas")


def payload(plan, x):
    return configuracion.firma({"company":plan["company_id"],"uid":plan["odoo_uid"],"linea":x})


def campos_factura():
    return ["state", "move_type", "company_id", "currency_id", "journal_id", "invoice_date", "amount_total",
        "receipt_bill_contract", "receipt_bill_lot", "receipt_bill_photo", "receipt_bill_payment",
        "receipt_bill_payload_hash", "receipt_bill_source_uid", "receipt_bill_original_total", "receipt_bill_original_analytic"]


def comprobar_factura(b, plan, x, g):
    if (b.get("receipt_bill_contract") != 2 or b.get("receipt_bill_lot") != plan["lote_id"]
            or b.get("receipt_bill_photo") != x["n"] or b.get("receipt_bill_payment") != x["pago"]
            or b.get("receipt_bill_payload_hash") != payload(plan,x)
            or g._rel(b.get("receipt_bill_source_uid")) != plan["odoo_uid"]
            or g._rel(b.get("company_id")) != plan["company_id"]
            or g._rel(b.get("currency_id")) != x["currency_id"]
            or g._rel(b.get("journal_id")) != plan["journal_id"]
            or b.get("move_type") != "in_invoice" or b.get("state") not in ("draft","posted")
            or b.get("invoice_date") != x["fecha"]
            or Decimal(str(b.get("receipt_bill_original_total", 0))) != Decimal(x["importe"])
            or not g._misma_distribucion(b.get("receipt_bill_original_analytic"), x["imputacion"]["distribucion"])):
        raise g.GastoError("La factura canónica no coincide con el comprobante; revisar sin duplicarla")


async def cargar(ctx, lote, c, plan, g):
    if capacidades(c)["factura"] == "estandar":
        return await estandar.cargar(ctx,lote,c,plan,g)
    facturas = {}
    for x in plan["comprobantes"]:
        g._vigencia(ctx,c)
        filas = await g._odoo(ctx,"account.move","search_read",[
            ("company_id","=",c["company_id"]),("receipt_bill_lot","=",plan["lote_id"]),
            ("receipt_bill_photo","=",x["n"])],fields=campos_factura(),limit=2)
        if len(filas) > 1:
            raise g.GastoError("Hay más de una factura para el mismo comprobante")
        nueva = not filas
        if nueva:
            ident = await g._odoo(ctx,"account.move","create",{
                "move_type":"in_invoice","company_id":c["company_id"],"journal_id":plan["journal_id"],
                "currency_id":x["currency_id"],"invoice_date":x["fecha"],"partner_id":False,"ref":False,
                "receipt_bill_lot":plan["lote_id"],"receipt_bill_photo":x["n"],
                "receipt_bill_payment":x["pago"],"receipt_bill_payload_hash":payload(plan,x),
                "invoice_line_ids":[(0,0,{"name":f"{x['comercio']} {x['marker']}",
                    "account_id":x["account_id"],"quantity":1,"price_unit":float(Decimal(x["importe"])),
                    "tax_ids":[(6,0,[])],"analytic_distribution":x["imputacion"]["distribucion"]})]})
        else:
            ident = filas[0]["id"]
        ver = await g._odoo(ctx,"account.move","read",[ident],fields=campos_factura()+["invoice_line_ids","partner_id","ref"])
        if len(ver) != 1:
            raise g.GastoError("No se pudo verificar la factura creada")
        comprobar_factura(ver[0],plan,x,g)
        if nueva:
            lineas = await g._odoo(ctx,"account.move.line","read",ver[0]["invoice_line_ids"],
                fields=["account_id","analytic_distribution","quantity","price_unit","tax_ids"])
            if (len(lineas) != 1 or g._rel(lineas[0].get("account_id")) != x["account_id"]
                    or not g._misma_distribucion(lineas[0].get("analytic_distribution"), x["imputacion"]["distribucion"])
                    or Decimal(str(lineas[0].get("quantity",0))) != 1
                    or Decimal(str(lineas[0].get("price_unit",0))) != Decimal(x["importe"])
                    or lineas[0].get("tax_ids") or ver[0].get("partner_id") or ver[0].get("ref")
                    or Decimal(str(ver[0]["amount_total"])) != Decimal(x["importe"])):
                raise g.GastoError("Un default alteró cuenta, analítica o datos de la factura; revisar sin duplicar")
        estado.efecto(plan["lote_id"],c,"account.move",ident)
        for respaldo in g._respaldos(x):
            aid = await g._foto_adjuntar(ctx,lote,x,ident,respaldo,modelo="account.move")
            estado.efecto(plan["lote_id"],c,"ir.attachment",aid)
        facturas[str(x["n"])] = ident
    return {"estado":"borrador","facturas":facturas,
        "rendicion":"disponible como acción separada" if c["rendicion"] else "deshabilitada",
        "aviso":"Facturas con analítica y fotos registradas. No aprueba conducta ni reembolso. Revisión fiscal independiente."}


def ruta(lote_id,c,tipo="propuesta"):
    p = estado.recibo_path(lote_id,c)
    return estado.seguro(p.with_name(p.name.replace(".recibo.json", f".rendicion.{tipo}.json")))


def huella(lote_id,c):
    return hashlib.sha256(ruta(lote_id,c).read_bytes()).hexdigest()


async def base_rendicion(ctx,args,g):
    c = g._CORRIDA.get()
    if c.get("rendicion") is not True:
        raise g.GastoError("La rendición está deshabilitada para esta empresa")
    caps = capacidades(c)
    if caps["rendicion"] == "ninguna":
        raise g.GastoError("La capacidad de rendición está deshabilitada")
    if caps["rendicion"] == "revision_hr" and caps["control_sin_contabilizar_confirmado"] is not True:
        raise g.GastoError("Confirmar administrativamente el control sin contabilizar antes de crear RRHH. " + AVISO_CONTROL)
    await g._puerta(ctx)
    c["_rindiendo"] = True
    c["employee_id"] = configuracion.empleado(ctx.user,c["empresa"],c["destino"],c["plana"])
    uid, empleado = await g._identidad(ctx,c)
    if not configuracion.positivo(empleado["id"]):
        raise g.GastoError("Vincular el empleado nominal para la acción de rendición")
    lote_id = args["lote_id"]
    plan = g._leer(lote_id)
    if plan["login"] != ctx.user.login or plan.get("circuito") != "facturas/1":
        raise g.GastoError("La propuesta fiscal debe pertenecer a tu identidad y al circuito de facturas")
    if plan["odoo_uid"] != uid:
        raise g.GastoError("Cambió la identidad nominal desde el registro de la factura")
    g._validar_seleccion(plan,c)
    r = estado.recibo(lote_id,c)
    if not r or r.get("estado") != "cargado" or "facturas" not in r.get("resultado",{}):
        raise g.GastoError("Primero registrar las facturas; su recibo es independiente de la rendición")
    for x in plan["comprobantes"]:
        ident = r["resultado"]["facturas"][str(x["n"])]
        if caps["factura"] == "estandar":
            if "_estandar_campos" not in c:
                await estandar.contrato(ctx,g)
            campos_bill = estandar.campos_factura(c)
        else:
            campos_bill = campos_factura()
        filas = await g._odoo(ctx,"account.move","read",[ident],fields=campos_bill)
        if len(filas) != 1:
            raise g.GastoError("Falta una factura registrada")
        if caps["factura"] == "estandar":
            await estandar.comprobar(ctx,filas[0],plan,x,g,fase="rendicion")
        else:
            comprobar_factura(filas[0],plan,x,g)
        if filas[0]["state"] != "posted" or Decimal(str(filas[0]["amount_total"])) != Decimal(x["importe"]):
            raise g.GastoError("Contabilidad debe revisar y contabilizar la factura antes de enlazarla en RRHH; no se bloquea su registro")
    campos = await g._odoo(ctx,"hr.expense","fields_get",attributes=["type","readonly"])
    c["_campos_hr"] = campos
    if caps["rendicion"] == "vinculada" and not {"receipt_bill_source_id","receipt_bill_contract","invoice_id"} <= set(campos):
        raise g.GastoError("Instalar receipt_bill_expense y su dependencia OCA para habilitar la rendición")
    if caps["rendicion"] == "revision_hr":
        await _contrato_control(ctx,c,g)
        _fotos_rendicion(plan,g)
        for x in plan["comprobantes"]:
            if await g._resolver_categoria(ctx,c,x["categoria"]) != x["product_id"]:
                raise g.GastoError("Cambió la categoría de RRHH; revisar la propuesta sin alterar las facturas")
    return c,plan,r,uid,empleado


# El contrato del control de RRHH (rendición revision_hr). El adjunto es el mismo del ingreso estándar (estandar.ADJUNTO).
CONTRATO_CONTROL={
    "hr.expense":{"name":"char","state":"selection","company_id":"many2one","employee_id":"many2one",
        "currency_id":"many2one","product_id":"many2one","date":"date","quantity":"float",
        "payment_mode":"selection","total_amount_currency":"monetary","analytic_distribution":"json","tax_ids":"many2many"},
    "hr.expense.sheet":{"name":"char","state":"selection","company_id":"many2one","employee_id":"many2one",
        "employee_journal_id":"many2one","expense_line_ids":"one2many"},
    "ir.attachment":estandar.ADJUNTO}


async def _contrato_control(ctx,c,g):
    for modelo,campos in CONTRATO_CONTROL.items():
        for operacion in ("read","create"):
            if await g._odoo(ctx,modelo,"check_access_rights",operacion,raise_exception=False) is not True:
                raise g.GastoError("Falta permiso nominal para crear o verificar el control de RRHH")
        metadata=await g._odoo(ctx,modelo,"fields_get",attributes=["type","readonly"])
        if faltas := estandar.diferencias(modelo,metadata,campos):
            raise estandar.error_contrato(g,faltas)
        if modelo=="hr.expense":
            c["_campos_hr"]=metadata


async def operar_rendicion(ctx,args,g,*,cargar=False,aprobacion=None):
    with g._contexto(ctx,args) as c:
        lote_id = args.get("lote_id","")
        estado.raiz(lote_id)
        locks = g._LOCKS.setdefault(asyncio.get_running_loop(),{})
        async with locks.setdefault(lote_id,asyncio.Lock()), custodia.exclusion(lote_id,c):
            c,plan,r,uid,empleado = await base_rendicion(ctx,args,g)
            if cargar:
                propuesta = estado.leer(ruta(lote_id,c))
                if not propuesta or not aprobacion or aprobacion != huella(lote_id,c):
                    raise g.GastoError("La rendición requiere su propio OK L2; el OK fiscal no la autoriza")
                if propuesta["firma"] != firma_rendicion(c,empleado,r) or propuesta["login"] != ctx.user.login:
                    raise g.GastoError("Cambió la configuración o identidad de la rendición")
                anterior = propuesta["politica"]
                actual = await g._evaluar_politica(ctx,c,uid,plan["comprobantes"])
                if actual != anterior or actual["resultado"]["bloqueos"]:
                    raise g.GastoError("La política impide esta rendición; las facturas permanecen registradas")
                proveedor = g._politica(c)
                if proveedor and await g._del_plugin("revalidar política",proveedor.revalidar(ctx,g._marco(ctx,c,uid),g._copia(plan["comprobantes"]),g._copia(actual["resultado"]))):
                    raise g.GastoError("Cambió la política de rendición; las facturas permanecen registradas")
                return await rendir(ctx,c,plan,r,empleado,propuesta,g)
            if estado.leer(ruta(lote_id,c,"recibo")):
                raise g.GastoError("Retomar la rendición iniciada, sin reemplazar su recibo")
            p={"revision":uuid.uuid4().hex,"login":ctx.user.login,"firma":firma_rendicion(c,empleado,r),
                "facturas":r["resultado"]["facturas"],"employee_id":empleado["id"],
                "modo":capacidades(c)["rendicion"],"advertencia":aviso_rendicion(c),
                "politica":await g._evaluar_politica(ctx,c,uid,plan["comprobantes"])}
            estado.guardar(ruta(lote_id,c),p)
            return {"estado":"bloqueada" if p["politica"]["resultado"]["bloqueos"] else "sin rendir",
                "facturas":p["facturas"],"politica":p["politica"]["resultado"],"huella":huella(lote_id,c),
                "aviso":aviso_rendicion(c)}


def firma_rendicion(c,empleado,r):
    return configuracion.firma({"fiscal":c["firma"],"empleado":empleado["id"],"politica":c.get("politica",""),
        "rendicion":c["rendicion"],"capacidades_hr":{
            "modo":capacidades(c)["rendicion"],
            "control_sin_contabilizar_confirmado":capacidades(c)["control_sin_contabilizar_confirmado"]},
        "facturas":r["resultado"]["facturas"]})


def _fotos_rendicion(plan,g):
    from bot import lotes
    try:
        lote = lotes.lote_ver(plan["lote_id"])
        fotos = g._fotos(lote,[[f["n"] for f in g._respaldos(x)] for x in plan["comprobantes"]],
            [f["n"] for f in plan.get("fotos_excluidas",[])])
        if any(fotos[f["n"]-1]["sha256"] != f["sha256"]
               for x in plan["comprobantes"] for f in g._respaldos(x)):
            raise ValueError("huella")
        return lote
    except (g.GastoError,ValueError,KeyError,IndexError,TypeError,OSError) as exc:
        raise g.GastoError("Faltan fotos originales seguras para RRHH o fueron purgadas; las facturas permanecen registradas") from exc


async def rendir(ctx,c,plan,r,empleado,p,g):
    lote_id = plan["lote_id"]
    control = capacidades(c)["rendicion"] == "revision_hr"
    lote = _fotos_rendicion(plan,g) if control else None
    firmado = huella(lote_id,c)
    anterior = estado.leer(ruta(lote_id,c,"recibo"))
    if anterior and anterior["huella"] != firmado:
        raise g.GastoError("Hay efectos de otra rendición; retomarla sin reemplazarla")
    recibo = anterior or {"huella":firmado,"estado":"parcial","gastos":[],"informes":{}}
    recibo.setdefault("correlaciones",{})
    recibo.setdefault("intenciones",{})
    estado.guardar(ruta(lote_id,c,"recibo"),recibo)
    c["_aplicando"] = True
    async def vigente():
        actual=g._seleccionar(ctx,{"empresa":c["empresa"],"destino":c["destino"]})
        actual["employee_id"]=configuracion.empleado(ctx.user,c["empresa"],c["destino"],c["plana"])
        if (actual.get("rendicion") is not True or firma_rendicion(actual, {"id":actual["employee_id"]},r)!=p["firma"]
                or huella(lote_id,c)!=firmado):
            raise g.GastoError("La rendición cambió durante la carga")
        actual_politica=await g._evaluar_politica(ctx,c,plan["odoo_uid"],plan["comprobantes"])
        if actual_politica!=p["politica"] or actual_politica["resultado"]["bloqueos"]:
            raise g.GastoError("Cambió la política durante la rendición; las facturas permanecen registradas")
    por_grupo={}
    async def crear_seguro(clave,modelo,vals,filas):
        intento=recibo["intenciones"].get(clave)
        if filas:
            ident=filas[0]["id"]
            if not intento:
                raise g.GastoError("Existe RRHH sin intención propia de esta rendición; revisar sin adoptarlo ni duplicarlo")
            if intento and intento.get("id") not in (None,ident):
                raise g.GastoError("El resultado de RRHH no coincide con la intención registrada")
            return ident
        if intento:
            raise g.GastoError("Resultado de RRHH incierto o desaparecido; revisar manualmente sin repetir la creación")
        await vigente()
        recibo["intenciones"][clave]={"estado":"pendiente","modelo":modelo}
        estado.guardar(ruta(lote_id,c,"recibo"),recibo)
        try:
            ident=await g._odoo(ctx,modelo,"create",vals)
        except BaseException:
            recibo["intenciones"][clave]["estado"]="resultado_incierto"
            estado.guardar(ruta(lote_id,c,"recibo"),recibo)
            raise
        recibo["intenciones"][clave].update(estado="id_conocido",id=ident)
        estado.guardar(ruta(lote_id,c,"recibo"),recibo)
        return ident
    for x in plan["comprobantes"]:
        await vigente()
        bid=r["resultado"]["facturas"][str(x["n"])]; imp=x["imputacion"]
        campos=["name","invoice_id","receipt_bill_source_id","receipt_bill_contract","company_id","employee_id",
            "currency_id","date","payment_mode","total_amount_currency","analytic_distribution","product_id","state","tax_ids"]
        nombre_gasto=f"{x['comercio']} {x['marker']}"
        if control:
            campos.append("quantity")
            campos=[field for field in campos if field not in ("invoice_id","receipt_bill_source_id","receipt_bill_contract")]
            campos.append("quantity")
            dominio=[("name","=",nombre_gasto),("company_id","=",c["company_id"])]
        else:
            dominio=[("receipt_bill_source_id","=",bid)]
        filas=await g._odoo(ctx,"hr.expense","search_read",dominio,fields=campos,limit=2)
        if len(filas)>1:
            raise g.GastoError("Hay rendiciones duplicadas para la misma factura")
        valores={
                "name":nombre_gasto,"receipt_bill_source_id":bid,"invoice_id":bid,
                "company_id":c["company_id"],"employee_id":empleado["id"],"date":x["fecha"],
                "currency_id":x["currency_id"],"product_id":x["product_id"],"quantity":1,
                "total_amount_currency":float(Decimal(x["importe"])),"payment_mode":x["pago"],
                "tax_ids":[(6,0,[])],"analytic_distribution":imp["distribucion"]}
        descripcion=c.get("_campos_hr",{}).get("description")
        texto=" ".join(t for t in (AVISO_CONTROL if control else "",
                                   f"Pago: {x['pago_detalle']}." if x.get("pago_detalle") else "") if t)
        if texto and isinstance(descripcion,dict) and not descripcion.get("readonly"):
            valores["description"]=texto
        if control:
            valores.pop("receipt_bill_source_id"); valores.pop("invoice_id")
            eid=await crear_seguro(f"gasto:{x['n']}","hr.expense",valores,filas)
        elif not filas:
            await vigente()
            eid=await g._odoo(ctx,"hr.expense","create",valores)
        else: eid=filas[0]["id"]
        filas=await g._odoo(ctx,"hr.expense","read",[eid],fields=campos)
        if (len(filas)!=1 or (not control and (filas[0].get("receipt_bill_contract")!=2
                or g._rel(filas[0].get("receipt_bill_source_id"))!=bid or g._rel(filas[0].get("invoice_id"))!=bid))
                or filas[0].get("name")!=nombre_gasto
                or g._rel(filas[0].get("employee_id"))!=empleado["id"]
                or g._rel(filas[0].get("company_id"))!=c["company_id"]
                or g._rel(filas[0].get("currency_id"))!=x["currency_id"]
                or g._rel(filas[0].get("product_id"))!=x["product_id"] or filas[0].get("date")!=x["fecha"]
                or filas[0].get("payment_mode")!=x["pago"] or filas[0].get("state") not in ("draft","reported")
                or (control and Decimal(str(filas[0].get("quantity",0)))!=1)
                or (control and Decimal(str(filas[0].get("quantity",0)))!=1)
                or Decimal(str(filas[0].get("total_amount_currency",0)))!=Decimal(x["importe"])
                or not g._misma_distribucion(filas[0].get("analytic_distribution"),imp["distribucion"]) or filas[0].get("tax_ids")):
            raise g.GastoError("El gasto de rendición no coincide con su factura canónica")
        if control:
            clave=str(x["n"])
            correlacion={"factura_id":bid,"gasto_id":eid}
            if clave in recibo["correlaciones"] and recibo["correlaciones"][clave]!=correlacion:
                raise g.GastoError("Cambió la correlación entre factura y gasto de control")
            recibo["correlaciones"][clave]=correlacion
            recibo["intenciones"][f"gasto:{x['n']}"].update(estado="id_conocido",id=eid)
            estado.guardar(ruta(lote_id,c,"recibo"),recibo)
            for respaldo in g._respaldos(x):
                await vigente()
                n=respaldo["n"]
                nombre_adjunto=(f"Comprobante {x['marker']}.jpg" if n==x["n"] else
                    f"Respaldo {x['marker']} foto {n:02d}.jpg")
                adjuntos=await g._odoo(ctx,"ir.attachment","search_read",[
                    ("res_model","=","hr.expense"),("res_id","=",eid),("name","=",nombre_adjunto)],
                    fields=["name","checksum"],limit=2)
                if len(adjuntos)>1:
                    raise g.GastoError("Hay adjuntos duplicados en el gasto de control")
                clave_adjunto=f"foto:{x['n']}:{n}"
                intento=recibo["intenciones"].get(clave_adjunto)
                if intento and not adjuntos:
                    raise g.GastoError("Resultado del adjunto de RRHH incierto; revisar sin repetir su creación")
                if not adjuntos:
                    recibo["intenciones"][clave_adjunto]={"estado":"pendiente","modelo":"ir.attachment"}
                    estado.guardar(ruta(lote_id,c,"recibo"),recibo)
                try:
                    aid=await g._foto_adjuntar(ctx,lote,x,eid,respaldo)
                except BaseException:
                    recibo["intenciones"].setdefault(clave_adjunto,{})["estado"]="resultado_incierto"
                    estado.guardar(ruta(lote_id,c,"recibo"),recibo)
                    raise
                if intento and intento.get("id") not in (None,aid):
                    raise g.GastoError("Cambió el ID del adjunto de control")
                recibo["intenciones"][clave_adjunto]={"estado":"id_conocido","modelo":"ir.attachment","id":aid}
                estado.guardar(ruta(lote_id,c,"recibo"),recibo)
        por_grupo.setdefault(g._clave_hoja(x),[]).append(eid)
        if eid not in recibo["gastos"]: recibo["gastos"].append(eid)
        estado.guardar(ruta(lote_id,c,"recibo"),recibo)
    for grupo,eids in por_grupo.items():
        await vigente()
        x=next(x for x in plan["comprobantes"] if g._clave_hoja(x)==grupo)
        rotulo=x["imputacion"].get("rotulo","")
        fecha=min(x["fecha"] for x in plan["comprobantes"]).replace("-","")
        nombre=f"Rendición {ctx.user.name} {fecha}{' '+rotulo if rotulo else ''} [{lote_id}] · {g._PAGO_NOMBRE[x['pago']]}"
        campos=["state","company_id","employee_id","employee_journal_id","expense_line_ids"]
        hojas=await g._odoo(ctx,"hr.expense.sheet","search_read",[("name","=",nombre),("company_id","=",c["company_id"])],fields=campos,limit=2)
        if len(hojas)>1: raise g.GastoError("Hay hojas duplicadas")
        valores_hoja={"name":nombre,"company_id":c["company_id"],
                "employee_id":empleado["id"],"employee_journal_id":plan["journal_id"],"expense_line_ids":[(4,i,0) for i in eids]}
        if control:
            sid=await crear_seguro(f"hoja:{grupo}","hr.expense.sheet",valores_hoja,hojas)
        elif not hojas:
            sid=await g._odoo(ctx,"hr.expense.sheet","create",valores_hoja)
        else: sid=hojas[0]["id"]
        hojas=await g._odoo(ctx,"hr.expense.sheet","read",[sid],fields=campos)
        if (len(hojas)!=1 or hojas[0]["state"]!="draft" or set(hojas[0]["expense_line_ids"])!=set(eids)
                or g._rel(hojas[0]["company_id"])!=c["company_id"] or g._rel(hojas[0]["employee_id"])!=empleado["id"]
                or g._rel(hojas[0]["employee_journal_id"])!=plan["journal_id"]):
            raise g.GastoError("La hoja existente no coincide con esta rendición")
        recibo["informes"][grupo]=sid
        if control:
            recibo["intenciones"][f"hoja:{grupo}"].update(estado="id_conocido",id=sid)
        estado.guardar(ruta(lote_id,c,"recibo"),recibo)
    recibo["estado"]="cargado"
    estado.guardar(ruta(lote_id,c,"recibo"),recibo)
    return {"estado":"borrador","informes":recibo["informes"],"gastos":recibo["gastos"],
        "texto":g.texto_carga({"informes":recibo["informes"]},{},c),
        "aviso":AVISO_CONTROL if control else "Rendición independiente preparada. No aprueba, reembolsa, cancela ni altera las facturas."}
