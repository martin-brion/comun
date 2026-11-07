"""Contrato de la rendición de gastos del núcleo: fecha de factura, hojas por criterio de pago, foto y reintento sin
duplicados. El núcleo rinde sin ningún módulo de proyectos (D-103) y no imputa a nada; la imputación es el punto de
extensión `gastos.Imputador`, que acá se prueba con un imputador FICTICIO (clase ImputadorTests).

La compañía, su zona horaria y su diario son los de una empresa ficticia (configuración `gastos`)."""
import asyncio
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import importlib.util
import json
from unittest.mock import patch

if os.name == "nt":  # el bot corre en Linux; estas pruebas de la lógica también corren en Windows
    sys.modules.setdefault("fcntl", SimpleNamespace(flock=lambda *_: None, LOCK_EX=1, LOCK_UN=2))

from dataclasses import replace

from bot import broker, config, lotes, perfil, plugins
with patch.object(config, "PLUGINS_INSTANCIA", True), patch.object(config, "PLUGINS_DIR", Path(os.environ["GASTOS_PLUGIN_DIR"])):
    plugins.cargar_instancia()
gastos = sys.modules["plugins_instancia.gastos"]

try:
    from tests import perfil_ficticio
    from tests.test_correo_motor import configurar_funciones, regir_perfil_completo   # desde la raiz del repo
except ImportError:
    import perfil_ficticio
    from test_correo_motor import configurar_funciones, regir_perfil_completo         # tests/ en el PYTHONPATH

# Configuracion `gastos` de la empresa ficticia: la compañia 3 de Odoo, a UTC-3 (sin horario de verano).
GASTOS = {"company_id": 3, "zona_horaria": "Etc/GMT+3", "diario": {"ids": {"heredado": 61}}, "etiqueta": "XB",
          "compania": "Ejemplo XB", "comprobante": "de prueba", "categorias": {"Comidas": {"ids": {"heredado": 51}}}}


class PuertasTests(unittest.IsolatedAsyncioTestCase):
    async def test_puerta_identidad_y_aviso_se_conservan_en_el_plugin(self):
        from unittest.mock import AsyncMock
        from bot import odoo
        user = SimpleNamespace(login="persona_prueba", odoo_uid=1,
                               odoo_ids_extra={"empleado_gastos": 2}, puede=lambda _: True)
        ctx = SimpleNamespace(user=user, nivel1=lambda: True, secreto=lambda _: None, private_channel=True)
        for modo in ("pin", "cuenta"):
            aviso = AsyncMock()
            with patch.object(config, "MODO_IDENTIDAD", modo), patch.object(config, "config_funcion", return_value={}), patch.object(odoo, "avisar_credenciales", aviso):
                with self.assertRaises(gastos.GastoError) as error:
                    await gastos._puerta(ctx)
                self.assertEqual(str(error.exception), gastos._textos()["gastos_credenciales"])
                if modo == "cuenta": aviso.assert_awaited_once_with(ctx, ["ODOO_LOGIN", "ODOO_API_KEY"])
                else: aviso.assert_not_awaited()
                ctx.nivel1 = lambda: False
                with self.assertRaises(gastos.GastoError) as error:
                    gastos._gate(ctx)
                self.assertEqual(str(error.exception), gastos._textos()["gastos_requiere_acceso"])
                ctx.nivel1 = lambda: True


class ImportesTests(unittest.TestCase):
    def test_importar_no_registra_y_solo_importa_privados_de_resultado(self):
        import ast
        texto = Path(gastos.__file__).read_text(encoding="utf-8")
        for nodo in ast.walk(ast.parse(texto)):
            if isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.module.startswith("bot"):
                self.assertTrue(all(not alias.name.startswith("_") or
                                    (nodo.module == "bot.broker" and alias.name in ("_ok", "_err"))
                                    for alias in nodo.names))
        from bot import extensiones, salas_personales
        registros = (plugins.PLUGINS, plugins.TOOLS, extensiones.PUNTOS, extensiones.PROVEEDORES,
                     lotes._CONSUMIDORES, salas_personales.NOTAS_SISTEMA)
        antes = [x.copy() for x in registros]
        spec = importlib.util.spec_from_file_location("gastos_import_prueba", gastos.__file__)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = modulo
        try:
            spec.loader.exec_module(modulo)
        finally:
            sys.modules.pop(spec.name, None)
        self.assertEqual([x.copy() for x in registros], antes)

    def test_textos_identidad_y_override(self):
        for modo in ("pin", "cuenta"):
            with patch.object(config, "MODO_IDENTIDAD", modo), patch.object(config, "config_funcion", return_value={}):
                textos = gastos._textos()
                self.assertTrue(all(isinstance(t, str) and t for t in textos.values()))
                if modo == "cuenta": self.assertTrue(all("/pin" not in t and "/secreto" not in t for t in textos.values()))
            with patch.object(config, "MODO_IDENTIDAD", modo), patch.object(config, "config_funcion", return_value={
                    "textos": {"gastos_credenciales": "Acceso de la Casa de Prueba pendiente."}}):
                self.assertEqual(gastos._textos()["gastos_credenciales"], "Acceso de la Casa de Prueba pendiente.")

    def test_importe_invalido(self):
        for value in ("0", "-1", "NaN", "12.345"):
            with self.subTest(value=value), self.assertRaises(gastos.GastoError):
                gastos._decimal(value)

    def test_fecha_futura_se_rechaza(self):
        tz = gastos.ZoneInfo("Etc/GMT+3")
        with self.assertRaisesRegex(gastos.GastoError, "futura"):
            gastos._fecha("2999-01-01", tz)
        with self.assertRaisesRegex(gastos.GastoError, "AAAA-MM-DD"):
            gastos._fecha("ayer", tz)


class LoteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumidor_fallido_no_marca_cerrado(self):
        lote = {"id": 1, "lote_id": "L000123", "consumidor": "gastos", "login": "ana.gomez"}
        store = SimpleNamespace(por_pk=lambda _: lote, marcar_cerrado=lambda _: self.fail("cerró lote fallido"))
        async def fail(*_):
            raise RuntimeError("motor temporalmente indisponible")
        async def passthrough(_, item):
            return item
        async def notify(*_):
            return None
        with (patch.object(lotes, "store", return_value=store),
              patch.object(lotes, "consumidor", return_value=SimpleNamespace(nombre="gastos", al_cerrar=fail)),
              patch.object(lotes, "_transcribir_audios", side_effect=passthrough),
              patch.object(lotes, "_manifiesto"), patch.object(lotes, "_notify_owner", side_effect=notify),
              patch.object(lotes.log, "exception")):
            await lotes._cerrar_interno(None, 1)

    async def test_soporte_retoma_lote_cerrando(self):
        row = {"id": 1, "lote_id": "L000123", "estado": "cerrando", "chat_id": "!room",
               "persona_uid": "@ana.gomez:ejemplo.test"}
        async def close(*_, **__):
            row["estado"] = "cerrado"
        with (patch.object(lotes, "store", return_value=SimpleNamespace(lote=lambda _: row)),
              patch.object(lotes, "_cerrar_interno", side_effect=close)):
            result = await lotes.reintentar_cierre(None, "L000123")
        self.assertEqual(result["estado"], "cerrado")


class LoteSinPinTests(unittest.IsolatedAsyncioTestCase):
    """El cierre del lote L000004 leía las fotos, el turno rebotaba por PIN y el lote quedaba despachado sin llegar
    al agente."""

    def setUp(self):
        regir_perfil_completo(self)       # el cierre del lote mira el perfil antes de hacer nada

    def _lote(self):
        return {"id": 1, "lote_id": "L000004", "login": "ana.gomez", "chat_id": "!gastos", "persona_uid": "@ana.gomez:ejemplo.test",
                "items": [{"tipo": "foto", "ruta": "/x/1.jpg"}]}

    def _store(self):
        return SimpleNamespace(por_pk=lambda _: {"extra": {}},
                               set_extra=lambda *_: self.fail("marcó el lote como despachado"))

    async def test_sin_sesion_espera_el_pin_sin_leer_fotos(self):
        from unittest.mock import AsyncMock
        from bot import core
        user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@ana.gomez:ejemplo.test")
        leer, on_text = AsyncMock(), AsyncMock()
        with (patch.object(lotes, "store", return_value=self._store()),
              patch("bot.identity.User.load", return_value=user),
              patch.object(core.sessions, "get", return_value=None),
              patch.object(gastos, "_leer_foto", leer), patch.object(core, "on_text", on_text)):
            with self.assertRaises(lotes.EsperaPin):
                await gastos._lote_cerrado(None, self._lote())
        leer.assert_not_awaited()
        on_text.assert_not_awaited()

    @unittest.skipUnless(hasattr(lotes, "TurnoIncompleto"), "el motor anterior a 0.6.8 no distingue el turno incompleto")
    async def test_turno_incompleto_no_se_da_por_despachado(self):
        # Motor 0.6.8: si el turno no se completó (falló el agente, el cupo), el lote no queda despachado y se retoma.
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock
        from bot import core, turnos
        user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@ana.gomez:ejemplo.test")

        @asynccontextmanager
        async def turno(_):
            yield
        extra = {}
        store = SimpleNamespace(por_pk=lambda _: {"extra": dict(extra)}, set_extra=lambda _pk, e: extra.update(e))
        ch = SimpleNamespace(name="matrix", send_grupo=AsyncMock())
        with (patch.object(lotes, "store", return_value=store),
              patch("bot.identity.User.load", return_value=user),
              patch.object(core.sessions, "get", return_value=object()),
              patch.object(gastos, "_leer_foto", AsyncMock(return_value={})),
              patch.object(core, "on_text", AsyncMock(return_value=None)),
              patch.object(turnos, "turno", turno)):
            with self.assertRaises(lotes.TurnoIncompleto):
                await gastos._lote_cerrado(ch, self._lote())
        self.assertNotIn("gastos_despachado", extra)

    async def test_turno_rebotado_por_pin_no_se_da_por_despachado(self):
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock
        from bot import core, turnos
        user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@ana.gomez:ejemplo.test")

        @asynccontextmanager
        async def turno(_):
            yield
        ch = SimpleNamespace(name="matrix", send_grupo=AsyncMock())
        with (patch.object(lotes, "store", return_value=self._store()),
              patch("bot.identity.User.load", return_value=user),
              patch.object(core.sessions, "get", return_value=object()),
              patch.object(gastos, "_leer_foto", AsyncMock(return_value={})),
              patch.object(core, "on_text", AsyncMock(return_value=core.PIDE_PIN)),
              patch.object(turnos, "turno", turno)):
            with self.assertRaises(lotes.EsperaPin):
                await gastos._lote_cerrado(ch, self._lote())


class ConfiguracionTests(unittest.TestCase):
    """La compañía, la zona horaria y el diario son de la empresa: sin su configuración no se prepara ni se carga."""

    def configurar(self, **gastos):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        configurar_funciones(self, Path(tmp.name), **({"gastos": gastos} if gastos else {}))

    def test_sin_configuracion_lo_dice(self):
        self.configurar()
        with self.assertRaisesRegex(gastos.GastoError, "configuración"):
            gastos._configuracion()
        r = gastos._rotulos()        # el cierre del lote y el lector de fotos no fallan: textos neutros
        self.assertEqual((r["etiqueta"], r["comprobante"]), ("", ""))

    def test_configuracion_invalida_se_rechaza(self):
        for malo in ({**GASTOS, "company_id": "3"}, {**GASTOS, "zona_horaria": "Marte/Olympus"},
                     {**GASTOS, "diario": ""}):
            with self.subTest(malo=malo):
                self.configurar(**malo)
                with self.assertRaises(gastos.GastoError):
                    gastos._configuracion()

    def test_los_textos_nombran_a_la_compania_de_la_configuracion(self):
        self.configurar(**GASTOS)
        c = gastos._configuracion()
        self.assertEqual((c["company_id"], c["diario"], c["compania"], c["etiqueta"]), (3, 61, "Ejemplo XB", "XB"))
        self.assertEqual(gastos._con("diario de compras", c["etiqueta"]), "diario de compras XB")
        self.assertEqual(gastos._con("diario de compras", ""), "diario de compras")


class ApprovalTests(unittest.TestCase):
    def test_carga_directa_sin_ok_l2_se_rechaza(self):
        from bot import broker
        ctx = SimpleNamespace(consumir_aprobacion=lambda *_: "")
        result = asyncio.run(gastos._tool_cargar(ctx, {"lote_id": "L000123"}))
        self.assertTrue(result.get("is_error"))
        self.assertIn("no tiene la aprobación exacta", result["content"][0]["text"])
        self.assertEqual(result["publico"], result["content"][0]["text"])

    def test_un_error_de_disco_no_llega_a_la_persona(self):
        ctx = SimpleNamespace(consumir_aprobacion=lambda *_: "firma")
        with patch.object(gastos, "_operacion", side_effect=OSError("/data/gastos/privado")):
            result = asyncio.run(gastos._tool_cargar(ctx, {"lote_id": "L000123"}))
        self.assertTrue(result["is_error"])
        self.assertEqual(result["publico"], gastos._MOTIVO_TECNICO)

    def test_un_error_de_odoo_no_llega_a_la_persona(self):
        from bot import odoo
        ctx = SimpleNamespace(consumir_aprobacion=lambda *_: "firma")
        async def leer(*_a, **_k):
            await gastos._odoo(ctx, "res.partner", "read", [1])
        with patch.object(odoo, "llamar", side_effect=odoo.OdooError("res.partner: /ruta/interna")), \
                patch.object(gastos, "_operacion", leer):
            result = asyncio.run(gastos._tool_cargar(ctx, {"lote_id": "L000123"}))
        self.assertTrue(result["is_error"])
        self.assertIn("/ruta/interna", result["content"][0]["text"])
        self.assertEqual(result["publico"], gastos._MOTIVO_TECNICO)

    def test_lo_dirigido_a_quien_opera_el_plugin_no_es_el_resultado_publico(self):
        for exc in (gastos.GastoError("Corregí el ID en gastos.json."), gastos.GastoError("El imputador pidió res.partner.write"),
                    gastos.GastoTecnico("Traceback: /ruta/interna")):
            self.assertEqual(gastos._rechazo(exc)["publico"], gastos._MOTIVO_TECNICO)
        for texto in ("No pude leer /padron.json", "No pude dar de alta el CUIT/RUT 1: /opt/alta/contactos.py", "Falló el alta en Z:/Gerencia/planilla.xlsx",
                      "La imputación falló al preparar (FileNotFoundError); no se cargó nada."):
            self.assertEqual(gastos._rechazo(gastos.GastoError(texto))["publico"], gastos._MOTIVO_TECNICO, texto)
        for texto in ("No pude dar de alta el CUIT/RUT 1: el padrón no lo valida.", "Ya está cargada: https://odoo.test/web/1/2"):
            self.assertEqual(gastos._rechazo(gastos.GastoError(texto))["publico"], texto)
        self.assertEqual(gastos._rechazo(gastos.GastoTecnico("/ruta/interna", publico="Sin alta."))["publico"], "Sin alta.")
        rechazo = "Foto 1: el comprobante está a nombre de otro receptor."
        self.assertEqual(gastos._rechazo(gastos.GastoError(rechazo))["publico"], rechazo)

    def test_el_resultado_publico_respeta_el_tope_sin_perder_el_pie(self):
        cuerpo = "\n".join(f"• Factura {n} → https://odoo.test/{n}" for n in range(200))
        publico = gastos._publico({}, cuerpo, "Préstamo: falta revisar.")["publico"]
        self.assertLessEqual(len(publico), 1500)
        lineas = publico.split("\n")
        self.assertEqual(lineas[-1], "Préstamo: falta revisar.")
        self.assertRegex(lineas[-2], r"^• Factura (\d+) → https://odoo\.test/\1$")
        self.assertEqual(gastos._publico({}, ""), {})
        self.assertEqual(broker.texto_publico("gasto_cargar", {"publico": publico}), publico)


class _BancoOdoo(unittest.IsolatedAsyncioTestCase):
    """Un Odoo en memoria con SOLO lo que usa el núcleo de gastos: monedas, categorías, diario, gastos, adjuntos y hojas.
    Cualquier otro modelo (un módulo de proyectos, p. ej.) hace fallar la prueba: el núcleo no depende de él."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(config, "DATA_DIR", Path(self.tmp.name))
        p.start(); self.addCleanup(p.stop)
        for nombre, valor in (("ODOO_URL", "https://heredado.example.test"), ("ODOO_DB", "casa_prueba")):
            p = patch.object(config, nombre, valor); p.start(); self.addCleanup(p.stop)
        configurar_funciones(self, Path(self.tmp.name) / "funciones", gastos=GASTOS)
        regir_perfil_completo(self)
        self.user = SimpleNamespace(login="ana.gomez", name="Ana Gomez", odoo_uid=17,
                                    odoo_ids_extra={"empleado_gastos": 81})
        self.ctx = SimpleNamespace(user=self.user, nivel1=lambda: True, secreto=lambda _: "credential", private_channel=True)
        self.items = []
        for n in (1, 2):
            directory = config.DATA_DIR / "lotes" / "ana.gomez" / "L000123"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{n:03d}_foto.jpg"
            path.write_bytes(b"photo" + bytes([n]))
            self.items.append({"tipo": "foto", "ruta": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.lote = {"lote_id": "L000123", "login": "ana.gomez", "consumidor": "gastos",
                     "estado": "cerrado", "items": self.items}
        self.rows = [
            {"n": 1, "fecha": "2026-09-23", "importe": "123.45", "moneda": "XTS",
             "comercio": "Café Norte", "categoria": "Comidas", "pago": "company_account", "referencia": "A-123"},
            {"n": 2, "fecha": "2026-09-24", "importe": "22", "moneda": "XTS",
             "comercio": "Café Sur", "categoria": "Comidas", "pago": "own_account", "referencia": "A-124"},
        ]
        self.expenses = {}
        self.attachments = {}
        self.sheets = {}
        self.calls = []
        self.patches = [
            patch.object(gastos, "_gate", return_value=None),
            patch.object(gastos, "_identidad", return_value=(17, {"id": 81, "name": "Ana Gomez XB"})),
            patch("bot.lotes.lote_ver", return_value=self.lote),
            patch.object(gastos, "_odoo", side_effect=self.odoo),
            patch("bot.imagen.normalizar", side_effect=self.normalizar),
        ]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    async def normalizar(self, data, **kw):
        return SimpleNamespace(datos=b"jpeg" + data)

    def add_photo(self, n):
        path = config.DATA_DIR / "lotes" / "ana.gomez" / "L000123" / f"{n:03d}_foto.jpg"
        path.write_bytes(b"photo" + bytes([n]))
        self.items.append({"tipo": "foto", "ruta": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    async def odoo(self, ctx, model, method, *args, **kwargs):
        self.calls.append((model, method))
        if model == "res.currency":
            return [{"id": 71, "name": "XTS"}]
        if model == "product.product":
            return [{"id": 51, "name": "Nombre ERP duplicable", "company_id": [3, "XB"],
                     "can_be_expensed": True, "active": True, "default_code": False}]
        if model == "account.journal":
            assert (method, args[0]) == ("read", [61]), (method, args)
            return [{"id": 61, "name": "Gastos de prueba", "type": "purchase", "company_id": [3, "XB"], "active": True}]
        if model == "hr.expense":
            if method == "search_read":
                return [e for e in self.expenses.values() if e["name"] == args[0][0][2]]
            if method == "read":
                return [self.expenses[args[0][0]]]
            if method == "create":
                vals = args[0]
                eid = 2000 + len(self.expenses)
                self.expenses[eid] = {"analytic_distribution": False, **vals, "id": eid, "state": "draft",
                    "company_id": [3, "XB"], "employee_id": [81, "Ana Gomez XB"],
                    "currency_id": [vals["currency_id"], "XTS"], "product_id": [vals["product_id"], "Comidas"],
                    "tax_ids": [], "price_unit": vals["total_amount_currency"]}
                return eid
        if model == "ir.attachment":
            if method == "search_read":
                return [a for a in self.attachments.values() if a["res_id"] == args[0][1][2]
                        and a["name"] == args[0][2][2]]
            if method == "create":
                vals = args[0]
                import base64
                aid = 3000 + len(self.attachments)
                self.attachments[aid] = {"id": aid, "name": vals["name"], "res_id": vals["res_id"],
                    "res_model": vals["res_model"], "checksum": hashlib.sha1(base64.b64decode(vals["datas"])).hexdigest()}
                return aid
            if method == "read":
                return [self.attachments[args[0][0]]]
        if model == "hr.expense.sheet":
            if method == "search_read":
                return [s for s in self.sheets.values() if s["name"] == args[0][0][2]]
            if method == "create":
                vals = args[0]
                sid = 4000 + len(self.sheets)
                self.sheets[sid] = {"id": sid, "name": vals["name"], "state": "draft",
                    "employee_id": [81, "Ana Gomez XB"], "company_id": [3, "XB"],
                    "employee_journal_id": [vals["employee_journal_id"], "Gastos de prueba"],
                    "expense_line_ids": [i for _, i, _ in vals["expense_line_ids"]]}
                for eid in self.sheets[sid]["expense_line_ids"]:
                    self.expenses[eid]["state"] = "reported"
                return sid
            if method == "read":
                return [self.sheets[args[0][0]]]
        raise AssertionError((model, method, args, kwargs))


class WorkflowTests(_BancoOdoo):
    """El núcleo SIN plugin: la rendición completa de un comprobante a un gasto y su hoja, sin imputar a nada."""

    async def test_preparar_y_cargar_dos_veces_no_duplica(self):
        preview = await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        lines = preview["propuesta"]["comprobantes"]
        self.assertEqual([x["pago"] for x in lines], ["company_account", "own_account"])
        self.assertTrue(all("imputacion" not in x for x in lines))
        self.assertNotIn("imputación", preview["aviso"])
        first = await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        second = await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(first["informes"], second["informes"])
        self.assertEqual(len(self.expenses), 2)
        self.assertEqual(len(self.attachments), 2)
        self.assertEqual(len(self.sheets), 2)
        self.assertTrue(all(e["tax_ids"] == [] for e in self.expenses.values()))
        # sin imputación: ninguna distribución analítica, y el ERP solo vio los modelos de la rendición
        self.assertTrue(all(not e["analytic_distribution"] for e in self.expenses.values()))
        self.assertEqual({m for m, _ in self.calls},
                         {"res.currency", "product.product", "account.journal", "hr.expense", "ir.attachment",
                          "hr.expense.sheet"})

    async def test_los_campos_del_gasto_son_los_de_la_rendicion_generica(self):
        await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        e = min(self.expenses.values(), key=lambda v: v["id"])
        self.assertEqual((e["date"], e["payment_mode"], e["product_id"][0], e["currency_id"][0], e["company_id"][0]),
                         ("2026-09-23", "company_account", 51, 71, 3))
        self.assertEqual(e["total_amount_currency"], 123.45)
        self.assertEqual(e["description"], "Comprobante: A-123. Lote L000123, foto 1.")
        self.assertIs(e["analytic_distribution"], False)

    async def test_las_hojas_se_agrupan_por_criterio_de_pago(self):
        self.rows[1]["pago"] = "company_account"
        proposal = await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        self.assertEqual(list(proposal["propuesta"]["informes"]), ["company_account"])
        result = await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(len(result["informes"]), 1)
        self.assertEqual(len(self.sheets), 1)
        self.assertEqual(len(next(iter(self.sheets.values()))["expense_line_ids"]), 2)
        self.assertRegex(next(iter(self.sheets.values()))["name"], r"^Rendición Ana Gomez 20260923 \[L000123\] · Empresa$")

    async def test_el_resumen_del_ok_no_nombra_planning_ni_proyectos(self):
        from bot import broker
        await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        _, approval = await broker.resumen(self.ctx, "gasto_cargar", {"lote_id": "L000123"})
        for palabra in ("tarea", "proyecto", "planning"):
            self.assertNotIn(palabra, approval.lower())
        for nombre in ("gasto_preparar", "gasto_cargar"):
            descripcion = broker._DESC[nombre][0].lower()
            for palabra in ("tarea", "proyecto", "planning"):
                self.assertNotIn(palabra, descripcion, nombre)

    async def test_imputacion_sin_imputador_se_rechaza(self):
        self.rows[0]["imputacion"] = 501
        with self.assertRaisesRegex(gastos.GastoError, "no imputa"):
            await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        self.assertFalse(gastos._plan_path("L000123").exists())

    async def test_factura_y_voucher_en_un_gasto_y_foto_excluida(self):
        from bot import broker
        self.add_photo(3)  # comprobante de pago del mismo gasto que la foto 1
        self.add_photo(4)  # documento que la persona excluyó de esta rendición
        rows = [{**self.rows[0], "fotos": [1, 3]}, self.rows[1]]
        args = {"lote_id": "L000123", "comprobantes": rows, "excluir_fotos": [4]}
        preview = await gastos.preparar(self.ctx, args)
        self.assertEqual(preview["propuesta"]["comprobantes"][0]["fotos"], [1, 3])
        self.assertEqual(preview["propuesta"]["fotos_excluidas"], [4])
        _, approval = await broker.resumen(self.ctx, "gasto_cargar", {"lote_id": "L000123"})
        self.assertIn("Fotos 1, 3", approval)
        self.assertIn("Fotos excluidas: 4", approval)
        first = await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        again = await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(first, again)
        self.assertEqual(len(self.expenses), 2)
        self.assertEqual(len(self.attachments), 3)
        first_id = min(self.expenses)
        self.assertEqual(len([a for a in self.attachments.values() if a["res_id"] == first_id]), 2)
        self.assertFalse(any("foto 04" in a["name"] for a in self.attachments.values()))

    async def test_fotos_sin_destino_o_repetidas_frenan_propuesta(self):
        self.add_photo(3)
        with self.assertRaisesRegex(gastos.GastoError, "foto 3"):
            await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        with self.assertRaisesRegex(gastos.GastoError, "una sola"):
            await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": [
                {**self.rows[0], "fotos": [1, 3]}, {**self.rows[1], "fotos": [2, 3]}]})

    async def test_correccion_incompleta_invalida_propuesta_anterior(self):
        await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        self.assertTrue(gastos._plan_path("L000123").exists())
        self.add_photo(3)
        with self.assertRaisesRegex(gastos.GastoError, "foto 3"):
            await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        self.assertFalse(gastos._plan_path("L000123").exists())

    async def test_falla_del_segundo_respaldo_retoma_sin_duplicados(self):
        self.add_photo(3)
        await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": [
            {**self.rows[0], "fotos": [1, 3]}, self.rows[1]]})
        original = gastos._foto_adjuntar
        failed = False
        async def fail_once(ctx, lote, line, eid, respaldo):
            nonlocal failed
            if respaldo["n"] == 3 and not failed:
                failed = True
                raise gastos.GastoError("segundo respaldo no disponible")
            return await original(ctx, lote, line, eid, respaldo)
        with patch.object(gastos, "_foto_adjuntar", side_effect=fail_once):
            with self.assertRaisesRegex(gastos.GastoError, "segundo respaldo"):
                await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(len(self.expenses), 1)
        self.assertEqual(len(self.attachments), 1)
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(len(self.expenses), 2)
        self.assertEqual(len(self.attachments), 3)
        self.assertEqual(len(self.sheets), 2)

    async def test_falla_de_adjunto_retoma_sin_duplicar_gasto(self):
        await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})
        with patch.object(gastos, "_foto_adjuntar", side_effect=gastos.GastoError("imagen no disponible")):
            with self.assertRaisesRegex(gastos.GastoError, "imagen no disponible"):
                await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(len(self.expenses), 1)
        self.assertEqual(len(self.attachments), 0)
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(len(self.expenses), 2)
        self.assertEqual(len(self.attachments), 2)
        self.assertEqual(len(self.sheets), 2)

    async def test_foto_duplicada_frena_propuesta(self):
        Path(self.items[1]["ruta"]).write_bytes(Path(self.items[0]["ruta"]).read_bytes())
        self.items[1]["sha256"] = self.items[0]["sha256"]
        with self.assertRaisesRegex(gastos.GastoError, "fotos idénticas"):
            await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})

    async def test_categoria_o_moneda_inexistente_frenan(self):
        base = self.odoo
        async def odoo(ctx, model, method, *args, **kwargs):
            if model == "res.currency":
                return []
            return await base(ctx, model, method, *args, **kwargs)
        with patch.object(gastos, "_odoo", side_effect=odoo):
            with self.assertRaisesRegex(gastos.GastoError, "moneda XTS"):
                await gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows})


# --------------------------------------------------------------------------------------------- el punto de extensión
CUENTAS = {"Café Norte": (41, "norte"), "Café Sur": (42, "sur")}     # el imputador ficticio imputa por comercio


class ImputadorFicticio:
    """Un imputador de mentira para probar el punto de extensión: cada comercio tiene una «cuenta» (id analítico) y una
    clave de agrupación. `cuentas_ahora` es lo que el «ERP» dice hoy: cambiarlo entre la vista previa y el OK simula que
    algo cambió. `salida` permite forzar lo que devuelve (para probar la validación del núcleo)."""

    def __init__(self):
        self.cuentas_ahora = dict(CUENTAS)
        self.salida = None
        self.falla_al = ""
        self.llamadas = []

    def plugin(self, nombre="ficimp", grupo="ficimp"):
        return plugins.Plugin(nombre=nombre, grupo=grupo, extensiones=(plugins.Extension("gastos.imputador", 1, nombre, gastos.Imputador(
            self.proponer, self.revalidar, nota="Si hay que elegir cuenta, indicala en imputacion=<id>.")),))

    async def proponer(self, ctx, marco, lineas):
        self.llamadas.append(("proponer", marco, lineas))
        if self.falla_al == "proponer":
            raise RuntimeError("se cayó el servicio de imputación")
        await marco.odoo("res.currency", "search_read", [("name", "=", "XTS")])        # el plugin usa el Odoo nominal
        if self.salida is not None:
            return self.salida
        pendientes, salida = [], {}
        for x in lineas:
            cuenta = self.cuentas_ahora.get(x["comercio"])
            if x["eleccion"] in (41, 42):
                cuenta = next(v for v in CUENTAS.values() if v[0] == x["eleccion"])
            if cuenta is None:
                pendientes.append({"foto": x["n"], "motivo": "comercio sin cuenta",
                                   "candidatas": [{"id": v[0], "cuenta": v[1]} for v in CUENTAS.values()]})
                continue
            salida[x["n"]] = {"distribucion": {cuenta[0]: 100}, "grupo": cuenta[1], "rotulo": f"Cuenta {cuenta[1]}",
                              "detalle": f"Imputado a {cuenta[1]}.", "texto": f"cuenta {cuenta[1]} (#{cuenta[0]})",
                              "estado": {"cuenta": cuenta[0], "dia": x["fecha"]}}
        if pendientes:
            return {"pendiente": pendientes, "opciones": "Elegí la cuenta con imputacion=<id> o corregí la fecha."}
        return {"lineas": salida}

    async def revalidar(self, ctx, marco, lineas):
        self.llamadas.append(("revalidar", marco, lineas))
        if self.falla_al == "revalidar":
            raise RuntimeError("se cayó el servicio de imputación")
        vigentes = {v[0] for v in self.cuentas_ahora.values()}
        for x in lineas:
            if x["imputacion"]["estado"]["cuenta"] not in vigentes:
                return f"Foto {x['n']}: cambió la cuenta"
        return None


class ImputadorTests(_BancoOdoo):
    """El punto de extensión de gastos (gastos.Imputador) con un imputador ficticio."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.imp = ImputadorFicticio()
        self.registrar()

    def registrar(self, imp=None, **kw):
        plugin = (imp or self.imp).plugin(**kw)
        plugins.registrar(plugin)
        self.elegir_imputador(plugin.nombre)
        self.addCleanup(plugins.quitar, plugin.nombre)
        return plugin

    def prepararlo(self, **extra):
        return gastos.preparar(self.ctx, {"lote_id": "L000123", "comprobantes": self.rows, **extra})

    def elegir_imputador(self, nombre):
        import json
        path = Path(config.FUNCIONES_CONFIG_DIR) / "gastos.json"
        datos = json.loads(path.read_text(encoding="utf-8"))
        datos["imputador"] = nombre
        path.write_text(json.dumps(datos), encoding="utf-8")

    def reemplazar_imputador(self, imp, nombre):
        """Saca el imputador ficticio y registra `imp` (con proponer y revalidar) en su lugar."""
        plugins.quitar("ficimp")
        plugins.registrar(plugins.Plugin(nombre=nombre, grupo=nombre, extensiones=(plugins.Extension("gastos.imputador", 1, nombre, gastos.Imputador(imp.proponer, imp.revalidar)),)))
        self.elegir_imputador(nombre)
        self.addCleanup(plugins.quitar, nombre)

    async def test_propuesta_con_imputacion_y_carga_con_distribucion_y_hojas_por_grupo(self):
        self.rows[1]["pago"] = "company_account"
        preview = await self.prepararlo()
        self.assertEqual([x["imputacion"] for x in preview["propuesta"]["comprobantes"]],
                         ["cuenta norte (#41)", "cuenta sur (#42)"])
        self.assertEqual(sorted(preview["propuesta"]["informes"]), ["norte:company_account", "sur:company_account"])
        self.assertRegex(preview["propuesta"]["informes"]["norte:company_account"],
                         r"^Rendición Ana Gomez 20260923 Cuenta norte \[L000123\] · Empresa$")
        self.assertIn("e imputación", preview["aviso"])
        result = await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(len(result["informes"]), 2)
        self.assertEqual({tuple(e["analytic_distribution"].items()) for e in self.expenses.values()},
                         {(("41", 100),), (("42", 100),)})
        self.assertTrue(all(e["description"].startswith("Imputado a ") for e in self.expenses.values()))
        again = await gastos.cargar(self.ctx, {"lote_id": "L000123"})          # reintento: no duplica
        self.assertEqual(again["informes"], result["informes"])
        self.assertEqual((len(self.expenses), len(self.sheets)), (2, 2))

    async def test_el_plugin_recibe_el_marco_y_las_lineas_validadas(self):
        await self.prepararlo()
        nombre, marco, lineas = self.imp.llamadas[0]
        self.assertEqual((marco.company_id, marco.uid, marco.etiqueta), (3, 17, "XB"))
        self.assertEqual(set(lineas[0]), {"n", "fecha", "importe", "moneda", "comercio", "categoria", "pago",
                                          "referencia", "eleccion"})
        self.assertEqual((lineas[0]["importe"], lineas[0]["eleccion"]), ("123.45", None))

    async def test_la_imputacion_entra_en_la_huella_del_lote(self):
        primera = (await self.prepararlo())["huella"]
        otra = (await self.prepararlo())["huella"]
        self.assertNotEqual(primera, otra)                    # propuesta nueva, OK nuevo incluso si no cambian los datos
        self.imp.cuentas_ahora["Café Norte"] = (42, "sur")    # otra imputación -> otra huella (el OK viejo no vale)
        cambiada = (await self.prepararlo())["huella"]
        self.assertNotEqual(primera, cambiada)
        sin_plugin = (await self.prepararlo())["huella"]
        plugins.quitar("ficimp")
        self.elegir_imputador("")
        self.assertNotEqual((await self.prepararlo())["huella"], sin_plugin)

    async def test_pendiente_de_correccion_se_muestra_sin_elegir_y_no_deja_propuesta(self):
        await self.prepararlo()
        self.assertTrue(gastos._plan_path("L000123").exists())
        self.rows[0]["comercio"] = "Café Desconocido"
        result = await self.prepararlo()
        self.assertEqual(result["estado"], "pendiente_correccion")
        self.assertEqual(result["conflictos"][0]["motivo"], "comercio sin cuenta")
        self.assertEqual([c["id"] for c in result["conflictos"][0]["candidatas"]], [41, 42])
        self.assertIn("Elegí la cuenta", result["opciones"])
        self.assertIn("ningún gasto se cargó", result["opciones"])
        self.assertFalse(gastos._plan_path("L000123").exists())
        self.rows[0]["imputacion"] = 41                       # la persona elige entre las candidatas
        revisada = await self.prepararlo()
        self.assertEqual(revisada["propuesta"]["comprobantes"][0]["imputacion"], "cuenta norte (#41)")
        self.assertEqual(self.imp.llamadas[-1][2][0]["eleccion"], 41)

    async def test_revalidacion_al_ok_detecta_un_cambio_y_no_carga(self):
        await self.prepararlo()
        self.imp.cuentas_ahora["Café Norte"] = (99, "otro")
        with self.assertRaisesRegex(gastos.GastoError, "cambió la cuenta.*prepará otra"):
            await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual((self.expenses, self.sheets), ({}, {}))
        nombre, _, lineas = self.imp.llamadas[-1]
        self.assertEqual((nombre, lineas[0]["imputacion"]["estado"]), ("revalidar", {"cuenta": 41, "dia": "2026-09-23"}))

    async def test_salida_invalida_se_rechaza_y_no_deja_propuesta(self):
        buena = {"distribucion": {41: 100}, "grupo": "norte", "rotulo": "R", "detalle": "", "texto": "t",
                 "estado": {"cuenta": 41}}
        def lineas(d):
            return {"lineas": d}
        malas = {
            "no suma 100": lineas({1: {**buena, "distribucion": {41: 60}}, 2: buena}),
            "suma de mas": lineas({1: {**buena, "distribucion": {41: 60, 42: 60}}, 2: buena}),
            "id no entero": lineas({1: {**buena, "distribucion": {"41": 100}}, 2: buena}),
            "id negativo": lineas({1: {**buena, "distribucion": {-1: 100}}, 2: buena}),
            "porcentaje booleano": lineas({1: {**buena, "distribucion": {41: True}}, 2: buena}),
            "sin distribucion": lineas({1: {k: v for k, v in buena.items() if k != "distribucion"}, 2: buena}),
            "campo arbitrario del ERP": lineas({1: {**buena, "account_id": 5}, 2: buena}),
            "grupo con espacios": lineas({1: {**buena, "grupo": "a b"}, 2: buena}),
            "grupo largo": lineas({1: {**buena, "grupo": "x" * 41}, 2: buena}),
            "texto en varias lineas": lineas({1: {**buena, "texto": "a\nb"}, 2: buena}),
            "rotulo no es texto": lineas({1: {**buena, "rotulo": 5}, 2: buena}),
            "estado grande": lineas({1: {**buena, "estado": {"x": "y" * 3000}}, 2: buena}),
            "estado no serializable": lineas({1: {**buena, "estado": {"x": object()}}, 2: buena}),
            "falta una linea": lineas({1: buena}),
            "linea de mas": lineas({1: buena, 2: buena, 3: buena}),
            "dos rotulos para un grupo": lineas({1: buena, 2: {**buena, "rotulo": "otro"}}),
            "dos grupos con el mismo rotulo": lineas({1: buena, 2: {**buena, "grupo": "sur"}}),
            "dos grupos con rotulos que difieren solo en espacios de borde": lineas({1: buena, 2: {**buena, "grupo": "sur", "rotulo": " R "}}),
            "dos grupos sin rotulo": lineas({1: {**buena, "rotulo": ""}, 2: {**buena, "grupo": "sur", "rotulo": ""}}),
            "grupo no vacio sin rotulo": lineas({1: {**buena, "grupo": "", "rotulo": "R"}, 2: {**buena, "rotulo": ""}}),
            "grupo vacio con el rotulo de otro": lineas({1: buena, 2: {**buena, "grupo": ""}}),
            "no es un dict": [buena],
            "clave desconocida": {"lineas": {1: buena, 2: buena}, "tokens": 1},
            "pendiente vacio": {"pendiente": []},
            "pendiente con lineas": {"pendiente": [{"a": 1}], "lineas": {1: buena, 2: buena}},
        }
        for caso, salida in malas.items():
            with self.subTest(caso):
                self.imp.salida = salida
                with self.assertRaises(gastos.GastoError):
                    await self.prepararlo()
                self.assertFalse(gastos._plan_path("L000123").exists())
        self.imp.salida = {"lineas": {1: buena, 2: {**buena, "distribucion": {42: 30.5, 43: 69.5}, "grupo": "norte"}}}
        propuesta = await self.prepararlo()                   # 30,5 + 69,5 = 100: vale
        self.assertEqual(propuesta["estado"], "sin cargar")
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertIn({"42": 30.5, "43": 69.5}, [e["analytic_distribution"] for e in self.expenses.values()])

    async def test_dos_grupos_con_el_mismo_rotulo_no_dejan_borradores_a_medias(self):
        buena = {"distribucion": {41: 100}, "rotulo": "", "detalle": "", "texto": "t", "estado": {}}
        self.imp.salida = {"lineas": {1: {**buena, "grupo": "a"}, 2: {**buena, "grupo": "b"}}}
        with self.assertRaises(gastos.GastoError):
            await self.prepararlo()
        self.assertFalse(gastos._plan_path("L000123").exists())
        self.assertEqual((self.expenses, self.sheets), ({}, {}))

    async def test_el_marco_odoo_del_plugin_solo_lee(self):
        banco = self
        resultados = {}

        class Imp:
            async def proponer(self, ctx, marco, lineas):
                for metodo in ("create", "write", "unlink", "message_post", "button_validate", "action_confirm"):
                    with banco.assertRaisesRegex(gastos.GastoError, "solo puede leer"):
                        await marco.odoo("account.move", metodo, [1])
                resultados["lectura"] = await marco.odoo("res.currency", "search_read", [("name", "=", "XTS")])
                return await banco.imp.proponer(ctx, marco, lineas)

            async def revalidar(self, ctx, marco, lineas):
                return None
        self.reemplazar_imputador(Imp(), "sololeer")
        await self.prepararlo()
        self.assertIn("lectura", resultados)
        self.assertFalse([c for c in self.calls if c[0] == "account.move"])      # ni una llamada llegó al ERP

    async def test_el_marco_odoo_del_plugin_no_lee_los_modelos_bloqueados_del_agente(self):
        from bot import broker
        banco = self
        rechazados = []

        class Imp:
            async def proponer(self, ctx, marco, lineas):
                for modelo in sorted(broker.ODOO_MODELS_DENY) + ["RES.USERS", " ir.config_parameter "]:
                    for metodo in ("search_read", "read", "search", "fields_get"):
                        with banco.assertRaisesRegex(gastos.GastoError, "bloqueado para el bot"):
                            await marco.odoo(modelo, metodo, [])
                        rechazados.append((modelo, metodo))
                return await banco.imp.proponer(ctx, marco, lineas)

            async def revalidar(self, ctx, marco, lineas):
                return None
        self.reemplazar_imputador(Imp(), "sinbloqueados")
        await self.prepararlo()
        self.assertGreaterEqual(len(rechazados), 7 * 4)
        self.assertFalse([c for c in self.calls if c[0] in broker.ODOO_MODELS_DENY])      # ni una llamada llegó al ERP

    async def test_revalidar_no_puede_cambiar_lo_aprobado(self):
        base = self.imp

        class Imp:
            async def proponer(self, ctx, marco, lineas):
                return await base.proponer(ctx, marco, lineas)

            async def revalidar(self, ctx, marco, lineas):
                for x in lineas:
                    x["imputacion"]["distribucion"] = {"999": 37}
                    x["imputacion"]["detalle"] = "inyectado\npor revalidar"
                return None
        self.reemplazar_imputador(Imp(), "muta")
        await self.prepararlo()
        await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual({tuple(e["analytic_distribution"].items()) for e in self.expenses.values()},
                         {(("41", 100),), (("42", 100),)})
        self.assertFalse(any("inyectado" in e["description"] for e in self.expenses.values()))

    async def test_proponer_recibe_la_copia_que_hace_el_nucleo_y_no_la_lista_que_arma(self):
        """Lo que el plugin recibe en `proponer` es la salida de `_copia` (JSON: no comparte ningún objeto con lo del núcleo).
        Mutar lo recibido no tiene un efecto observable desde afuera —el pedido se arma de cero y el núcleo no lo reusa—, así
        que lo que se mide es el mecanismo: el objeto que llega al plugin ES la copia, no la lista original."""
        base = self.imp
        copias, recibido = [], {}
        real = gastos._copia

        def espia(valor):
            copia = real(valor)
            copias.append((valor, copia))
            return copia

        class Imp:
            async def proponer(self, ctx, marco, lineas):
                recibido["lineas"] = lineas
                salida = await base.proponer(ctx, marco, [dict(x) for x in lineas])
                for x in lineas:
                    x["importe"] = "1"
                    x["pago"] = "own_account"
                lineas.append({"n": 99})
                return salida

            async def revalidar(self, ctx, marco, lineas):
                return None
        self.reemplazar_imputador(Imp(), "muta2")
        with patch.object(gastos, "_copia", side_effect=espia):
            r = await self.prepararlo()
        originales = [o for o, c in copias if c is recibido["lineas"]]
        self.assertEqual(len(originales), 1, "lo que recibe el plugin tiene que ser la salida de _copia")
        self.assertIsNot(originales[0], recibido["lineas"])
        self.assertTrue(all(a is not b for a, b in zip(originales[0], recibido["lineas"])))
        self.assertEqual([x["n"] for x in originales[0]], [x["n"] for x in recibido["lineas"] if x["n"] != 99])   # el original no creció
        self.assertEqual([c["importe"] for c in r["propuesta"]["comprobantes"]], ["123.45", "22"])

    def test_copia_no_comparte_ningun_objeto_con_el_original(self):
        original = [{"n": 1, "anidado": {"a": [1, 2]}, "lista": [{"b": 1}]}]
        copia = gastos._copia(original)
        self.assertEqual(copia, original)
        self.assertIsNot(copia, original)
        self.assertIsNot(copia[0], original[0])
        self.assertIsNot(copia[0]["anidado"], original[0]["anidado"])
        self.assertIsNot(copia[0]["anidado"]["a"], original[0]["anidado"]["a"])
        self.assertIsNot(copia[0]["lista"][0], original[0]["lista"][0])

    async def test_excepcion_del_plugin_no_carga_y_el_error_es_claro(self):
        self.imp.falla_al = "proponer"
        with self.assertLogs("motor.gastos", level="ERROR"):
            with self.assertRaisesRegex(gastos.GastoError, r"falló al proponer \(RuntimeError\); no se cargó nada"):
                await self.prepararlo()
        self.assertFalse(gastos._plan_path("L000123").exists())
        self.imp.falla_al = ""
        await self.prepararlo()
        self.imp.falla_al = "revalidar"
        with self.assertLogs("motor.gastos", level="ERROR"):
            with self.assertRaisesRegex(gastos.GastoError, r"falló al revalidar \(RuntimeError\)"):
                await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual((self.expenses, self.sheets, self.attachments), ({}, {}, {}))

    async def test_varios_proveedores_y_seleccion_explicita(self):
        otro = ImputadorFicticio()
        plugins.registrar(otro.plugin(nombre="ficimp2", grupo="ficimp2"))
        self.addCleanup(plugins.quitar, "ficimp2")
        self.elegir_imputador("ficimp2")
        await self.prepararlo()
        self.assertEqual(self.imp.llamadas, [])
        self.assertTrue(otro.llamadas)

    def limitar_perfil(self, *grupos):
        limitado = replace(perfil_ficticio.perfil_todo(), grupos_tools=frozenset(grupos))
        p = patch.object(perfil, "actual", return_value=limitado)
        p.start(); self.addCleanup(p.stop)

    async def test_proveedor_configurado_fuera_del_perfil_falla_cerrado(self):
        self.limitar_perfil("nucleo", "gastos")
        with self.assertRaises(gastos.GastoError):
            await self.prepararlo()
        self.assertEqual(self.imp.llamadas, [])
        self.assertEqual(self.expenses, {})

    async def test_si_cambia_el_imputador_habilitado_entre_la_vista_previa_y_el_ok_no_se_carga(self):
        await self.prepararlo()
        self.limitar_perfil("nucleo", "gastos")
        with self.assertRaisesRegex(gastos.GastoError, "fuera del perfil"):
            await gastos.cargar(self.ctx, {"lote_id": "L000123"})
        self.assertEqual(self.expenses, {})

    async def test_una_propuesta_sin_imputador_no_se_carga_si_aparece_uno(self):
        plugins.quitar("ficimp")
        self.elegir_imputador("")
        await self.prepararlo()
        self.registrar()
        with self.assertRaisesRegex(gastos.GastoError, "configuración"):
            await gastos.cargar(self.ctx, {"lote_id": "L000123"})

    async def test_la_nota_del_plugin_llega_al_turno_del_lote(self):
        from unittest.mock import AsyncMock
        from contextlib import asynccontextmanager
        from bot import core, turnos
        user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@ana.gomez:ejemplo.test")

        @asynccontextmanager
        async def turno(_):
            yield
        on_text = AsyncMock(return_value=getattr(core, "TURNO_COMPLETO", "ok"))     # el turno se completó (motor 0.6.8)
        lote = {"id": 1, "lote_id": "L000123", "login": "ana.gomez", "chat_id": "!gastos", "persona_uid": "@ana.gomez:ejemplo.test",
                "items": [{"tipo": "nota", "texto": "hola"}]}
        store = SimpleNamespace(por_pk=lambda _: {"extra": {}}, set_extra=lambda *_: None)
        with (patch.object(lotes, "store", return_value=store), patch("bot.identity.User.load", return_value=user),
              patch.object(core.sessions, "get", return_value=object()), patch.object(core, "on_text", on_text),
              patch.object(turnos, "turno", turno)):
            await gastos._lote_cerrado(SimpleNamespace(name="matrix", send_grupo=AsyncMock()), lote)
        nota = on_text.await_args.kwargs["nota_sistema"]
        self.assertIn("imputacion=<id>", nota)
        for palabra in ("tarea", "planning"):
            self.assertNotIn(palabra, nota.lower())
