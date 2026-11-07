"""La capa sintética distribuida respeta los contratos en sus cuatro combinaciones."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tests import test_gastos_plugin as banco
from bot import config, plugins


class CapaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.raiz = Path(os.environ["GASTOS_CAPA_DIR"])
        self.datos = json.loads((self.raiz / "gastos.json").read_text())
        self.reglas = json.loads((self.raiz / "capa_prueba.json").read_text())
        self.user = SimpleNamespace(odoo_ids_extra=json.loads((self.raiz / "vinculos.json").read_text())["persona_prueba"])
        self.capa = plugins._modulo(self.raiz / "capa_prueba.py")
        self.addCleanup(plugins._quitar_modulos, "capa_prueba")

    async def test_ejemplo_facturas_con_y_sin_rendicion_separa_cuentas_de_productos(self):
        raiz = json.loads((self.raiz / "gastos_facturas.json").read_text())
        for empresa, habilitada in (("taller", True), ("oficina", False)):
            for destino in ("ensayo", "principal"):
                c = banco.gastos.configuracion.seleccionar(raiz,self.user,{"empresa":empresa,"destino":destino})
                self.assertIs(c["rendicion"],habilitada)
                self.assertEqual(c["employee_id"],0)
                self.assertEqual(set(c["cuentas"]),set(c["categorias"]))
                for n in c["cuentas"]:
                    self.assertNotEqual(c["cuentas"][n],c["categorias"][n]["id"])

    async def test_ejemplo_destinos_mixtos_requiere_confirmacion_administrativa_hr(self):
        raiz=json.loads((self.raiz/"gastos_destinos.json").read_text())
        for empresa in ("taller","oficina"):
            for destino,modo in (("ensayo","addon"),("principal","estandar")):
                c=banco.gastos.configuracion.seleccionar(raiz,self.user,{"empresa":empresa,"destino":destino})
                self.assertEqual(c["capacidades"]["factura"],modo)
                self.assertFalse(c["capacidades"]["control_sin_contabilizar_confirmado"])
                self.assertNotIn("capacidades",c["endpoint"])
                if empresa=="oficina":
                    self.assertEqual(c["capacidades"]["rendicion"],"ninguna")

    async def test_configuracion_y_proveedores_en_cuatro_combinaciones(self):
        self.assertEqual(len(self.capa.PLUGIN.extensiones), 4)
        for empresa in ("taller", "oficina"):
            for destino in ("ensayo", "principal"):
                c = banco.gastos.configuracion.seleccionar(self.datos, self.user, {"empresa": empresa, "destino": destino})
                ident = self.reglas["cuentas"][empresa][destino]
                async def leer(modelo, metodo, ids, **kw):
                    self.assertEqual((modelo, metodo, ids), ("account.analytic.account", "read", [ident]))
                    return [{"id": ident, "name": "Cuenta de Prueba", "company_id": [c["company_id"], "Casa"], "active": True}]
                marco = SimpleNamespace(empresa=empresa, destino=destino, company_id=c["company_id"], odoo=leer)
                with patch.object(config, "config_funcion", return_value=self.reglas):
                    resultado = await self.capa.proponer(None, marco, [{"n": 1}])
                    self.assertEqual(resultado["lineas"][1]["distribucion"], {ident: 100})
                    self.assertIsNone(await self.capa.revalidar_imputacion(None, marco, [{"imputacion": resultado["lineas"][1]}]))
                self.assertTrue(all("id" in ref for ref in c["categorias"].values()))
        self.assertNotIn("Comidas", self.datos["empresas"]["oficina"]["categorias"])

    async def test_politicas_de_ejemplo_observan_o_bloquean_y_revalidan(self):
        with patch.object(config, "config_funcion", return_value=self.reglas):
            for empresa, bloquea in (("taller", False), ("oficina", True)):
                marco = SimpleNamespace(empresa=empresa)
                lineas = [{"moneda": "XTS", "importe": "300.00"}]
                r = await self.capa.evaluar(None, marco, lineas)
                self.assertTrue(r["observaciones"])
                self.assertEqual(bool(r["bloqueos"]), bloquea)
                self.assertIsNone(await self.capa.revalidar_politica(None, marco, lineas, r))
                self.reglas["topes"][empresa]["importe"] = "400.00"
                self.assertIsNotNone(await self.capa.revalidar_politica(None, marco, lineas, r))
