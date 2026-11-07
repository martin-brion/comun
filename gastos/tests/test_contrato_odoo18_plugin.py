"""Los contratos de campos del plugin contra el fields_get real de un Odoo 18 en producción (tests/datos/fields_get_odoo18.json).

Primera carga real (29/10): gasto_preparar cortó con «El destino no expone el contrato estándar requerido de ir.attachment».
En Odoo 18, ir.attachment.res_id es many2one_reference, y los dos contratos (estandar y el control de RRHH) pedían integer.
Los dobles de las pruebas también decían integer, así que nada lo detectaba. El fixture es el esquema real (tipo y readonly),
sin datos. El agente leyó el error como un problema de configuración y ofreció probar en otro destino: el mensaje ahora dice
que es del plugin.
"""
import copy
from contextvars import ContextVar
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from tests import test_gastos_plugin as banco

gastos = banco.gastos
estandar, facturas = gastos.estandar, gastos.facturas
ODOO18 = json.loads((Path(os.environ["GASTOS_DATOS_DIR"]) / "fields_get_odoo18.json").read_text(encoding="utf-8"))


def contratos():
    """Cada contrato de tipos fijos del plugin: (nombre, modelo, {campo: tipo o tupla de tipos})."""
    campo, tipos = estandar._NARRACION
    return ([(f"estandar[{m}]", m, r) for m, r in estandar._CAMPOS.items()]
            + [("estandar LATAM", "account.move", estandar._LATAM), ("estandar fiscal", "account.move", estandar._FISCAL),
               ("estandar fiscal de línea", "account.move.line", estandar._FISCAL_LINEA),
               ("estandar narración", "account.move", {campo: tipos})]
            + [(f"control[{m}]", m, r) for m, r in facturas.CONTRATO_CONTROL.items()])


class Odoo:
    """El destino: responde fields_get con el esquema dado (por defecto, el de Odoo 18) y concede los permisos."""

    def __init__(self, esquema=None):
        self.esquema = copy.deepcopy(esquema or ODOO18)
        self.c = {}
        self._CORRIDA = ContextVar("corrida_prueba", default=self.c)
        self.GastoError = gastos.GastoError
        self.GastoTecnico = gastos.GastoTecnico

    async def _odoo(self, ctx, modelo, metodo, *args, **kw):
        if metodo == "check_access_rights":
            return True
        assert metodo == "fields_get", metodo
        return copy.deepcopy(self.esquema[modelo])


class ContratosOdoo18Tests(unittest.IsolatedAsyncioTestCase):
    def test_el_fixture_es_odoo_18_y_solo_esquema(self):
        self.assertTrue(ODOO18["_origen"]["odoo"].startswith("18."))
        self.assertEqual(ODOO18["ir.attachment"]["res_id"]["type"], "many2one_reference")
        atributos = {k for m, campos in ODOO18.items() if m != "_origen" for d in campos.values() for k in d}
        self.assertEqual(atributos, {"type", "readonly"})

    def test_todos_los_tipos_fijos_estan_en_odoo_18(self):
        revisados = 0
        for nombre, modelo, requeridos in contratos():
            with self.subTest(contrato=nombre):
                self.assertEqual(estandar.diferencias(modelo, ODOO18[modelo], requeridos), [])
                revisados += len(requeridos)
        self.assertGreaterEqual(revisados, 56)

    async def test_el_contrato_estandar_pasa_con_odoo_18(self):
        g = Odoo()
        resultado = await estandar.contrato(None, g)
        self.assertEqual(set(resultado), set(estandar._CAMPOS))
        self.assertTrue(estandar.admite_fiscal(g.c))           # documento LATAM e IVA total: la factura va con impuestos
        self.assertTrue(estandar._admite_narracion(g.c))

    async def test_el_contrato_de_control_pasa_con_odoo_18(self):
        g = Odoo()
        await facturas._contrato_control(None, g.c, g)
        self.assertEqual(g.c["_campos_hr"], ODOO18["hr.expense"])

    async def test_res_id_integer_de_versiones_anteriores_sigue_valiendo(self):
        viejo = copy.deepcopy(ODOO18)
        viejo["ir.attachment"]["res_id"]["type"] = "integer"
        g = Odoo(viejo)
        await estandar.contrato(None, g)
        await facturas._contrato_control(None, g.c, g)

    async def test_lo_que_no_cumple_dice_que_es_del_plugin_y_que_campo(self):
        for modelo, campo, tipo, dice in (("ir.attachment", "datas", "char", "ir.attachment.datas es char"),
                                          ("ir.attachment", "checksum", None, "ir.attachment.checksum falta"),
                                          ("account.move", "l10n_latam_document_number", "integer",
                                           "account.move.l10n_latam_document_number es integer")):
            esquema = copy.deepcopy(ODOO18)
            if tipo is None:
                del esquema[modelo][campo]
            else:
                esquema[modelo][campo]["type"] = tipo
            with self.subTest(campo=campo):
                with self.assertRaises(gastos.GastoError) as e:
                    await estandar.contrato(None, Odoo(esquema))
                texto = str(e.exception)
                self.assertTrue(texto.startswith(estandar.PREFIJO_PLUGIN), texto)
                self.assertIn(dice, texto)
                self.assertIn("Lo arregla quien mantiene el bot", texto)
                self.assertIn("cambiar de destino no lo resuelve", texto)
                self.assertNotIn("gastos.json)", texto)         # no es el error de configuración del bot
        esquema = copy.deepcopy(ODOO18)
        esquema["hr.expense.sheet"]["employee_journal_id"]["type"] = "char"
        g = Odoo(esquema)
        with self.assertRaisesRegex(gastos.GastoError, r"^Plugin de gastos .*hr\.expense\.sheet\.employee_journal_id es char"):
            await facturas._contrato_control(None, g.c, g)


if __name__ == "__main__":
    unittest.main()
