"""Distribución con claves combinadas, lo escrito sobre el comprobante, de quién es y quién pagó.

Todo es inventado: identificaciones fiscales con dígito verificador inválido (no pueden ser de nadie), IDs analíticos,
comercios y anotaciones ficticias.
"""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from tests import test_destinos_plugin as destinos
from tests import test_empresas_plugin as empresas
from tests import test_fiscal_plugin as fiscal_t
from tests import test_gastos_plugin as banco
from bot import lotes, plugins

gastos = banco.gastos
fiscal, cfgmod, estado = gastos.fiscal, gastos.configuracion, gastos.estado
TALLER, OFICINA, AJENO = "30777777770", "30888888880", "20999999990"
RUT_SINTETICO = "999999999999"        # doce dígitos: el formato de otro país


def con(lec, **campos):
    """La lectura `lec` con campos fiscales (receptor_id_fiscal, …) o de primer nivel (anotaciones) cambiados."""
    lec = copy.deepcopy(lec)
    for k, v in campos.items():
        (lec["fiscal"] if k in fiscal._CAMPOS_FISCAL else lec)[k] = v
    return lec


def combinar(distribucion):
    """El imputador de la prueba devuelve `distribucion` en cada comprobante (como la capa que combina dos planes)."""
    plugins.quitar("analitica_factura")

    async def proponer(ctx, marco, lineas):
        return {"lineas": {x["n"]: {"distribucion": distribucion, "grupo": "obra", "rotulo": "Obra de Prueba",
                                    "detalle": "", "texto": "Obra de Prueba", "estado": {}} for x in lineas}}

    async def revalidar(*args):
        return None
    plugins.registrar(plugins.Plugin(nombre="analitica_factura", grupo="analitica_factura", extensiones=(
        plugins.Extension("gastos.imputador", 1, "analitica_factura",
                          SimpleNamespace(proponer=proponer, revalidar=revalidar, nota="Analítica configurada")),)))


# --------------------------------------------------------------------------------------------- distribución combinada
class DistribucionTests(unittest.TestCase):
    def test_clave_combinada_como_tupla_o_texto(self):
        for valor, esperado in (({(1, 166): 100}, {"1,166": 100}), ({"1,166": 100}, {"1,166": 100}),
                                ({(166, 1): 60, 41: 40}, {"166,1": 60, "41": 40}),
                                ({(1, 2, 3, 4, 5): 100}, {"1,2,3,4,5": 100}),
                                ({"1,166": 30.5, (1, 167): 69.5}, {"1,166": 30.5, "1,167": 69.5})):
            with self.subTest(valor=valor):
                self.assertEqual(gastos._distribucion(valor, 1), esperado)
        # La combinación va en el orden del imputador: Odoo no la ordena.
        self.assertEqual(list(gastos._distribucion({(166, 1): 100}, 1)), ["166,1"])

    def test_clave_combinada_invalida(self):
        for valor in ({(1, 1): 100}, {"1,166,1": 100}, {(0, 166): 100}, {(1, -166): 100}, {"0,166": 100},
                      {(1, 2, 3, 4, 5, 6): 100}, {"1,2,3,4,5,6": 100}, {(1,): 100}, {(1, True): 100}, {(1, 1.5): 100},
                      {"1,166,": 100}, {"1;166": 100}, {" 1,166": 100}, {"1, 166": 100}, {"01,166": 100}, {"1,a": 100},
                      {"": 100}, {"41": 100}, {(1, 166): 50, "1,166": 50}, {(1, 166): 0}, {(1, 166): 60}):
            with self.subTest(valor=valor), self.assertRaises(gastos.GastoError):
                gastos._distribucion(valor, 1)

    def test_lo_que_devuelve_el_erp_se_compara_normalizado(self):
        propuesta = gastos._distribucion({(1, 166): 70, 41: 30}, 1)
        # Odoo devuelve el JSON guardado: claves de texto y porcentajes que pueden volver como float.
        for leida in ({"1,166": 70.0, "41": 30.0}, {"41": 30, "1,166": 70}, {"166,1": 70, "41": 30},
                      json.loads(json.dumps(propuesta))):
            self.assertTrue(gastos._misma_distribucion(leida, propuesta), leida)
        for leida in ({"1,167": 70, "41": 30}, {"1,166": 100}, {"1,166": 70, "41": 30, "42": 0}, False, None, [],
                      {"1,1,166": 70, "41": 30}, {"1,166": "setenta", "41": 30}, {"1,166": True, "41": 30},
                      {"1,166": 70, "166,1": 30}):
            self.assertFalse(gastos._misma_distribucion(leida, propuesta), leida)


class CargaCombinadaTests(fiscal_t.FiscalTests):
    """La factura estándar con impuestos lleva la misma clave combinada en todas sus líneas, y la verificación después de
    cargar la compara con lo que devuelve el ERP."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        combinar({(1, 166): 100})

    async def test_todas_las_lineas_llevan_la_combinacion(self):
        self.rows[0]["importe"] = "140.00"
        self.guardar(fiscal_t.lectura(total="140.00", no_gravado="5.00", exento="3.00",
                                      percepciones=(("Percepción IVA", "4.00", "iva"), ("Percepción sin tipo", "2.00")),
                                      otros=(("Impuesto interno", "5.00"),)))
        p, (una, otra) = await self.cargar()
        lineas = self.lineas(una)
        self.assertEqual([l["tax_ids"] for l in lineas], [[721, 732], [723, 732], [724, 732], [], [723, 732]])
        self.assertTrue(all(l["analytic_distribution"] == {"1,166": 100} for l in lineas + self.lineas(otra)))
        self.assertEqual(self.de_impuesto(una), {721: 21.0, 732: 4.0})

    async def test_clave_en_texto_y_combinada_con_un_id(self):
        combinar({"1,166": 60, 41: 40})
        _, facturas = await self.cargar()
        self.assertTrue(all(l["analytic_distribution"] == {"1,166": 60, "41": 40}
                            for f in facturas for l in self.lineas(f)))

    async def test_el_erp_que_la_devuelve_en_otro_orden_o_como_float_verifica(self):
        self.inyectar = ("linea.analytic_distribution", {"166,1": 100.0})
        _, facturas = await self.cargar()
        self.assertEqual(len(facturas), 2)

    async def test_el_erp_que_la_cambia_frena_sin_duplicar(self):
        self.inyectar = ("linea.analytic_distribution", {"1,167": 100})
        await self.preparar(destino="principal")
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        self.assertEqual(len(self.facturas()), 1)

    async def test_combinacion_invalida_no_deja_propuesta(self):
        combinar({(1, 1): 100})
        with self.assertRaisesRegex(gastos.GastoError, "combinaciones de 2 a 5 ids"):
            await self.preparar(destino="principal")
        self.assertFalse(estado.propuesta("L000123", self.seleccion(destino="principal")).exists())


class RendicionCombinadaTests(destinos.DestinosTests):
    """La factura del addon, la estándar y el gasto de control de la rendición llevan la clave combinada."""

    async def test_factura_del_addon_estandar_y_rendicion_con_clave_combinada(self):
        combinar({(1, 166): 100})
        await self.preparar()
        r = await self.aprobar(); self.assertNotIn("is_error", r, r)
        ensayo = self.erps["ensayo"]
        self.assertTrue(all(l["analytic_distribution"] == {"1,166": 100} for l in ensayo["account.move.line"].values()))
        self.assertTrue(all(f["receipt_bill_original_analytic"] == {"1,166": 100} for f in ensayo["account.move"].values()))
        await self.fiscal_principal()
        args = self.args(destino="principal")
        p = await gastos.facturas.operar_rendicion(self.ctx, args, gastos)
        r = await gastos.facturas.operar_rendicion(self.ctx, args, gastos, cargar=True, aprobacion=p["huella"])
        self.assertEqual(r["estado"], "borrador")
        gastos_hr = self.erps["principal"]["hr.expense"].values()
        self.assertEqual(len(gastos_hr), 2)
        self.assertTrue(all(e["analytic_distribution"] == {"1,166": 100} for e in gastos_hr))

    async def test_pago_detalle_en_la_descripcion_del_gasto_de_control(self):
        self.rows[0]["pago_detalle"] = "efectivo AB"
        await self.fiscal_principal()
        args = self.args(destino="principal")
        p = await gastos.facturas.operar_rendicion(self.ctx, args, gastos)
        await gastos.facturas.operar_rendicion(self.ctx, args, gastos, cargar=True, aprobacion=p["huella"])
        aviso = gastos.facturas.AVISO_CONTROL
        self.assertEqual(sorted(e["description"] for e in self.erps["principal"]["hr.expense"].values()),
                         sorted([f"{aviso} Pago: efectivo AB.", aviso]))


class HojasCombinadaTests(banco.ImputadorTests):
    """El circuito de gastos y hojas (sin facturas) también lleva y verifica la clave combinada."""

    async def test_gasto_con_clave_combinada_y_pago_detalle(self):
        buena = {"grupo": "norte", "rotulo": "Norte", "detalle": "", "texto": "t", "estado": {"cuenta": 41}}
        self.imp.salida = {"lineas": {1: {**buena, "distribucion": {(1, 166): 100}},
                                      2: {**buena, "distribucion": {"1,167": 50, 41: 50}}}}
        self.rows[0]["pago_detalle"] = "TC Banco Sintético AB"
        await self.prepararlo()
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        por_nombre = {e["name"]: e for e in self.expenses.values()}
        self.assertEqual(por_nombre["Café Norte [L000123/F01]"]["analytic_distribution"], {"1,166": 100})
        self.assertEqual(por_nombre["Café Sur [L000123/F02]"]["analytic_distribution"], {"1,167": 50, "41": 50})
        self.assertEqual(por_nombre["Café Norte [L000123/F01]"]["description"],
                         "Comprobante: A-123. Pago: TC Banco Sintético AB. Lote L000123, foto 1.")
        self.assertEqual(por_nombre["Café Sur [L000123/F02]"]["description"], "Comprobante: A-124. Lote L000123, foto 2.")
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})           # reintento: verifica y no duplica
        self.assertEqual(len(self.expenses), 2)


# --------------------------------------------------------------------------------------------- lectura
class LecturaTests(unittest.TestCase):
    def test_el_lector_transcribe_anotaciones_y_lee_el_receptor(self):
        self.assertIn("anotaciones", fiscal.ESQUEMA["required"])
        self.assertIn("receptor_id_fiscal", fiscal.ESQUEMA["properties"]["fiscal"]["required"])
        for texto in ("anotaciones: transcripción literal", "sin interpretar, corregir ni completar",
                      "receptor_id_fiscal = CUIT o RUT impreso del receptor", "consumidor final"):
            self.assertIn(texto, fiscal.INSTRUCCIONES)

    def test_anotaciones_acotadas_y_lectura_de_antes(self):
        lec = fiscal.normalizar({"anotaciones": "Obra 12 materiales\n" * 40, "fiscal": {"receptor_id_fiscal": "30-7"}})
        self.assertEqual(len(lec["anotaciones"]), 300)
        self.assertTrue(lec["anotaciones"].startswith("Obra 12 materiales Obra 12"))
        self.assertEqual(lec["fiscal"]["receptor_id_fiscal"], "30-7")
        viejo = fiscal.normalizar({"importe": "1", "fiscal": {"emisor_id_fiscal": TALLER}})
        self.assertEqual((viejo["anotaciones"], viejo["fiscal"]["receptor_id_fiscal"]), ("", ""))


# --------------------------------------------------------------------------------------------- empresa y destino
def validada(**por_empresa):
    raiz = empresas.configuracion()
    for nombre, extra in por_empresa.items():
        raiz["empresas"][nombre].update(extra)
    return cfgmod.validar(raiz)


def leida(receptor="", emisor=""):
    return fiscal.normalizar({"fiscal": {"receptor_id_fiscal": receptor, "emisor_id_fiscal": emisor}})


class EmpresaSugeridaTests(unittest.TestCase):
    DOS = ["taller", "oficina"]

    def test_por_receptor(self):
        cfg = validada(taller={"id_fiscal": TALLER}, oficina={"id_fiscal": OFICINA})
        self.assertEqual(fiscal.empresa_sugerida(cfg, self.DOS, leida("30-88888888-0", TALLER)), ("oficina", "receptor"))
        self.assertEqual(fiscal.empresa_sugerida(cfg, self.DOS, leida(TALLER)), ("taller", "receptor"))

    def test_por_emisor_si_el_receptor_no_figura_o_no_es_de_ninguna(self):
        cfg = validada(taller={"id_fiscal": TALLER, "emisor_id_fiscal_patron": "[0-9]{11}"},
                       oficina={"id_fiscal": OFICINA, "emisor_id_fiscal_patron": "[0-9]{12}"})
        self.assertEqual(fiscal.empresa_sugerida(cfg, self.DOS, leida("", "30-99999999-0")), ("taller", "emisor"))
        self.assertEqual(fiscal.empresa_sugerida(cfg, self.DOS, leida("", RUT_SINTETICO)), ("oficina", "emisor"))
        self.assertEqual(fiscal.empresa_sugerida(cfg, self.DOS, leida(AJENO, RUT_SINTETICO)), ("oficina", "emisor"))

    def test_ambigua_no_elige(self):
        mismo = validada(taller={"id_fiscal": TALLER, "emisor_id_fiscal_patron": "[0-9]{11}"},
                         oficina={"id_fiscal": TALLER, "emisor_id_fiscal_patron": "[0-9]{11,12}"})
        self.assertEqual(fiscal.empresa_sugerida(mismo, self.DOS, leida(TALLER, AJENO)), ("", ""))
        self.assertEqual(fiscal.empresa_sugerida(mismo, self.DOS, leida(TALLER, RUT_SINTETICO)), ("oficina", "emisor"))

    def test_ausente(self):
        sin_claves = validada()
        con_claves = validada(taller={"id_fiscal": TALLER, "emisor_id_fiscal_patron": "[0-9]{11}"})
        for cfg, lec in ((sin_claves, leida(TALLER, AJENO)), (con_claves, leida()), (con_claves, None),
                         (con_claves, leida(AJENO, RUT_SINTETICO)), (con_claves, fiscal.normalizar({}))):
            self.assertEqual(fiscal.empresa_sugerida(cfg, self.DOS, lec), ("", ""))

    def test_solo_entre_las_empresas_accesibles(self):
        cfg = validada(taller={"id_fiscal": TALLER}, oficina={"emisor_id_fiscal_patron": "[0-9]{11}"})
        self.assertEqual(fiscal.empresa_sugerida(cfg, ["oficina"], leida(TALLER, AJENO)), ("oficina", "emisor"))
        self.assertEqual(fiscal.empresa_sugerida(cfg, [], leida(TALLER, AJENO)), ("", ""))

    def test_receptor_ajeno(self):
        self.assertEqual(fiscal.receptor_ajeno({"id_fiscal": TALLER}, leida(AJENO)), AJENO)
        for c, lec in (({"id_fiscal": TALLER}, leida("30-77777777-0")), ({"id_fiscal": TALLER}, leida()),
                       ({}, leida(AJENO)), ({"id_fiscal": TALLER}, None)):
            self.assertEqual(fiscal.receptor_ajeno(c, lec), "")


class ConfiguracionTests(unittest.TestCase):
    USUARIO = SimpleNamespace(odoo_ids_extra={"empleados_gastos": {"taller": 81, "oficina": 91}})

    def test_claves_nuevas_invalidas(self):
        for extra, texto in (({"id_fiscal": "30-77777777-0"}, "id_fiscal"), ({"id_fiscal": int(TALLER)}, "id_fiscal"),
                             ({"id_fiscal": "123"}, "id_fiscal"), ({"emisor_id_fiscal_patron": "["}, "patron"),
                             ({"emisor_id_fiscal_patron": ""}, "patron"), ({"emisor_id_fiscal_patron": 11}, "patron"),
                             ({"destino_por_defecto": "otro"}, "destino_por_defecto"),
                             ({"destino_por_defecto": None}, "destino_por_defecto"),
                             ({"destino_por_defecto": ["principal"]}, "destino_por_defecto")):
            with self.subTest(extra=extra), self.assertRaisesRegex(cfgmod.ConfigError, texto):
                validada(taller=extra)

    def test_no_cambian_la_firma_y_el_destino_por_defecto_es_el_de_la_corrida(self):
        raiz = empresas.configuracion()
        antes = cfgmod.seleccionar(raiz, self.USUARIO, {"empresa": "taller", "destino": "principal"})
        raiz["empresas"]["taller"].update(id_fiscal=TALLER, emisor_id_fiscal_patron="[0-9]{11}",
                                         destino_por_defecto="principal")
        explicito = cfgmod.seleccionar(raiz, self.USUARIO, {"empresa": "taller", "destino": "principal"})
        defecto = cfgmod.seleccionar(raiz, self.USUARIO, {"empresa": "taller"})
        self.assertEqual(antes["firma"], explicito["firma"])
        self.assertEqual((defecto["destino"], defecto["firma"]), ("principal", antes["firma"]))
        self.assertEqual(cfgmod.seleccionar(raiz, self.USUARIO, {"empresa": "taller", "destino": "ensayo"})["destino"],
                         "ensayo")
        with self.assertRaisesRegex(cfgmod.ConfigError, "Elegí destino"):
            cfgmod.seleccionar(raiz, self.USUARIO, {"empresa": "oficina"})


# --------------------------------------------------------------------------------------------- de punta a punta
class PropuestaTests(fiscal_t.FiscalTests):
    """El turno del lote, la barrera de receptor, el destino por defecto y el detalle del pago, contra el ERP sintético."""

    async def lote_cerrado(self, *leidas, lote_id="L000125"):
        from contextlib import asynccontextmanager
        from bot import core, turnos
        lote = {**copy.deepcopy(self.lote), "id": 7, "lote_id": lote_id, "chat_id": "!gastos",
                "persona_uid": "@persona:ejemplo.test"}
        user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@persona:ejemplo.test",
                               odoo_ids_extra=self.user.odoo_ids_extra)

        @asynccontextmanager
        async def turno(_):
            yield
        store = SimpleNamespace(por_pk=lambda _: {"extra": {}}, set_extra=lambda *_: None)
        on_text = AsyncMock(return_value=getattr(core, "TURNO_COMPLETO", "ok"))
        with (patch.object(lotes, "store", return_value=store), patch("bot.identity.User.load", return_value=user),
              patch.object(core.sessions, "get", return_value=object()), patch.object(core, "on_text", on_text),
              patch.object(turnos, "turno", turno),
              patch.object(gastos, "_leer_foto", AsyncMock(side_effect=list(leidas)))):
            await gastos._lote_cerrado(SimpleNamespace(name="matrix", send_grupo=AsyncMock()), lote)
        texto = self.material = on_text.await_args.args[2]
        fotos = {int(l.split(":", 1)[0][5:]): json.loads(l.split(": ", 1)[1])
                 for l in texto.split("\n") if l.startswith("Foto ")}
        return fotos, on_text.await_args.kwargs["nota_sistema"], lote

    async def test_material_del_lote_trae_anotaciones_receptor_y_empresa_sugerida(self):
        self.raiz["empresas"]["taller"]["id_fiscal"] = TALLER
        self.raiz["empresas"]["oficina"]["emisor_id_fiscal_patron"] = "[0-9]{12}"
        self.escribir()
        uno = con(fiscal_t.lectura(), receptor_id_fiscal="30-77777777-0", anotaciones="Obra 12 - materiales - TC AB")
        dos = con(fiscal_t.lectura(cuit=RUT_SINTETICO), anotaciones="")
        fotos, nota, lote = await self.lote_cerrado(uno, dos)
        self.assertEqual((fotos[1]["anotaciones"], fotos[1]["fiscal"]["receptor_id_fiscal"]),
                         ("Obra 12 - materiales - TC AB", "30-77777777-0"))
        self.assertEqual((fotos[1]["empresa_sugerida"], fotos[1]["empresa_motivo"]), ("taller", "receptor"))
        self.assertEqual((fotos[2]["empresa_sugerida"], fotos[2]["empresa_motivo"]), ("oficina", "emisor"))
        # La sugerencia va en el material, no en la lectura guardada (de ella salen los números de la factura).
        guardada = fiscal.lectura("L000125", 1, lote["items"][0]["sha256"])
        self.assertNotIn("empresa_sugerida", guardada)
        self.assertEqual(guardada["anotaciones"], "Obra 12 - materiales - TC AB")
        for texto in ("Preguntá solo lo que falte", "no vuelvas a preguntar lo ya dicho", "empresa_sugerida, sin preguntar",
                      "nunca de la tarjeta o el medio impreso en el voucher", "pago_detalle",
                      "Un lote va a una sola empresa", "mandá ese texto tal cual en imputacion"):
            self.assertIn(texto, nota)
        for palabra in ("tarea", "planning", "proyecto"):
            self.assertNotIn(palabra, nota.lower())

    async def test_lote_de_galeria_con_epigrafe(self):
        # Motor 0.6.9: una galería entra foto por foto, con el id del evento y «#i», y su epígrafe como una nota del lote.
        self.raiz["empresas"]["taller"]["id_fiscal"] = TALLER
        self.escribir()
        for i, item in enumerate(self.lote["items"]):
            item["event_id"] = f"$evento_galeria#{i}"
        self.lote["items"].append({"tipo": "texto", "texto": "Obra 12, pagó AB en efectivo", "event_id": "$evento_galeria#texto"})
        renglones = "Obra 12" + chr(10) + "materiales"          # dos renglones escritos a mano
        fotos, nota, _ = await self.lote_cerrado(con(fiscal_t.lectura(), receptor_id_fiscal=TALLER,
                                                     anotaciones=renglones), {}, lote_id="L000123")
        self.assertEqual(sorted(fotos), [1, 2])
        self.assertEqual((fotos[1]["empresa_sugerida"], fotos[1]["anotaciones"]), ("taller", renglones))
        self.assertEqual(fiscal.lectura("L000123", 1, self.lote["items"][0]["sha256"])["anotaciones"], "Obra 12 materiales")
        self.assertIn("Nota: Obra 12, pagó AB en efectivo", self.material)
        self.assertIn("lo que la persona ya dijo en el lote", nota)
        # La propuesta numera las fotos del lote, no los ids de la galería; la nota no es una foto.
        self.rows[0]["pago_detalle"] = "efectivo AB"
        p = await self.preparar(destino="principal")
        self.assertEqual([x["fotos"] for x in p["propuesta"]["comprobantes"]], [[1], [2]])
        self.assertEqual(p["propuesta"]["comprobantes"][0]["factura"]["impuestos"], "sí")

    async def test_sin_configuracion_de_empresa_no_sugiere(self):
        fotos, _, _ = await self.lote_cerrado(con(fiscal_t.lectura(), receptor_id_fiscal=TALLER), {})
        self.assertEqual({(f["empresa_sugerida"], f["empresa_motivo"]) for f in fotos.values()}, {("", "")})

    def a_nombre_de(self, receptor, id_fiscal=TALLER):
        """La foto 1 a nombre de `receptor`, y la empresa con `id_fiscal` (o sin él)."""
        c = self.raiz["empresas"]["taller"]
        c.pop("id_fiscal", None)
        if id_fiscal:
            c["id_fiscal"] = id_fiscal
        self.escribir()
        self.guardar(con(fiscal_t.lectura(), receptor_id_fiscal=receptor))

    async def test_comprobante_a_nombre_de_otro_receptor_no_se_prepara(self):
        self.a_nombre_de(AJENO)
        with self.assertRaisesRegex(gastos.GastoError, rf"Foto 1: el comprobante está a nombre de otro receptor "
                                                       rf"\(CUIT/RUT {AJENO}; Casa de Prueba taller es {TALLER}\)"):
            await self.preparar(destino="principal")
        self.sin_escrituras()
        # Excluida, el resto del lote se prepara.
        p = await gastos.preparar(self.ctx, {**self.args(destino="principal"), "comprobantes": self.rows[1:],
                                             "excluir_fotos": [1]})
        self.assertEqual(p["propuesta"]["fotos_excluidas"], [1])

    async def test_receptor_con_un_digito_mal_leido_se_carga_y_lo_anota(self):
        self.a_nombre_de("30777777771")          # el de la empresa con el último dígito mal leído
        p = await self.preparar(destino="principal")
        self.assertEqual(p["propuesta"]["comprobantes"][0]["factura"]["impuestos"], "sí")
        self.assertNotIn("is_error", await self.aprobar(destino="principal"))
        una = sorted(self.erps["principal"]["account.move"].values(), key=lambda f: f["id"])[0]
        self.assertIn("se leyó el CUIT/RUT 30777777771, que difiere en un dígito del de Casa de Prueba taller", una["narration"])

    async def test_receptor_confirmado_por_la_persona_se_carga(self):
        self.a_nombre_de(AJENO)
        with self.assertRaisesRegex(gastos.GastoError, "receptor_es_la_empresa=true"):
            await self.preparar(destino="principal")
        args = {**self.args(destino="principal"), "comprobantes": [{**self.rows[0], "receptor_es_la_empresa": True}, self.rows[1]]}
        with self.assertRaisesRegex(gastos.GastoError, "receptor_es_la_empresa"):
            await gastos.preparar(self.ctx, {**args, "comprobantes": [{**self.rows[0], "receptor_es_la_empresa": "sí"},
                                                                      self.rows[1]]})
        await gastos.preparar(self.ctx, args)
        self.assertNotIn("is_error", await self.aprobar(destino="principal"))
        una = sorted(self.erps["principal"]["account.move"].values(), key=lambda f: f["id"])[0]
        self.assertIn(f"se leyó el CUIT/RUT {AJENO}; la persona confirmó, mirando el comprobante, que es el de Casa de "
                      "Prueba taller", una["narration"])

    async def test_receptor_igual_vacio_o_empresa_sin_id_fiscal_se_prepara(self):
        for receptor, id_fiscal in (("30-77777777-0", TALLER), ("", TALLER), (AJENO, "")):
            with self.subTest(receptor=receptor, id_fiscal=id_fiscal):
                self.a_nombre_de(receptor, id_fiscal)
                p = await self.preparar(destino="principal")
                self.assertEqual(p["propuesta"]["comprobantes"][0]["factura"]["impuestos"], "sí")

    async def test_respaldo_a_nombre_de_otro_receptor_tambien_frena(self):
        self.raiz["empresas"]["taller"]["id_fiscal"] = TALLER
        self.escribir()
        self.guardar(fiscal_t.lectura(), con(fiscal_t.lectura(), receptor_id_fiscal=AJENO))
        with self.assertRaisesRegex(gastos.GastoError, "Foto 2: el comprobante está a nombre de otro receptor"):
            await gastos.preparar(self.ctx, {**self.args(destino="principal"),
                                             "comprobantes": [{**self.rows[0], "fotos": [1, 2]}]})

    async def test_el_receptor_se_revisa_otra_vez_al_cargar(self):
        self.a_nombre_de(AJENO, id_fiscal="")
        await self.preparar(destino="principal")
        firma = await gastos._huella_cargar(self.ctx, self.args(destino="principal"))
        self.raiz["empresas"]["taller"]["id_fiscal"] = TALLER         # no cambia la firma: la barrera la frena igual
        self.escribir()
        r = await self.aprobar(destino="principal", huella=firma)
        self.assertTrue(r["is_error"])
        self.assertIn("a nombre de otro receptor", r["content"][0]["text"])
        self.assertIn("a nombre de otro receptor", r["publico"])
        self.assertEqual(self.facturas(), [])

    async def sin_destino(self):
        args = {"lote_id": "L000123", "empresa": "taller"}
        return args, await gastos.preparar(self.ctx, {**args, "comprobantes": self.rows})

    async def test_destino_por_defecto_sin_preguntar(self):
        self.raiz["empresas"]["taller"]["destino_por_defecto"] = "principal"
        self.escribir()
        args, p = await self.sin_destino()
        self.assertEqual(p["destino"], "principal")
        firma = await gastos._huella_cargar(self.ctx, args)
        self.ctx.consumir_aprobacion = lambda *_: firma
        r = await gastos._tool_cargar(self.ctx, args)
        self.assertNotIn("is_error", r, r)
        self.assertEqual(len(self.facturas()), 2)
        catalogo = json.loads((await gastos._tool_catalogo(self.ctx, {"empresa": "taller"}))["content"][0]["text"])
        self.assertEqual((catalogo["destino"], catalogo.get("destino_por_defecto")), ("principal", True))
        pedido = await gastos._tool_catalogo(self.ctx, {"empresa": "taller", "destino": "ensayo"})
        self.assertNotIn("destino_por_defecto", pedido["content"][0]["text"])

    async def test_la_carga_deja_lo_que_ve_la_persona_como_resultado_publico(self):
        self.raiz["empresas"]["taller"]["destino_por_defecto"] = "principal"
        self.escribir()
        args, _ = await self.sin_destino()
        firma = await gastos._huella_cargar(self.ctx, args)
        self.ctx.consumir_aprobacion = lambda *_: firma
        r = await gastos._tool_cargar(self.ctx, args)
        self.assertNotIn("is_error", r, r)
        self.assertEqual(r["publico"], r["content"][0]["text"])
        self.assertIn("en borrador", r["publico"])
        self.assertNotIn('{"', r["publico"])

    async def test_destino_por_defecto_invalido_o_sin_acceso(self):
        self.raiz["empresas"]["taller"]["destino_por_defecto"] = "otro"
        self.escribir()
        with self.assertRaisesRegex(gastos.GastoError, "destino_por_defecto tiene que ser uno de sus destinos"):
            await self.sin_destino()
        self.raiz["empresas"]["taller"]["destino_por_defecto"] = "principal"
        self.escribir()
        self.user.odoo_ids_extra["usuarios_erp"].pop("principal")        # sin acceso al de por defecto: se pregunta
        with self.assertRaisesRegex(gastos.GastoError, "Elegí destino"):
            await self.sin_destino()
        catalogo = json.loads((await gastos._tool_catalogo(self.ctx, {"empresa": "taller"}))["content"][0]["text"])
        self.assertEqual(catalogo["pedido"], "Elegí destino")

    async def test_pago_detalle_en_propuesta_tarjeta_huella_y_nota(self):
        self.rows[0]["pago_detalle"] = "TC Banco Sintético AB"
        p = await self.preparar(destino="principal")
        vista = p["propuesta"]["comprobantes"]
        self.assertEqual(vista[0]["pago_detalle"], "TC Banco Sintético AB")
        self.assertNotIn("pago_detalle", vista[1])
        plan = estado.leer(estado.propuesta("L000123", self.seleccion(destino="principal")))
        self.assertEqual(plan["comprobantes"][0]["pago_detalle"], "TC Banco Sintético AB")
        self.assertNotIn("pago_detalle", plan["comprobantes"][1])
        _, cuerpo = await gastos._resumen_cargar(self.ctx, self.args(destino="principal"), False)
        self.assertIn("empresa (TC Banco Sintético AB) · ", cuerpo)
        r = await self.aprobar(destino="principal"); self.assertNotIn("is_error", r, r)
        una, otra = self.facturas()
        self.assertTrue(una["narration"].endswith("<p>Pago: TC Banco Sintético AB</p>"), una["narration"])
        self.assertIn("CAE 71234567890123", una["narration"])
        self.assertNotIn("Pago:", otra["narration"])

    async def test_cambiar_pago_detalle_invalida_la_propuesta(self):
        self.rows[0]["pago_detalle"] = "efectivo AB"
        antes = (await self.preparar(destino="principal"))["huella"]
        self.rows[0]["pago_detalle"] = "efectivo CD"
        await self.preparar(destino="principal")
        r = await self.aprobar(destino="principal", huella=antes)
        self.assertTrue(r["is_error"])
        self.assertEqual(self.facturas(), [])
        self.assertNotIn("is_error", await self.aprobar(destino="principal"))
        self.assertTrue(self.facturas()[0]["narration"].endswith("<p>Pago: efectivo CD</p>"))

    async def test_pago_detalle_invalido_y_normalizado(self):
        for malo in ("x" * 81, "TC\nAB", 7, ["TC"]):
            self.rows[0]["pago_detalle"] = malo
            with self.subTest(malo=malo), self.assertRaisesRegex(gastos.GastoError, "pago_detalle"):
                await self.preparar(destino="principal")
        for dado, visto in (("  TC   AB  ", "TC AB"), ("", None), (None, None)):
            self.rows[0]["pago_detalle"] = dado
            vista = (await self.preparar(destino="principal"))["propuesta"]["comprobantes"][0]
            self.assertEqual(vista.get("pago_detalle"), visto)

    async def test_pago_sigue_siendo_obligatorio(self):
        self.rows[0]["pago_detalle"] = "efectivo AB"
        self.rows[0].pop("pago")
        with self.assertRaisesRegex(gastos.GastoError, "confirmá quién pagó"):
            await self.preparar(destino="principal")

    def test_las_tools_explican_el_turno_sin_nombrar_proyectos(self):
        from bot import broker
        preparar, catalogo = broker._DESC["gasto_preparar"][0], broker._DESC["gasto_catalogo"][0]
        self.assertIn("nunca de la tarjeta impresa en el voucher", preparar)
        self.assertIn("pago_detalle=quién y con qué pagó", preparar)
        self.assertIn("empresa_sugerida", catalogo)
        self.assertIn("destino por defecto", catalogo)
        self.assertNotIn("proyecto", (preparar + catalogo + gastos.NOTA_SALA).lower())


# Las bases ya corren en sus suites: acá solo se heredan sus bancos.
for _clase in (CargaCombinadaTests, RendicionCombinadaTests, HojasCombinadaTests, PropuestaTests):
    for _nombre in {n for base in _clase.__mro__[1:] for n in vars(base) if n.startswith("test_")}:
        if _nombre not in vars(_clase):
            setattr(_clase, _nombre, None)
