"""El cierre del lote por inactividad sale de gastos.json (lote.cierre_inactividad_min) y por defecto no hay.

Pedido de un inquilino (29/10): una persona empieza una rendición y la retoma al otro día; el cierre fijo a los 30 minutos
cerraba el lote sin un mensaje suyo, y el lote quedaba esperando un evento verificado. Todo es sintético.
"""
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import test_empresas_plugin as empresas
from tests import test_gastos_plugin as banco
from bot import config, lotes

gastos = banco.gastos
cfgmod = gastos.configuracion


def raiz(**lote):
    r = empresas.configuracion()
    if lote:
        r["lote"] = lote
    return r


class ValidadorTests(unittest.TestCase):
    def test_sin_clave_o_en_null_no_hay_cierre(self):
        self.assertIsNone(cfgmod.validar(empresas.configuracion())["lote"]["cierre_inactividad_min"])
        self.assertIsNone(cfgmod.validar(raiz(cierre_inactividad_min=None))["lote"]["cierre_inactividad_min"])
        self.assertEqual(cfgmod.cierre_inactividad(empresas.configuracion()), float("inf"))

    def test_con_minutos(self):
        for minutos in (1, 30, 1440):
            with self.subTest(minutos=minutos):
                self.assertEqual(cfgmod.validar(raiz(cierre_inactividad_min=minutos))["lote"]["cierre_inactividad_min"], minutos)
                self.assertEqual(cfgmod.cierre_inactividad(raiz(cierre_inactividad_min=minutos)), minutos)

    def test_invalida_es_error_de_configuracion(self):
        for valor in (0, -5, 1.5, 30.0, "30", True, [], {}):
            with self.subTest(valor=valor), self.assertRaisesRegex(cfgmod.ConfigError, r"^Configuración del bot.*cierre_inactividad_min"):
                cfgmod.validar(raiz(cierre_inactividad_min=valor))
        for lote in (None, [], "30", {"cierre_inactividad_min": 30, "otra": 1}):
            r = empresas.configuracion()
            r["lote"] = lote
            with self.subTest(lote=lote), self.assertRaisesRegex(cfgmod.ConfigError, r"^Configuración del bot.*\blote\b"):
                cfgmod.validar(r)

    def test_va_en_el_plugin_no_en_la_empresa(self):
        r = empresas.configuracion()
        r["empresas"]["taller"]["lote"] = {"cierre_inactividad_min": 30}
        with self.assertRaisesRegex(cfgmod.ConfigError, "claves desconocidas"):
            cfgmod.validar(r)
        # en la configuración de una sola empresa (plana) va en la raíz y no se toma como campo de la empresa
        plana = {**banco.GASTOS, "lote": {"cierre_inactividad_min": 45}}
        cfg = cfgmod.validar(plana)
        self.assertEqual(cfg["lote"]["cierre_inactividad_min"], 45)
        self.assertNotIn("lote", cfg["empresas"]["principal"])

    def test_no_cambia_la_firma_de_la_corrida(self):
        # el plazo del lote no es parte de lo que aprueba el OK: cambiarlo no invalida una propuesta preparada
        user = banco.SimpleNamespace(odoo_uid=999, odoo_ids_extra={
            "usuarios_erp": {"ensayo": 17, "principal": 27}, "empleados_gastos": {"taller": 81, "oficina": 91},
            "empleados_gastos_destinos": {"taller": {"ensayo": 181}, "oficina": {"ensayo": 191}}})
        for args in ({"empresa": "taller", "destino": "ensayo"}, {"empresa": "oficina", "destino": "principal"}):
            with self.subTest(**args):
                sin = cfgmod.seleccionar(empresas.configuracion(), user, args)["firma"]
                con = cfgmod.seleccionar(raiz(cierre_inactividad_min=45), user, args)["firma"]
                self.assertEqual(sin, con)


class ConsumidorTests(unittest.IsolatedAsyncioTestCase):
    """Lo que el plugin registra en el consumidor de lotes y cómo lo usa el tick del motor."""

    async def asyncSetUp(self):
        self.addCleanup(lotes._CONSUMIDORES.update, dict(lotes._CONSUMIDORES))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(config, "DATA_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        lotes._store = None
        self.addCleanup(setattr, lotes, "_store", None)
        self.cerrados = []

    def registrado(self, cfg):
        with patch.object(config, "config_funcion", return_value=cfg):
            gastos._activar()
        return lotes.consumidor("gastos").cierre_min

    def test_el_plugin_registra_lo_de_gastos_json(self):
        self.assertEqual(self.registrado(empresas.configuracion()), float("inf"))           # default: sin cierre
        self.assertEqual(self.registrado(raiz(cierre_inactividad_min=None)), float("inf"))
        self.assertEqual(self.registrado(raiz(cierre_inactividad_min=45)), 45)
        with self.assertLogs("motor.gastos", "WARNING"):
            self.assertEqual(self.registrado(raiz(cierre_inactividad_min=0)), float("inf"))  # inválida: sin cierre
        self.assertEqual(self.registrado(None), float("inf"))

    async def _lote_quieto(self, cierre_min, minutos_quieto):
        async def al_cerrar(ch, lote):
            self.cerrados.append(lote["lote_id"])
        # el mismo consumidor que registra el plugin, con un al_cerrar de prueba: lo que se mide es el tick del motor
        lotes.registrar_consumidor("gastos", espera_s=60, cierre_min=cierre_min, al_cerrar=al_cerrar)
        pk, lote_id = lotes.store().abrir(chat_id="!gastos", persona_uid="@persona:ejemplo.test", login="persona_prueba",
                                          consumidor="gastos")
        lotes.store().agregar_item(pk, {"tipo": "texto", "texto": "nota", "event_id": f"ev-{lote_id}", "ts": "0"})
        with lotes.store().db() as db:
            db.execute("UPDATE lotes SET actualizado=? WHERE id=?", (time.time() - minutos_quieto * 60, pk))
        canal = banco.SimpleNamespace(send_grupo=lambda *a, **k: _nada(), retirar_grupo=lambda *a, **k: _nada())
        await lotes._tick(canal)
        return lote_id

    async def test_sin_cierre_el_tick_no_cierra_nunca(self):
        lote_id = await self._lote_quieto(float("inf"), 60 * 24 * 365)      # un año quieto
        self.assertEqual(self.cerrados, [])
        self.assertEqual(lotes.store().lote(lote_id)["estado"], "juntando")

    async def test_con_minutos_cierra_pasado_el_plazo(self):
        quieto = await self._lote_quieto(30, 20)
        self.assertEqual(self.cerrados, [])
        self.assertEqual(lotes.store().lote(quieto)["estado"], "juntando")
        cerrado = await self._lote_quieto(30, 31)
        self.assertEqual(self.cerrados, [cerrado])


async def _nada():
    return None


if __name__ == "__main__":
    unittest.main()
