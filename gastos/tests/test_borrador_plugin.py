"""ante_error=borrador (0.1.0-dev.19): un comprobante con un error de datos entra igual en borrador, marcado para
Administración, y el error de uno no frena a los demás. Lo que impide escribir sí corta, y un reintento no duplica.

Todo inventado: CUIT de prueba con dígito verificador inválido, IDs y comercios ficticios. El toolbelt de alta es falso: con
--conservar-pendiente y un rechazo deja la provisoria con el prefijo y devuelve su id, como el de la casa.
"""
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import patch
from tests import test_fiscal_plugin as base
from tests import test_empresas_plugin as empresas
from tests import test_seguimiento_plugin as seguimiento
from bot import odoo, plugins

g = base.gastos
estado = base.estado
proveedores = seguimiento.proveedores
OTRO = "30888888880"            # dígito verificador inválido: no puede ser de nadie
GENERICO, RESPONSABLE, PROVISORIA = 9900, 6, 9200


class BorradorTests(base.FiscalTests):
    con_alta = seguimiento.SeguimientoTests.con_alta
    sin_facturas = seguimiento.SeguimientoTests.sin_facturas

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.actividades = {}
        self.caer = None
        self.corridas = []

    def dos_comprobantes(self):
        """El segundo comprobante, de un proveedor que ya está en Odoo y valida."""
        self.rows[1].update(importe="121.00")
        self.guardar(base.lectura(), base.lectura(cuit=OTRO, numero="0012-00034568"))

    def borrador(self, alta=True, **extra):
        self.dos_comprobantes()
        c = self.raiz["empresas"]["taller"]
        if alta:
            self.con_alta()
        else:
            c["padron_proveedores"] = "AR"
        c.update(ante_error="borrador", proveedor_a_regularizar={"ids": {"ensayo": 9500, "principal": GENERICO}},
                 responsable_revision={"ids": {"ensayo": 5, "principal": RESPONSABLE}}, **extra)
        self.escribir()
        self.erps["principal"]["res.partner"] = {}
        self.socio(GENERICO, "Proveedor a regularizar", vat=False)
        self.socio(9002, "Proveedor Dos", vat=OTRO)

    def toolbelt(self, rc=0, salida=None, crea=True, pendiente=False, efecto=None, **extra):
        async def correr(ctx, repo, rel, argv, aplicar, al_correr=None):
            self.corridas.append((repo, rel, list(argv), aplicar))
            if efecto:
                efecto()
            res = {"empresa_id": None, "empresa": None, "creados": [], "avisos": [], "error": None, **extra}
            if aplicar and rc == 0 and crea:
                self.socio(9100, "Razón del Padrón")
                res.update(empresa_id=9100, empresa="Razón del Padrón",
                           creados=[{"modelo": "res.partner", "id": 9100, "tipo": "empresa"}])
            if aplicar and rc == 1 and pendiente and "--conservar-pendiente" in argv:
                if PROVISORIA not in self.erps["principal"]["res.partner"]:
                    self.socio(PROVISORIA, proveedores.PENDIENTE + "Café Norte")
                res["pendiente_id"] = PROVISORIA
            texto = salida if salida is not None else "línea legible\nJSON " + json.dumps(res, ensure_ascii=False)
            al_correr(rc, texto)
            return {"content": [{"type": "text", "text": texto}]}
        return patch("bot.broker.ejecutar_toolbelt", side_effect=correr)

    async def rpc(self, ctx, modelo, metodo, args, kw, *, destino=None):
        if destino["url"] == base.PRINCIPAL:
            if self.caer and self.caer(modelo, metodo, args):
                raise odoo.OdooError("Odoo no responde (sintético)")
            if modelo == "ir.model" and metodo == "search_read":
                return [{"id": 85, "model": "account.move"}] if empresas.cumple({"model": "account.move"}, args[0]) else []
            if modelo == "mail.activity" and metodo in ("search_read", "create"):
                self.rpc_llamadas.append(("principal", modelo, metodo, copy.deepcopy(args)))
                if metodo == "search_read":
                    return copy.deepcopy([a for a in self.actividades.values() if empresas.cumple(a, args[0])])
                v = copy.deepcopy(args[0])
                self.assertEqual(v["res_model_id"], 85)
                ident = 70000 + len(self.actividades)
                self.actividades[ident] = {**v, "id": ident, "res_model": "account.move"}
                return ident
        return await super().rpc(ctx, modelo, metodo, args, kw, destino=destino)

    async def cargar_lote(self):
        with self.toolbelt():
            p = await self.preparar(destino="principal")
        return p

    def factura_de(self, n):
        marcador = f"[L000123/F{n:02d}]"
        return next(f for f in self.facturas() if f.get("invoice_origin") == marcador)

    def recibo(self):
        return estado.recibo("L000123", self.seleccion("principal"))

    # ------------------------------------------------------------------------------------------- el caso del pedido
    async def test_el_padron_rechaza_y_se_cargan_todas_en_borrador(self):
        self.borrador()
        p = await self.cargar_lote()
        self.assertIn("proveedor nuevo", p["propuesta"]["comprobantes"][0]["factura"]["proveedor"])
        _, cuerpo = await g._resumen_cargar(self.ctx, self.args("principal"), False)
        self.assertIn("la factura entra igual en borrador", cuerpo)
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="ARCA registra requerimientos pendientes"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertIn("--conservar-pendiente", self.corridas[-1][2])
        self.assertIs(self.corridas[-1][3], True)
        una, dos = self.factura_de(1), self.factura_de(2)
        self.assertEqual((una["partner_id"][0], dos["partner_id"][0]), (PROVISORIA, 9002))
        self.assertEqual(una["state"], "draft")
        self.assertIn("Para revisar", una["narration"])
        self.assertIn("ARCA registra requerimientos pendientes", una["narration"])
        self.assertNotIn("Para revisar", dos["narration"])
        (actividad,) = self.actividades.values()
        self.assertEqual((actividad["res_id"], actividad["user_id"]), (una["id"], RESPONSABLE))
        self.assertIn("alta fiscal pendiente", actividad["note"])
        texto = r["content"][0]["text"]
        self.assertIn("Cargué 2 en borrador; 1 quedó marcado para que los revise Administración:", texto)
        self.assertIn("⚠️ Foto 1", texto)
        self.assertIn(f"id={una['id']}&model=account.move", texto)
        self.assertEqual(r.get("publico"), texto)
        recibo = self.recibo()
        self.assertEqual(recibo["estado"], "cargado")
        self.assertEqual(set(recibo["resultado"]["revision"]), {"1"})
        # Ningún contacto con el mismo número: la provisoria es una sola.
        self.assertEqual([i for i, s in self.erps["principal"]["res.partner"].items() if s["vat"] == base.CUIT],
                         [PROVISORIA])

    async def test_repetir_el_ok_no_duplica_facturas_contactos_ni_actividades(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            await self.aprobar(destino="principal")
        antes = (copy.deepcopy(self.erps["principal"]["account.move"]), dict(self.actividades),
                 dict(self.erps["principal"]["res.partner"]), len(self.corridas))
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(set(self.erps["principal"]["account.move"]), set(antes[0]))
        self.assertEqual(self.actividades, antes[1])
        self.assertEqual(self.erps["principal"]["res.partner"], antes[2])
        self.assertEqual(len(self.corridas), antes[3], "el alta no se repite")

    async def test_el_alta_valida_no_marca_nada(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], 9100)
        self.assertEqual(self.actividades, {})
        self.assertIn("Proveedor nuevo: Razón del Padrón", r["content"][0]["text"])
        self.assertNotIn("revision", self.recibo()["resultado"])

    async def test_un_aviso_del_padron_carga_con_el_proveedor_y_lo_marca(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt(avisos=["Lo leído no se parece al padrón."]):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(self.factura_de(1)["partner_id"][0], 9100)
        self.assertIn("Lo leído no se parece al padrón.", self.factura_de(1)["narration"])
        self.assertEqual(len(self.actividades), 1)

    async def test_rechazo_sin_provisoria_va_con_el_proveedor_a_regularizar(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, error="no validó"):
            await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)
        self.assertIn("proveedor a regularizar", self.factura_de(1)["narration"])

    # ------------------------------------------------------------------------------------------- alta incierta
    async def test_timeout_no_repite_el_alta_y_usa_lo_que_quedo(self):
        self.borrador()
        await self.cargar_lote()
        dejo = lambda: self.socio(PROVISORIA, proveedores.PENDIENTE + "Café Norte")
        with self.toolbelt(rc=1, salida="timeout", efecto=dejo):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(self.factura_de(1)["partner_id"][0], PROVISORIA)
        self.assertIn("no terminó a tiempo", self.factura_de(1)["narration"])
        aplicadas = sum(1 for *_, aplicar in self.corridas if aplicar)
        with self.toolbelt():
            await self.aprobar(destino="principal")
        self.assertEqual(sum(1 for *_, aplicar in self.corridas if aplicar), aplicadas)
        self.assertEqual([i for i, s in self.erps["principal"]["res.partner"].items() if s["vat"] == base.CUIT],
                         [PROVISORIA])

    async def test_timeout_sin_nada_en_odoo_va_con_el_generico(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt(rc=1, salida="timeout"):
            await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)

    async def test_relectura_distinta_no_repite_y_marca(self):
        self.borrador()
        await self.cargar_lote()
        dejo = lambda: self.socio(9100, "Razón del Padrón")
        with self.toolbelt(rc=3, crea=False, efecto=dejo):
            await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], 9100)
        self.assertIn("relectura no coincide", self.factura_de(1)["narration"])

    async def test_corte_con_el_alta_en_curso_no_la_repite(self):
        self.borrador()
        await self.cargar_lote()

        async def cortado(*a, **k):
            self.corridas.append(a)
            raise RuntimeError("se cortó el proceso")
        with patch("bot.broker.ejecutar_toolbelt", side_effect=cortado):
            with self.assertRaises(RuntimeError):
                await self.aprobar(destino="principal")
        self.sin_facturas()
        corridas = len(self.corridas)
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(len(self.corridas), corridas)
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)
        self.assertIn("quedó a medias", self.factura_de(1)["narration"])

    # ------------------------------------------------------------------------------------------- al preparar
    async def test_sin_cuit_leido_va_con_el_generico_y_marca(self):
        self.borrador()
        self.guardar(base.lectura(cuit=""), base.lectura(cuit=OTRO, numero="0012-00034568"))
        p = await self.cargar_lote()
        self.assertEqual(self.corridas, [])
        factura = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertEqual(factura["proveedor"], "Proveedor a regularizar (proveedor a regularizar)")
        self.assertIn("No se leyó el CUIT/RUT", factura["revision"][0])
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)
        self.assertEqual(len(self.actividades), 1)

    async def test_sin_alta_en_el_destino_usa_la_provisoria_existente(self):
        self.borrador(alta=False)
        self.socio(PROVISORIA, proveedores.PENDIENTE + "Café Norte")
        p = await self.preparar(destino="principal")
        factura = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertIn("alta fiscal pendiente", factura["proveedor"])
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(self.factura_de(1)["partner_id"][0], PROVISORIA)

    async def test_sin_alta_en_el_destino_ni_provisoria_va_con_el_generico(self):
        self.borrador(alta=False)
        p = await self.preparar(destino="principal")
        self.assertIn("el bot no da altas", p["propuesta"]["comprobantes"][0]["factura"]["revision"][0])
        await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)

    async def test_ensayo_que_falla_o_numero_que_no_sirve_va_con_el_generico(self):
        for respuesta, texto in (({"rc": 1, "error": "no cierra el dígito"}, "no pasó el ensayo"),
                                 ({"empresa_id": 55, "empresa": "De otra compañía"}, "no sirve como proveedor")):
            with self.subTest(texto=texto):
                self.borrador()
                with self.toolbelt(**respuesta):
                    p = await self.preparar(destino="principal")
                factura = p["propuesta"]["comprobantes"][0]["factura"]
                self.assertIn(texto, factura["revision"][0])
                self.assertIn("proveedor a regularizar", factura["proveedor"])

    async def test_con_provisoria_y_alta_se_pide_el_alta_que_la_retoma(self):
        self.borrador()
        self.socio(PROVISORIA, proveedores.PENDIENTE + "Café Norte")
        p = await self.cargar_lote()
        self.assertIn("proveedor nuevo", p["propuesta"]["comprobantes"][0]["factura"]["proveedor"])
        self.assertFalse(any(not aplicar for *_, aplicar in self.corridas), "sin ensayo: el alta la retoma")

    async def test_sin_lectura_va_sin_impuestos_y_marcada(self):
        self.borrador()
        self.guardar(base.lectura(), None)
        p = await self.cargar_lote()
        self.assertIn("Va sin impuestos", p["propuesta"]["comprobantes"][1]["factura"]["revision"][0])
        with self.toolbelt():
            await self.aprobar(destino="principal")
        self.assertEqual([a["res_id"] for a in self.actividades.values()], [self.factura_de(2)["id"]])

    async def test_un_proveedor_a_regularizar_mal_configurado_frena(self):
        self.borrador()
        del self.erps["principal"]["res.partner"][GENERICO]
        self.guardar(base.lectura(cuit=""), None)
        with self.assertRaisesRegex(g.GastoError, "proveedor a regularizar"):
            await self.preparar(destino="principal")

    # ------------------------------------------------------------------------------------------- lote que no es todo o nada
    async def test_el_error_de_un_comprobante_no_frena_a_los_demas(self):
        self.borrador()
        await self.cargar_lote()
        # Entre la propuesta y el OK, alguien cargó a mano el segundo comprobante.
        tabla = self.erps["principal"]["account.move"]
        tabla[38999] = {"id": 38999, "name": "BILL/1", "state": "draft", "company_id": [3, "Casa"],
                        "move_type": "in_invoice", "l10n_latam_document_type_id": [801, "FA"],
                        "l10n_latam_document_number": "00012-00034568", "partner_id": [9002, "Proveedor Dos"]}
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        texto = r["content"][0]["text"]
        self.assertIn("Cargué 1 en borrador", texto)
        self.assertIn("✖ Foto 2", texto)
        self.assertIn("ya está en Odoo", texto)
        self.assertEqual(self.factura_de(1)["partner_id"][0], 9100)
        recibo = self.recibo()
        self.assertEqual(recibo["estado"], "cargado")
        self.assertEqual(set(recibo["resultado"]["no_cargados"]), {"2"})

    async def test_odoo_caido_corta_conserva_lo_cargado_y_el_reintento_retoma(self):
        self.borrador()
        await self.cargar_lote()
        busquedas = []

        def caer(modelo, metodo, args):
            if modelo == "account.move" and metodo == "search_read" and any(
                    isinstance(t, tuple) and t[0] == "invoice_origin" for t in args[0]):
                busquedas.append(args[0])
                return len(busquedas) == 2      # cae al buscar el segundo comprobante
            return False
        self.caer = caer
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        texto = r["content"][0]["text"]
        self.assertIn("Cargué 1 en borrador", texto)
        self.assertIn("✖ Foto 2", texto)
        self.assertIn("se cortó", texto)
        self.assertIn("repetila", texto)
        self.assertEqual(self.recibo()["estado"], "parcial")
        self.assertEqual(len(self.facturas()), 1)
        self.caer = None
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(len(self.facturas()), 2)
        self.assertEqual(self.recibo()["estado"], "cargado")

    async def test_una_actividad_que_falla_no_frena_y_se_dice(self):
        self.borrador()
        await self.cargar_lote()
        self.caer = lambda modelo, metodo, args: modelo == "mail.activity" and metodo == "create"
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(len(self.facturas()), 2)
        self.assertIn("No pude dejarle la actividad", r["content"][0]["text"])

    # ------------------------------------------------------------------------------------------- emisores observados
    async def test_emisor_observado_marca_y_la_nota_lo_dice(self):
        consultados = []

        async def consultar(ctx, marco, cuit):
            consultados.append(cuit)
            if cuit == OTRO:
                return {"observado": True, "texto": f"El CUIT/RUT {cuit} figura en el listado de emisores observados."}
            return {"observado": False, "texto": f"El CUIT/RUT {cuit} no figura en el listado de emisores observados."}
        plugins.registrar(plugins.Plugin(nombre="observados_prueba", grupo="observados_prueba", extensiones=(
            plugins.Extension("gastos.emisores", 1, "observados_prueba", NS(consultar=consultar)),)))
        self.addCleanup(plugins.quitar, "observados_prueba")
        self.borrador(emisores_observados="observados_prueba")
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(sorted(set(consultados)), sorted({base.CUIT, OTRO}))
        self.assertIn("no figura en el listado", self.factura_de(1)["narration"])
        self.assertIn("figura en el listado", self.factura_de(2)["narration"])
        self.assertEqual(len(self.actividades), 2)

    async def test_si_la_consulta_falla_no_frena(self):
        async def consultar(ctx, marco, cuit):
            raise RuntimeError("sin red")
        plugins.registrar(plugins.Plugin(nombre="observados_prueba", grupo="observados_prueba", extensiones=(
            plugins.Extension("gastos.emisores", 1, "observados_prueba", NS(consultar=consultar)),)))
        self.addCleanup(plugins.quitar, "observados_prueba")
        self.borrador(emisores_observados="observados_prueba")
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertIn("No pude consultar", self.factura_de(1)["narration"])

    # ------------------------------------------------------------------------------------------- configuración
    async def test_configuracion_de_la_politica(self):
        c = self.raiz["empresas"]["taller"]
        ids = {"ids": {"ensayo": 1, "principal": 2}}
        for malo, texto in (({"ante_error": "seguir"}, "ante_error"),
                            ({"proveedor_a_regularizar": ids}, "solo con ante_error=borrador"),
                            ({"ante_error": "borrador", "responsable_revision": ids}, "proveedor_a_regularizar"),
                            ({"ante_error": "borrador", "proveedor_a_regularizar": ids,
                              "responsable_revision": {"ids": {"principal": 2}}}, "responsable_revision"),
                            ({"emisores_observados": "x"}, "solo con ante_error=borrador")):
            with self.subTest(malo=malo):
                guardada = copy.deepcopy(c)
                c.update(malo)
                self.escribir()
                with self.assertRaisesRegex(g.GastoError, texto):
                    await self.preparar(destino="principal")
                c.clear(); c.update(guardada)
        c.update(ante_error="frenar")
        self.escribir()
        await self.preparar(destino="principal")

    async def test_sin_la_politica_el_rechazo_frena_como_antes(self):
        self.dos_comprobantes()
        self.con_alta()
        self.socio(9002, "Proveedor Dos", vat=OTRO)
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertTrue(r.get("is_error"))
        self.assertIn("No cargué ninguna factura", r["content"][0]["text"])
        self.assertNotIn("--conservar-pendiente", self.corridas[-1][2])
        self.sin_facturas()

    def test_lo_que_suma_la_carga_no_cambia_lo_firmado(self):
        plan = {"company_id": 3, "odoo_uid": 27}
        x = {"n": 1, "fiscal": {"modo": "con_impuestos", "partner_id": GENERICO, "revision": ["al preparar"]}}
        firma = g.estandar._payload(plan, x)
        x["fiscal"].update(revision_carga=["al cargar"], observado="no figura")
        self.assertEqual(g.estandar._payload(plan, x), firma)
        x["fiscal"]["revision"] = ["otro"]
        self.assertNotEqual(g.estandar._payload(plan, x), firma)

    # ------------------------------------------------------------------------------------------- de la revisión independiente
    async def test_una_factura_incierta_deja_la_corrida_abierta(self):
        self.borrador()
        await self.cargar_lote()
        cayo = []

        def caer(modelo, metodo, args):
            if modelo == "account.move" and metodo == "create" and args[0].get("invoice_origin") == "[L000123/F02]" \
                    and not cayo:
                cayo.append(1)
                return True
            return False
        self.caer = caer
        with self.toolbelt():
            await self.aprobar(destino="principal")
        self.caer = None
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        texto = r["content"][0]["text"]
        self.assertIn("no la pude verificar", texto)
        self.assertIn("antes de repetir la carga", texto)
        self.assertEqual(self.recibo()["estado"], "parcial")
        self.assertEqual(len(self.facturas()), 1)

    async def test_repetir_el_ok_de_una_carga_completa_devuelve_lo_registrado(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt():
            await self.aprobar(destino="principal")
        self.factura_de(1)["state"] = "posted"          # Administración la confirmó
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(set(self.recibo()["resultado"]["facturas"]), {"1", "2"})
        self.assertNotIn("no la cargué", r["content"][0]["text"])

    async def test_un_contacto_que_cambio_entre_intentos_no_se_usa(self):
        self.borrador()
        await self.cargar_lote()
        self.caer = lambda modelo, metodo, args: modelo == "account.move" and metodo == "search_read"
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertEqual(self.recibo()["estado"], "parcial")
        self.erps["principal"]["res.partner"][PROVISORIA]["vat"] = "30777777770"      # Administración le corrigió el número
        self.caer = None
        aplicadas = sum(1 for *_, aplicar in self.corridas if aplicar)
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(sum(1 for *_, aplicar in self.corridas if aplicar), aplicadas)
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)
        self.assertIn("cambió en Odoo", self.factura_de(1)["narration"])

    async def test_el_mismo_comprobante_con_el_proveedor_a_regularizar_no_se_duplica(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, error="no validó"):
            await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], GENERICO)
        otro = self.crear_lote("L000124")
        self.guardar(base.lectura(), base.lectura(cuit=OTRO, numero="0012-00034999"), lote_id="L000124",
                     items=otro["items"])
        with self.toolbelt(), self.assertRaisesRegex(g.GastoError, "ya está en Odoo"):
            await self.preparar(destino="principal", lote_id="L000124")
        # Otro emisor con el mismo número no es el mismo comprobante.
        self.guardar(base.lectura(cuit="30666666660"), base.lectura(cuit=OTRO, numero="0012-00034999"),
                     lote_id="L000124", items=otro["items"])
        with self.toolbelt():
            await self.preparar(destino="principal", lote_id="L000124")

    async def test_un_numero_argentino_mal_leido_va_sin_impuestos_y_marcado(self):
        self.borrador()
        self.guardar(base.lectura(numero="ABC123"), base.lectura(cuit=OTRO, numero="0012-00034568"))
        p = await self.cargar_lote()
        factura = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertEqual(factura["impuestos"], "no")
        self.assertIn("punto de venta", factura["revision"][0])

    async def test_sin_padron_un_proveedor_que_no_esta_va_con_el_generico(self):
        self.borrador(alta=False)
        del self.raiz["empresas"]["taller"]["padron_proveedores"]
        self.escribir()
        p = await self.preparar(destino="principal")
        factura = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertIn("no está en Odoo", factura["revision"][0])
        self.assertIn("proveedor a regularizar", factura["proveedor"])

    async def test_un_aviso_al_retomar_un_contacto_existente_marca(self):
        self.borrador()
        await self.cargar_lote()
        dejo = lambda: self.socio(9100, "Razón del Padrón")
        with self.toolbelt(crea=False, efecto=dejo, empresa_id=9100, empresa="Razón del Padrón",
                           avisos=["Falta un dato fiscal."]):
            await self.aprobar(destino="principal")
        self.assertEqual(self.factura_de(1)["partner_id"][0], 9100)
        self.assertIn("Falta un dato fiscal.", self.factura_de(1)["narration"])

    async def test_un_texto_tecnico_del_toolbelt_no_llega_a_la_nota(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt(rc=1, crea=False, error="Traceback (most recent call last): /srv/alta.py"):
            r = await self.aprobar(destino="principal")
        self.assertNotIn("Traceback", self.factura_de(1)["narration"])
        self.assertNotIn("Traceback", r["content"][0]["text"])

    def test_declarar_frenar_es_lo_mismo_que_omitirlo(self):
        antes = self.seleccion("principal")["firma"]
        self.raiz["empresas"]["taller"]["ante_error"] = "frenar"
        self.assertEqual(self.seleccion("principal")["firma"], antes)

    def test_si_no_entran_todas_las_lineas_las_marcadas_van_primero(self):
        plan = {"comprobantes": [{"n": n, "comercio": f"Comercio {n}", "moneda": "XTS", "importe": "1"}
                                 for n in range(1, 15)]}
        resultado = {"facturas": {str(n): 40000 + n for n in range(1, 15)},
                     "revision": {"14": {"factura": 40014, "motivos": ["Motivo de la catorce."]}}}
        texto = g.texto_carga(resultado, plan, self.seleccion("principal"))
        self.assertIn("Motivo de la catorce.", texto)
        self.assertIn("1 quedó marcado", texto)
