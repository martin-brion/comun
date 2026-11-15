"""Anular una carga recién hecha (gasto_anular) y la declaración de «guardar sin preguntar» de gasto_cargar.

Reglas que fija:
  - la anulación borra exactamente lo que la carga creó (gastos, adjuntos, informes) y deja el lote listo para prepararse
    de nuevo; el recibo queda aparte, como constancia;
  - si un borrador cambió de manos, se validó o ya no está, no se borra NADA;
  - sin una carga completa, de otra persona, o con pasos posteriores, no se anula;
  - el plugin solo puede borrar mientras anula, y solo los modelos que la carga crea;
  - en un motor con `Tool.deshacer`, gasto_cargar declara gasto_anular como su inversa; con un alta de proveedor o con
    seguimiento, esa carga sigue con tarjeta. En un motor anterior el plugin carga igual, sin la declaración.
"""
from types import SimpleNamespace
from unittest.mock import patch

from bot import broker, config, plugins
from tests.test_gastos_plugin import _BancoOdoo, gastos

ODOO_REAL = gastos._odoo          # el banco reemplaza gastos._odoo: la puerta de escrituras se prueba con la de verdad
LOTE = {"lote_id": "L000123"}


class _Banco(_BancoOdoo):
    """El banco de siempre, más lo que usa la anulación: buscar por id con su autor, y borrar."""

    def _tabla(self, model):
        return {"hr.expense": self.expenses, "ir.attachment": self.attachments, "hr.expense.sheet": self.sheets}[model]

    async def odoo(self, ctx, model, method, *args, **kwargs):
        propios = ("hr.expense", "ir.attachment", "hr.expense.sheet")
        if model in propios and method == "search_read" and args and args[0] and tuple(args[0][0][:2]) == ("id", "in"):
            self.calls.append((model, method))
            return [{"create_uid": [17, "Ana Gomez"], "write_uid": [17, "Ana Gomez"], **fila}
                    for i, fila in self._tabla(model).items() if i in args[0][0][2]]
        if method == "unlink":
            self.calls.append((model, method))
            for i in args[0]:
                del self._tabla(model)[i]
            return True
        return await super().odoo(ctx, model, method, *args, **kwargs)

    async def cargado(self):
        await gastos.preparar(self.ctx, {**LOTE, "comprobantes": self.rows})
        return await gastos.cargar(self.ctx, dict(LOTE))


class AnularTests(_Banco):
    async def test_anula_lo_que_cargo_y_el_lote_se_puede_preparar_de_nuevo(self):
        await self.cargado()
        self.assertEqual((len(self.expenses), len(self.attachments), len(self.sheets)), (2, 2, 2))
        r = await gastos._anular(self.ctx, dict(LOTE))
        self.assertEqual((r["estado"], r["documentos"], r["borrados"]), ("anulado", 2, 6))
        self.assertEqual((self.expenses, self.attachments, self.sheets), ({}, {}, {}))
        borrados = [m for m, metodo in self.calls if metodo == "unlink"]
        self.assertEqual(borrados, ["hr.expense.sheet", "ir.attachment", "hr.expense"], "primero el informe, al final el gasto")
        c = gastos._seleccionar(self.ctx, dict(LOTE))
        self.assertIsNone(gastos.estado.recibo("L000123", c), "el recibo ya no cuenta")
        constancias = list(gastos.estado.recibo_path("L000123", c).parent.glob("*.anulado.*.json"))
        self.assertEqual(len(constancias), 1, "queda la constancia de lo anulado")
        self.assertFalse(gastos._plan_path("L000123").exists())
        self.assertEqual(gastos.estado.cargados(["L000123"]), [])
        self.rows[0]["importe"] = "100"
        again = await self.cargado()
        self.assertEqual(len(again["gastos"]), 2)
        self.assertEqual(sorted(e["total_amount_currency"] for e in self.expenses.values()), [22.0, 100.0])

    async def test_si_alguien_lo_toco_no_borra_nada(self):
        await self.cargado()
        casos = {"otra persona lo cambió": lambda: next(iter(self.expenses.values())).update(write_uid=[2, "Administración"]),
                 "se validó": lambda: next(iter(self.sheets.values())).update(state="approve"),
                 "ya no está": lambda: self.attachments.pop(next(iter(self.attachments))),
                 "el adjunto cuelga de otro registro": lambda: next(iter(self.attachments.values())).update(res_id=999)}
        for motivo, tocar in casos.items():
            with self.subTest(motivo=motivo):
                copia = ({k: dict(v) for k, v in self.expenses.items()}, {k: dict(v) for k, v in self.attachments.items()},
                         {k: dict(v) for k, v in self.sheets.items()})
                tocar()
                antes = (dict(self.expenses), dict(self.attachments), dict(self.sheets))
                with self.assertRaisesRegex(gastos.GastoError, "no anulo nada"):
                    await gastos._anular(self.ctx, dict(LOTE))
                self.assertEqual((self.expenses, self.attachments, self.sheets), antes)
                self.assertNotIn("unlink", [metodo for _, metodo in self.calls])
                self.expenses, self.attachments, self.sheets = copia
        c = gastos._seleccionar(self.ctx, dict(LOTE))
        self.assertEqual(gastos.estado.recibo("L000123", c)["estado"], "cargado", "la carga sigue en pie")

    async def test_sin_una_carga_completa_o_de_otra_persona_no_anula(self):
        await gastos.preparar(self.ctx, {**LOTE, "comprobantes": self.rows})
        with self.assertRaisesRegex(gastos.GastoError, "no tiene una carga completa"):
            await gastos._anular(self.ctx, dict(LOTE))
        await gastos.cargar(self.ctx, dict(LOTE))
        otra = SimpleNamespace(user=SimpleNamespace(login="otra.persona", name="Otra", odoo_uid=18,
                                                    odoo_ids_extra={"empleado_gastos": 82}),
                               nivel1=lambda: True, secreto=lambda _: "credential", private_channel=True)
        with self.assertRaisesRegex(gastos.GastoError, "otra persona"):
            await gastos._anular(otra, dict(LOTE))
        self.assertEqual(len(self.expenses), 2)

    async def test_la_tool_cuenta_lo_anulado_y_su_tarjeta_dice_que_borra(self):
        await self.cargado()
        titulo, cuerpo = await broker.resumen(self.ctx, "gasto_anular", dict(LOTE))
        self.assertEqual(titulo, "Anular una carga de gastos")
        self.assertIn("los 2 borradores que creó la carga del lote L000123", cuerpo)
        r = await gastos._tool_anular(self.ctx, dict(LOTE))
        self.assertFalse(r.get("is_error"), r)
        self.assertIn("Anulé la carga del lote L000123: borré de Odoo 2 borradores", r["publico"])
        r = await gastos._tool_anular(self.ctx, dict(LOTE))
        self.assertTrue(r.get("is_error"), "anular dos veces no hace nada")
        self.assertIn(config.MCP_PREFIX + "gasto_anular", broker.WRITE_TOOLS, "es una escritura: fuera de lo reciente, pide tarjeta")


class PuertaDeEscriturasTests(_Banco):
    async def test_el_plugin_solo_borra_mientras_anula_y_solo_lo_suyo(self):
        for corrida, modelo in (({"_aplicando": True}, "hr.expense"), ({}, "hr.expense"), ({"_anulando": True}, "res.partner"),
                                ({"_anulando": True}, "account.move.line")):
            token = gastos._CORRIDA.set(corrida)
            try:
                with self.subTest(corrida=corrida, modelo=modelo), self.assertRaisesRegex(gastos.GastoError, "no permite esta escritura"):
                    await ODOO_REAL(self.ctx, modelo, "unlink", [1])
            finally:
                gastos._CORRIDA.reset(token)


class DeclaracionTests(_Banco):
    async def test_la_carga_declara_su_inversa_si_el_motor_la_conoce(self):
        tool = plugins.TOOLS["gasto_cargar"]
        if "deshacer" not in plugins.Tool.__dataclass_fields__:
            self.assertEqual(gastos._deshacer_de_la_carga(), {}, "en un motor anterior el plugin carga sin la declaración")
            return
        d = tool.deshacer
        self.assertEqual((d.tool, d.idempotente), ("gasto_anular", True))
        args = {"lote_id": "L000123", "empresa": "", "destino": ""}
        self.assertEqual(d.args({"args": args, "resultado": {}, "previo": None, "datos": {}, "id": "g1"}), args)
        self.assertIsNone(plugins.TOOLS["gasto_anular"].deshacer, "anular no se deshace")
        self.assertIsNone(plugins.TOOLS["gasto_rendir"].deshacer, "la rendición es un reintegro: siempre con tarjeta")

    async def test_con_alta_de_proveedor_o_seguimiento_la_carga_sigue_con_tarjeta(self):
        await gastos.preparar(self.ctx, {**LOTE, "comprobantes": self.rows})
        self.assertEqual(await gastos._previo_directo(self.ctx, dict(LOTE)), {"lote_id": "L000123"})
        plan = gastos._leer("L000123")
        plan["comprobantes"][0]["fiscal"] = {"alta_proveedor": {"id_fiscal": "00-00000000-0"}}
        with patch.object(gastos, "_leer", return_value=plan):
            self.assertIsNone(await gastos._previo_directo(self.ctx, dict(LOTE)))
        real = gastos._seleccionar
        with patch.object(gastos, "_seleccionar", side_effect=lambda ctx, a: {**real(ctx, a), "seguimiento": "posterior"}):
            self.assertIsNone(await gastos._previo_directo(self.ctx, dict(LOTE)))
            await gastos.cargar(self.ctx, dict(LOTE)) if False else None
        self.assertIsNone(await gastos._previo_directo(self.ctx, {"lote_id": "L999999"}), "sin propuesta, con tarjeta")
