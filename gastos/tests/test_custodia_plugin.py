"""Durabilidad y liberación de candados sin ERP real."""
import asyncio
from pathlib import Path
from unittest.mock import patch
from tests import test_gastos_plugin as banco
from bot import config

gastos = banco.gastos
custodia, estado = gastos.custodia, gastos.estado


class CustodiaTests(banco._BancoOdoo):
    async def test_cancelacion_libera_descriptor_y_no_bloquea_el_event_loop(self):
        dentro = asyncio.Event()
        async def mantener():
            async with custodia.exclusion("L000123", {}):
                dentro.set()
                await asyncio.Event().wait()
        tarea = asyncio.create_task(mantener())
        try:
            await asyncio.wait_for(dentro.wait(), 2)
            self.assertTrue(custodia.ocupado("L000123", {}))
            tarea.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tarea
            self.assertFalse(custodia.ocupado("L000123", {}))
            async with custodia.exclusion("L000123", {}):
                self.assertTrue(custodia.ocupado("L000123", {}))
        finally:
            if not tarea.done():
                tarea.cancel()

    async def test_purga_retiene_lote_con_candado_de_otra_corrida(self):
        async with custodia.exclusion("L000123", {}):
            self.assertTrue(gastos._retener(self.lote, 10**12))

    async def test_fsync_fallido_no_reemplaza_estado_ni_deja_temporales(self):
        p = estado.raiz("L000123") / "prueba.json"
        estado.guardar(p, {"antes":1})
        original = p.read_bytes()
        with patch.object(estado.os,"fsync",side_effect=OSError("Fallo sintético de persistencia")):
            with self.assertRaises(OSError):
                estado.guardar(p,{"despues":2})
        self.assertEqual(p.read_bytes(),original)
        self.assertEqual(list(p.parent.glob("prueba.json.*.tmp")),[])

    async def test_candado_no_admite_enlace_ni_archivo_con_multiples_nombres(self):
        p = custodia.ruta("L000123",{})
        objetivo = config.DATA_DIR / "otro.json"
        objetivo.write_text("evidencia",encoding="utf-8")
        p.symlink_to(objetivo)
        with self.assertRaises((OSError,estado.EstadoError)):
            async with custodia.exclusion("L000123",{}):
                self.fail("Aceptó el enlace")
        p.unlink()
        import os
        os.link(objetivo,p)
        with self.assertRaises(estado.EstadoError):
            async with custodia.exclusion("L000123",{}):
                self.fail("Aceptó el enlace duro")
        self.assertEqual(objetivo.read_text(),"evidencia")
