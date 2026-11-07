"""Dos empresas y dos ERPs sintéticos; ninguna llamada real ni búsqueda por nombre."""
import asyncio
import base64
import copy
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from tests import test_gastos_plugin as banco
from bot import config, lotes, plugins, odoo

gastos = banco.gastos
cfgmod, estado = gastos.configuracion, gastos.estado


def destino(nombre, company_id):
    return {"url": f"https://{nombre}.example.test", "link_url": f"https://{nombre}.example.test",
            "db": "casa_prueba", "secreto_login": nombre.upper() + "_LOGIN",
            "secreto_api_key": nombre.upper() + "_KEY", "company_id": company_id}


def configuracion():
    return {"retencion_dias": 90, "empresas": {
        nombre: {"company_id": company, "zona_horaria": "Etc/UTC",
                 "diario": {"ids": {"ensayo": 100 + diario, "principal": diario}},
                 "compania": "Casa de Prueba " + nombre, "imputador": "", "politica": "",
                 "destinos": {"ensayo": destino("ensayo", company + 10),
                              "principal": destino("principal", company)},
                 "categorias": {"Traslados": {"ids": {"ensayo": 401, "principal": 501}},
                                "Materiales": {"default_code": "GASTO_MAT"}}}
        for nombre, company, diario in (("taller", 3, 61), ("oficina", 7, 71))}}


def cumple(fila, dominio):
    def valor(campo):
        v = fila.get(campo)
        return v[0] if isinstance(v, (list, tuple)) and v else v
    tokens = iter(dominio)
    def uno(token):
        if token in ("|", "&"):
            a, b = uno(next(tokens)), uno(next(tokens))
            return a or b if token == "|" else a and b
        campo, op, esperado = token
        if op == "!=":
            return valor(campo) != esperado
        if op == "in":
            return valor(campo) in esperado
        if op == "ilike":
            return str(esperado).lower() in str(valor(campo) or "").lower()
        assert op == "=", op
        return valor(campo) == esperado
    resultados = [uno(t) for t in tokens]
    return all(resultados)


class EmpresasTests(banco._BancoOdoo):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for i in (0, 1, 2, 3): self.patches[i].stop()
        self.raiz = configuracion()
        self.escribir()
        self.user.odoo_uid = 999
        self.user.puede = lambda servicio: servicio == "odoo"
        self.user.odoo_ids_extra = {
            "usuarios_erp": {"ensayo": 17, "principal": 27},
            "empleados_gastos": {"taller": 81, "oficina": 91},
            "empleados_gastos_destinos": {"taller": {"ensayo": 181}, "oficina": {"ensayo": 191}}}
        self.secretos = {"ENSAYO_LOGIN": "persona_prueba", "ENSAYO_KEY": "falso_ensayo",
                         "PRINCIPAL_LOGIN": "persona_prueba", "PRINCIPAL_KEY": "falso_principal"}
        self.ctx.secreto = self.secretos.get
        self.lote["creado"] = time.time() - 40 * 86400
        self.lotes = {self.lote["lote_id"]: self.lote}
        self.rows[0]["categoria"] = self.rows[1]["categoria"] = "Traslados"
        self.erps = {}
        for alias, off, uid in (("ensayo", 100, 17), ("principal", 0, 27)):
            base_id = 401 if alias == "ensayo" else 501
            self.erps[alias] = {
                "uid": uid, "off": off,
                "product.product": {
                    base_id: {"id": base_id, "name": "Duplicado", "company_id": False,
                              "active": True, "can_be_expensed": True, "default_code": False},
                    base_id + 1: {"id": base_id + 1, "name": "Duplicado", "company_id": False,
                                  "active": True, "can_be_expensed": True, "default_code": "GASTO_MAT"}},
                "hr.employee": {
                    ident + off: {"id": ident + off, "name": "Persona de Prueba", "active": True,
                                  "company_id": [company + (10 if off else 0), "Casa"], "user_id": [uid, "Persona"]}
                    for ident, company in ((81, 3), (91, 7))},
                "account.journal": {
                    ident + off: {"id": ident + off, "name": "Compras", "company_id": [company + (10 if off else 0), "Casa"], "type": "purchase",
                                  "active": True}
                    for ident, company in ((61, 3), (71, 7))},
                "hr.expense": {}, "hr.expense.sheet": {}, "ir.attachment": {}}
        self.rpc_llamadas = []
        self.fallar_adjunto = False
        for p in (patch("bot.lotes.lote_ver", side_effect=lambda ident: self.lotes[ident]),
                  patch.object(odoo, "llamar", side_effect=self.rpc)):
            p.start(); self.addCleanup(p.stop)

    def escribir(self):
        (Path(config.FUNCIONES_CONFIG_DIR) / "gastos.json").write_text(json.dumps(self.raiz), encoding="utf-8")

    def args(self, destino="ensayo", empresa="taller", lote_id="L000123"):
        return {"lote_id": lote_id, "empresa": empresa, "destino": destino}

    async def preparar(self, **opciones):
        return await gastos.preparar(self.ctx, {**self.args(**opciones), "comprobantes": self.rows})

    def crear_lote(self, ident):
        lote = copy.deepcopy(self.lote); lote["lote_id"] = ident
        for item in lote["items"]:
            original = Path(item["ruta"])
            nuevo = original.parent.parent / ident / original.name
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            # Cada lote trae sus propias fotos: la misma foto en dos lotes no se vuelve a cargar.
            nuevo.write_bytes(original.read_bytes() + ident.encode()); item["ruta"] = str(nuevo)
            item["sha256"] = hashlib.sha256(nuevo.read_bytes()).hexdigest()
        self.lotes[ident] = lote
        return lote

    def seleccion(self, destino="ensayo", empresa="taller"):
        return cfgmod.seleccionar(self.raiz, self.user, self.args(destino, empresa))

    async def aprobar(self, destino="ensayo", empresa="taller", lote_id="L000123", huella=None):
        args = self.args(destino, empresa, lote_id)
        firma = huella if huella is not None else await gastos._huella_cargar(self.ctx, args)
        self.ctx.consumir_aprobacion = lambda *_: firma
        return await gastos._tool_cargar(self.ctx, args)

    def sin_escrituras(self):
        self.assertFalse(any(metodo == "create" for _, _, metodo, *_ in self.rpc_llamadas))

    async def rpc(self, ctx, modelo, metodo, args, kw, *, destino=None):
        await asyncio.sleep(0)
        assert destino is not None, "No se permite usar el ERP global"
        alias = "ensayo" if destino["url"] == "https://ensayo.example.test" else "principal"
        self.assertEqual(destino["secreto_api_key"], alias.upper() + "_KEY")
        self.rpc_llamadas.append((alias, modelo, metodo, copy.deepcopy(args)))
        erp = self.erps[alias]
        if modelo == "res.users":
            assert metodo == "context_get"
            return {"uid": erp["uid"]}
        if modelo == "res.currency": return [{"id": 71 + erp["off"], "name": "XTS", "active": True}]
        tabla = erp[modelo]
        if metodo == "search_read":
            if modelo == "product.product":
                self.assertFalse(any(isinstance(x, (tuple, list)) and x[0] == "name" for x in args[0]))
            return copy.deepcopy([fila for fila in tabla.values() if cumple(fila, args[0])][:kw.get("limit", 999)])
        if metodo == "read": return copy.deepcopy([tabla[i] for i in args[0] if i in tabla])
        assert metodo == "create", (modelo, metodo)
        valores = copy.deepcopy(args[0])
        ident = 10000 + 1000 * erp["off"] + sum(len(erp[m]) for m in ("hr.expense", "hr.expense.sheet", "ir.attachment"))
        if modelo == "hr.expense":
            fila = {**valores, "id": ident, "state": "draft", "quantity": 1, "tax_ids": [],
                    "price_unit": valores["total_amount_currency"],
                    "analytic_distribution": valores.get("analytic_distribution", False)}
            for k in ("company_id", "employee_id", "currency_id", "product_id"): fila[k] = [valores[k], "Prueba"]
        elif modelo == "ir.attachment":
            if self.fallar_adjunto:
                self.fallar_adjunto = False
                raise odoo.OdooError("Fallo sintético de adjunto")
            fila = {"id": ident, "name": valores["name"], "res_model": valores["res_model"],
                    "res_id": valores["res_id"], "checksum": hashlib.sha1(base64.b64decode(valores["datas"])).hexdigest()}
        else:
            fila = {**valores, "id": ident, "state": "draft",
                    "expense_line_ids": [x[1] for x in valores["expense_line_ids"]]}
            for k in ("company_id", "employee_id", "employee_journal_id"): fila[k] = [valores[k], "Prueba"]
            for i in fila["expense_line_ids"]: erp["hr.expense"][i]["state"] = "reported"
        tabla[ident] = fila
        return ident

    async def test_mismo_lote_ensayo_y_principal_con_ok_y_recibos_distintos(self):
        ensayo = await self.preparar()
        r = await self.aprobar(); self.assertNotIn("is_error", r)
        c_ensayo = self.seleccion()
        ruta = estado.recibo_path("L000123", c_ensayo); recibo = ruta.read_bytes()
        principal = await self.preparar(destino="principal")
        self.assertNotEqual(ensayo["huella"], principal["huella"])
        rechazo = await self.aprobar(destino="principal", huella=ensayo["huella"])
        self.assertTrue(rechazo["is_error"])
        self.assertEqual(self.erps["principal"]["hr.expense"], {})
        self.assertEqual(ruta.read_bytes(), recibo)
        self.assertFalse((await self.aprobar(destino="principal")).get("is_error"))
        self.assertEqual(ruta.read_bytes(), recibo)
        for alias, empleado, producto in (("ensayo", 181, 401), ("principal", 81, 501)):
            filas = list(self.erps[alias]["hr.expense"].values())
            self.assertEqual(len(filas), 2)
            self.assertTrue(all(x["employee_id"][0] == empleado and x["product_id"][0] == producto for x in filas))
        self.assertTrue(all(Path(i["ruta"]).is_file() for i in self.lote["items"]))
        self.assertTrue(estado.propuesta("L000123", c_ensayo).exists())

    async def test_elecciones_explicitas_e_inferencia_solo_empresa_accesible(self):
        for args in ({"lote_id": "L000123"}, {"lote_id": "L000123", "empresa": "taller"}):
            with self.assertRaises(gastos.GastoError):
                await gastos.preparar(self.ctx, {**args, "comprobantes": self.rows})
        self.user.odoo_ids_extra["empleados_gastos"].pop("oficina")
        self.user.odoo_ids_extra["empleados_gastos_destinos"].pop("oficina")
        r = await gastos.preparar(self.ctx, {"lote_id": "L000123", "destino": "ensayo", "comprobantes": self.rows})
        self.assertEqual(r["empresa"], "taller")
        self.sin_escrituras()

    async def test_catalogo_solo_nombres_configurados_y_seleccion(self):
        opciones = json.loads((await gastos._tool_catalogo(self.ctx, {}))["content"][0]["text"])
        self.assertEqual(set(opciones["empresas"]), {"taller", "oficina"})
        r = await gastos._tool_catalogo(self.ctx, self.args())
        salida = json.loads(r["content"][0]["text"])
        self.assertEqual(salida["categorias"], ["Materiales", "Traslados"])
        self.assertNotIn("Duplicado", str(salida)); self.assertNotIn("default_code", str(salida))
        self.sin_escrituras()

    async def test_catalogo_vacio_incompleto_o_nombre_ajeno_no_busca_por_nombre(self):
        originales = copy.deepcopy(self.raiz)
        for categorias, mensaje in (({}, "vacío"), ({"Comidas": {}}, "incompleto"),
                                    ({"Materiales": {"default_code": "GASTO_MAT"}}, "no está configurada")):
            self.raiz = copy.deepcopy(originales)
            self.raiz["empresas"]["taller"]["categorias"] = categorias; self.escribir()
            with self.assertRaisesRegex(gastos.GastoError, mensaje): await self.preparar()
        self.sin_escrituras()

    async def test_producto_compartido_y_default_code_admitidos(self):
        self.rows[0]["categoria"] = "Materiales"
        await self.preparar()
        self.assertNotIn("is_error", await self.aprobar())
        self.assertEqual({x["product_id"][0] for x in self.erps["ensayo"]["hr.expense"].values()}, {401, 402})

    async def test_default_code_ambiguo_falla_cerrado(self):
        self.rows[0]["categoria"] = "Materiales"
        otra = copy.deepcopy(self.erps["ensayo"]["product.product"][402]); otra.update(id=403, company_id=[13, "Casa"])
        self.erps["ensayo"]["product.product"][403] = otra
        with self.assertRaisesRegex(gastos.GastoError, "ambigua"): await self.preparar()
        self.sin_escrituras()

    async def test_id_prevalece_y_no_fallback_si_no_existe(self):
        ref = self.raiz["empresas"]["taller"]["categorias"]["Traslados"]
        ref["default_code"] = "GASTO_MAT"; self.escribir()
        await self.preparar()
        ref["ids"]["ensayo"] = 999; self.escribir()
        with self.assertRaisesRegex(gastos.GastoError, "referencia"): await self.preparar()
        self.sin_escrituras()

    async def test_producto_de_otra_compania_inactivo_o_no_rendible_rechazado(self):
        producto = self.erps["ensayo"]["product.product"][401]
        original = copy.deepcopy(producto)
        for cambios in ({"company_id": [999, "Otra"]}, {"active": False}, {"can_be_expensed": False}):
            producto.clear(); producto.update(original); producto.update(cambios)
            with self.assertRaises(gastos.GastoError): await self.preparar()
        self.sin_escrituras()

    async def test_empresa_del_lote_no_se_mezcla(self):
        await self.preparar()
        with self.assertRaisesRegex(gastos.GastoError, "una sola"): await self.preparar(empresa="oficina")
        self.sin_escrituras()

    async def test_uid_y_empleado_son_nominales_del_destino(self):
        await self.preparar()  # odoo_uid global 999 nunca se usa
        self.user.odoo_ids_extra["usuarios_erp"]["ensayo"] = 999
        with self.assertRaisesRegex(gastos.GastoError, "credencial"): await self.preparar()
        self.user.odoo_ids_extra["usuarios_erp"]["ensayo"] = 17
        self.erps["ensayo"]["hr.employee"][181]["company_id"] = [3, "Otra"]
        with self.assertRaisesRegex(gastos.GastoError, "activo"): await self.preparar()
        self.sin_escrituras()

    async def test_no_hereda_credenciales_ni_empleado_del_destino_global(self):
        self.secretos.pop("ENSAYO_KEY")
        with self.assertRaisesRegex(gastos.GastoError, "credenciales"): await self.preparar()
        self.secretos["ENSAYO_KEY"] = "falso_ensayo"
        self.user.odoo_ids_extra = {"empleado_gastos": 81}
        with self.assertRaises(gastos.GastoError): await self.preparar()
        self.sin_escrituras()

    async def test_cambio_de_configuracion_invalida_el_ok(self):
        p = await self.preparar()
        self.raiz["empresas"]["taller"]["diario"]["ids"]["ensayo"] = 999; self.escribir()
        self.assertTrue((await self.aprobar(huella=p["huella"]))["is_error"])
        self.sin_escrituras()

    async def test_cambio_en_otro_destino_no_invalida_ensayo(self):
        await self.preparar()
        self.raiz["empresas"]["taller"]["categorias"]["Traslados"]["ids"]["principal"] = 999; self.escribir()
        self.assertNotIn("is_error", await self.aprobar())

    async def test_categoria_se_revalida_antes_de_crear(self):
        await self.preparar()
        self.erps["ensayo"]["product.product"][401]["active"] = False
        self.assertTrue((await self.aprobar())["is_error"]); self.sin_escrituras()

    async def test_nueva_propuesta_mismo_destino_exige_nuevo_ok(self):
        vieja = await self.preparar(); nueva = await self.preparar()
        self.assertNotEqual(vieja["huella"], nueva["huella"])
        self.assertTrue((await self.aprobar(huella=vieja["huella"]))["is_error"]); self.sin_escrituras()

    async def test_parcial_retoma_sin_duplicar_y_no_reemplaza_propuesta(self):
        await self.preparar(); self.fallar_adjunto = True
        self.assertTrue((await self.aprobar())["is_error"])
        self.assertEqual(len(self.erps["ensayo"]["hr.expense"]), 1)
        with self.assertRaisesRegex(gastos.GastoError, "ya se inició"): await self.preparar()
        recibo = estado.recibo_path("L000123", self.seleccion()).read_bytes()
        await self.preparar(destino="principal")
        self.assertEqual(estado.recibo_path("L000123", self.seleccion()).read_bytes(), recibo)
        gastos._LOCKS.clear()  # no hay caché de recibos que deba sobrevivir
        self.assertNotIn("is_error", await self.aprobar())
        self.assertNotIn("is_error", await self.aprobar())
        self.assertEqual(len(self.erps["ensayo"]["hr.expense"]), 2)
        self.assertEqual(len(self.erps["ensayo"]["ir.attachment"]), 2)

    async def test_retencion_hasta_completar_destinos_o_vencer_plazo(self):
        await self.preparar(); await self.aprobar()
        ahora = time.time()
        self.assertTrue(gastos._retener(self.lote, ahora))
        self.assertFalse(gastos._retener(self.lote, ahora + 100 * 86400))
        await self.preparar(destino="principal"); await self.aprobar(destino="principal")
        self.assertFalse(gastos._retener(self.lote, ahora))

    async def test_purga_no_borra_fotos_con_destino_pendiente(self):
        await self.preparar(); await self.aprobar()
        almacen = SimpleNamespace(lotes_cerrados_antes_de=lambda _: [self.lote], borrar=Mock())
        with patch.object(lotes, "store", return_value=almacen):
            await lotes._purgar(time.time())
        almacen.borrar.assert_not_called()
        self.assertTrue(all(Path(i["ruta"]).exists() for i in self.lote["items"]))

    async def test_corridas_concurrentes_no_mezclan_contexto(self):
        self.crear_lote("L000124")
        await asyncio.gather(self.preparar(), self.preparar(destino="principal", empresa="oficina", lote_id="L000124"))
        await asyncio.gather(gastos.cargar(self.ctx, self.args()),
                             gastos.cargar(self.ctx, self.args("principal", "oficina", "L000124")))
        self.assertEqual({x["company_id"][0] for x in self.erps["ensayo"]["hr.expense"].values()}, {13})
        self.assertEqual({x["company_id"][0] for x in self.erps["principal"]["hr.expense"].values()}, {7})

    async def test_configuracion_rechaza_alias_y_secretos_compartidos(self):
        for mutacion in ("alias", "secreto", "sin_par"):
            c = configuracion()
            if mutacion == "alias": c["empresas"]["taller"]["destinos"]["duplicado"] = destino("ensayo", 13)
            elif mutacion == "secreto": c["empresas"]["taller"]["destinos"]["principal"]["secreto_api_key"] = "ENSAYO_KEY"
            else: del c["empresas"]["taller"]["destinos"]["principal"]["secreto_login"]
            with self.assertRaises(cfgmod.ConfigError): cfgmod.validar(c)

    def politica(self, *, bloquear=False):
        self.observacion = "Revisar el límite de la Casa de Prueba"
        self.bloquear = bloquear
        self.marcos = []
        async def evaluar(ctx, marco, lineas):
            self.marcos.append((marco.empresa, marco.destino, marco.uid))
            return {"observaciones": [self.observacion], "bloqueos": ["Supera el límite"] if self.bloquear else [], "estado": {"version": 1}}
        async def revalidar(ctx, marco, lineas, resultado): return None
        proveedor = SimpleNamespace(evaluar=evaluar, revalidar=revalidar)
        plugins.registrar(plugins.Plugin(nombre="politica_prueba", grupo="politica_prueba", extensiones=(
            plugins.Extension("gastos.politica", 1, "limites", proveedor),)))
        self.addCleanup(plugins.quitar, "politica_prueba")
        self.raiz["empresas"]["taller"]["politica"] = "limites"; self.escribir()
        return proveedor

    async def test_politica_observa_se_muestra_y_recibe_empresa_destino(self):
        self.politica(); p = await self.preparar()
        self.assertIn(self.observacion, p["politica"]["observaciones"])
        _, cuerpo = await gastos._resumen_cargar(self.ctx, self.args(), False)
        self.assertIn(self.observacion, cuerpo)
        self.assertIn("Destino: ensayo", cuerpo)
        self.assertNotIn("is_error", await self.aprobar())
        self.assertTrue(all(m == ("taller", "ensayo", 17) for m in self.marcos))

    async def test_politica_bloquea_o_cambia_y_no_crea(self):
        self.politica(bloquear=True); p = await self.preparar()
        self.assertEqual(p["estado"], "bloqueado")
        self.assertTrue((await self.aprobar())["is_error"])
        with self.assertRaises(gastos.GastoError): await gastos._resumen_cargar(self.ctx, self.args(), False)
        self.bloquear = False
        await self.preparar(); self.observacion = "Cambió el límite"
        self.assertTrue((await self.aprobar())["is_error"]); self.sin_escrituras()

    async def test_politica_invalida_o_excepcion_falla_cerrado(self):
        proveedor = self.politica()
        async def invalida(*args): return {"observaciones": [], "bloqueos": [], "estado": {"v": float("nan")}}
        proveedor.evaluar = invalida
        with self.assertRaises(gastos.GastoError): await self.preparar()
        async def falla(*args): raise RuntimeError("Fallo sintético")
        proveedor.evaluar = falla
        with self.assertRaises(gastos.GastoError): await self.preparar()
        self.sin_escrituras()

    async def test_imputador_distinto_por_empresa_y_marco_del_destino(self):
        marcos = []
        for empresa, cuenta in (("taller", 41), ("oficina", 42)):
            async def proponer(ctx, marco, lineas, cuenta=cuenta):
                marcos.append((marco.empresa, marco.destino, marco.company_id, marco.uid))
                return {"lineas": {x["n"]: {"distribucion": {cuenta: 100}, "grupo": "cuenta",
                    "rotulo": "Cuenta de Prueba", "detalle": "", "texto": "Cuenta de Prueba", "estado": {}}
                    for x in lineas}}
            async def revalidar(*args): return None
            nombre = "imputar_" + empresa
            plugins.registrar(plugins.Plugin(nombre=nombre, grupo=nombre, extensiones=(plugins.Extension(
                "gastos.imputador", 1, nombre, SimpleNamespace(proponer=proponer, revalidar=revalidar, nota="Cuenta configurada")),)))
            self.addCleanup(plugins.quitar, nombre)
            self.raiz["empresas"][empresa]["imputador"] = nombre
        self.escribir(); self.crear_lote("L000124")
        await self.preparar(); await self.preparar(empresa="oficina", destino="principal", lote_id="L000124")
        await self.aprobar(); await self.aprobar(empresa="oficina", destino="principal", lote_id="L000124")
        self.assertEqual(marcos, [("taller", "ensayo", 13, 17), ("oficina", "principal", 7, 27)])
        self.assertEqual({str(x["analytic_distribution"]) for x in self.erps["ensayo"]["hr.expense"].values()}, {"{'41': 100}"})
        self.assertEqual({str(x["analytic_distribution"]) for x in self.erps["principal"]["hr.expense"].values()}, {"{'42': 100}"})

    async def test_politica_se_aplica_solo_a_su_empresa(self):
        self.politica(bloquear=True); self.crear_lote("L000124")
        a = await self.preparar()
        b = await self.preparar(empresa="oficina", destino="principal", lote_id="L000124")
        self.assertEqual(a["estado"], "bloqueado"); self.assertEqual(b["politica"]["bloqueos"], [])
        self.assertNotIn("is_error", await self.aprobar(empresa="oficina", destino="principal", lote_id="L000124"))
        self.assertEqual(self.marcos, [("taller", "ensayo", 17)])

    async def test_nuevo_destino_y_destino_retirado_siguen_reteniendo(self):
        await self.preparar(); await self.aprobar()
        c = self.raiz["empresas"]["taller"]
        c["destinos"].pop("principal"); c["categorias"]["Traslados"]["ids"].pop("principal")
        c["diario"]["ids"].pop("principal"); self.escribir()
        self.assertTrue(gastos._retener(self.lote, time.time()))
        self.raiz = configuracion(); self.escribir()
        await self.preparar(destino="principal"); await self.aprobar(destino="principal")
        self.assertFalse(gastos._retener(self.lote, time.time()))
        c = self.raiz["empresas"]["taller"]
        c["destinos"]["archivo"] = destino("archivo", 3)
        c["categorias"]["Traslados"]["ids"]["archivo"] = 901; c["diario"]["ids"]["archivo"] = 61; self.escribir()
        self.assertTrue(gastos._retener(self.lote, time.time()))

    async def test_sesion_privado_y_turno_propio(self):
        for campo, valor in (("nivel1", lambda: False), ("private_channel", False), ("programado", True)):
            previo = getattr(self.ctx, campo, None); setattr(self.ctx, campo, valor)
            with self.assertRaises(gastos.GastoError): await self.preparar()
            setattr(self.ctx, campo, previo)
        self.sin_escrituras()

    async def test_tamper_de_propuesta_durante_lecturas_frena_antes_de_crear(self):
        p = await self.preparar()
        ruta = estado.propuesta("L000123", self.seleccion())
        original = self.rpc
        async def cambiar(ctx, modelo, metodo, args, kw, *, destino=None):
            r = await original(ctx, modelo, metodo, args, kw, destino=destino)
            if modelo == "product.product":
                datos = json.loads(ruta.read_text()); datos["revision"] = "otra"
                estado.guardar(ruta, datos)
            return r
        with patch.object(odoo, "llamar", side_effect=cambiar):
            self.assertTrue((await self.aprobar(huella=p["huella"]))["is_error"])
        self.sin_escrituras()

    async def test_cancelacion_libera_corrida_y_no_pierde_las_fotos(self):
        inicio = asyncio.Event()
        async def esperar(*args, **kw): inicio.set(); await asyncio.Event().wait()
        with patch.object(odoo, "llamar", side_effect=esperar):
            tarea = asyncio.create_task(self.preparar())
            await asyncio.wait_for(inicio.wait(), 2)
            tarea.cancel()
            with self.assertRaises(asyncio.CancelledError): await tarea
        await asyncio.wait_for(self.preparar(), 2)
        self.assertIsNone(gastos._CORRIDA.get())
        self.assertTrue(all(Path(i["ruta"]).exists() for i in self.lote["items"]))

    async def test_lote_vencido_no_inicia_otra_corrida(self):
        self.lote["creado"] = time.time() - 100 * 86400
        with self.assertRaisesRegex(gastos.GastoError, "plazo"): await self.preparar()
        self.sin_escrituras()

    async def test_recibo_no_admite_enlaces_fuera_de_custodia(self):
        ruta = estado.recibo_path("L000123", self.seleccion())
        ruta.parent.mkdir(parents=True, exist_ok=True)
        objetivo = config.DATA_DIR / "externo.json"; objetivo.write_text('{"prueba": 1}')
        ruta.symlink_to(objetivo)
        with self.assertRaises(gastos.GastoError): await self.preparar()
        self.assertEqual(objetivo.read_text(), '{"prueba": 1}')
        self.sin_escrituras()

    async def test_diario_por_id_no_depende_del_idioma(self):
        # El nombre del diario se traduce con el idioma de Odoo: se lee por ID y nunca se busca por nombre.
        for fila in self.erps["ensayo"]["account.journal"].values(): fila["name"] = "Vendor Bills"
        await self.preparar()
        self.assertNotIn("is_error", await self.aprobar())
        diarios = [x for x in self.rpc_llamadas if x[1] == "account.journal"]
        self.assertTrue(diarios)
        self.assertTrue(all(metodo == "read" and args == [[161]] for _, _, metodo, args in diarios), diarios)

    async def test_diario_texto_se_rechaza_como_configuracion(self):
        for lugar in ("empresa", "destino"):
            with self.subTest(lugar=lugar):
                self.raiz = configuracion()
                if lugar == "empresa":
                    self.raiz["empresas"]["taller"]["diario"] = "Compras"
                else:
                    self.raiz["empresas"]["taller"]["destinos"]["ensayo"]["diario"] = "Compras"
                self.escribir()
                with self.assertRaisesRegex(gastos.GastoError, r"Configuración del bot \(gastos\.json\).*diario.*"
                                                               r"no es un problema de Odoo"):
                    await self.preparar()
        self.sin_escrituras()

    async def test_diario_ajeno_o_no_compras_falla_antes_de_escribir(self):
        original = copy.deepcopy(self.erps["ensayo"]["account.journal"][161])
        for campo, valor in (("type", "sale"), ("company_id", [99, "Otra"]), ("active", False)):
            with self.subTest(campo=campo):
                self.erps["ensayo"]["account.journal"][161] = {**original, campo: valor}
                with self.assertRaisesRegex(gastos.GastoError, r"diario 161.*gastos\.json"):
                    await self.preparar()
        del self.erps["ensayo"]["account.journal"][161]
        with self.assertRaisesRegex(gastos.GastoError, r"diario 161.*no existe en Odoo"):
            await self.preparar()
        self.sin_escrituras()


class CompatibilidadTests(banco._BancoOdoo):
    async def test_propuesta_anterior_sin_seleccion_conserva_bytes_y_ok(self):
        args = {"lote_id": "L000123", "comprobantes": self.rows}
        await gastos.preparar(self.ctx, args)
        ruta = gastos._plan_path("L000123")
        p = json.loads(ruta.read_text())
        for campo in ("version", "revision", "seleccion", "politica"): p.pop(campo, None)
        estado.guardar(ruta, p); estado.indice_path("L000123").unlink()
        anterior = ruta.read_bytes()
        firma = hashlib.sha256(anterior).hexdigest()
        self.ctx.consumir_aprobacion = lambda *_: firma
        self.assertNotIn("is_error", await gastos._tool_cargar(self.ctx, {"lote_id": "L000123"}))
        self.assertEqual(ruta.read_bytes(), anterior)
