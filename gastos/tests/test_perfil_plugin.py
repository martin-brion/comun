import unittest
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from tests import test_gastos_plugin as banco
from tests import test_circuitos_perfil as soporte
from bot import core, lotes, perfil, config

gastos = banco.gastos
_ConPerfil = soporte._ConPerfil
_explota = soporte._explota
_explota_sync = soporte._explota_sync
AVISO = soporte.AVISO

@asynccontextmanager
async def _noop_turno(uid):
    yield

class GastosLoteCerradoPerfilTests(_ConPerfil, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.armar_acme()

    async def test_cierre_multiempresa_pide_catalogo_sin_elegir_empresa(self):
        self.regir_todo()
        raiz = json.loads((Path(os.environ["GASTOS_CAPA_DIR"]) / "gastos.json").read_text())
        extra = {}
        store = SimpleNamespace(por_pk=lambda _: {"extra": extra}, set_extra=lambda _, e: extra.update(e))
        entregar = AsyncMock()
        with patch.object(config, "config_funcion", return_value=raiz), \
             patch.object(lotes, "store", return_value=store), \
             patch("bot.turno_lote.exigir_sesion"), \
             patch("bot.turno_lote.entregar_lote_a_turno", entregar), \
             patch.object(gastos, "_leer_foto", AsyncMock(return_value={"importe": "10"})):
            await gastos._lote_cerrado(SimpleNamespace(send_grupo=AsyncMock()), self._lote())
        self.assertIn("gasto_catalogo", entregar.await_args.kwargs["nota_sistema"])
        self.assertIn("la empresa_sugerida, sin preguntar", entregar.await_args.kwargs["nota_sistema"])
        self.assertIsNone(gastos._CORRIDA.get())

    @staticmethod
    def _lote():
        return {"id": 1, "lote_id": "L000004", "login": "ana.gomez", "chat_id": "!gastos", "persona_uid": "@ana.gomez:x", "consumidor": "gastos",
                "estado": "cerrado", "items": [{"tipo": "foto", "ruta": "/x/1.jpg"}], "extra": {}}

    def _bloqueos(self):
        parches = [patch.object(lotes, "store", MagicMock(return_value=SimpleNamespace(
                       por_pk=_explota_sync("lotes.store().por_pk"), set_extra=_explota_sync("lotes.store().set_extra")))),
                   patch("bot.identity.User.load", _explota_sync("User.load")),
                   patch.object(core.sessions, "get", _explota_sync("core.sessions.get")),
                   patch.object(gastos, "_leer_foto", _explota("_leer_foto (modelo)")),
                   patch.object(core, "on_text", _explota("core.on_text (el turno)")),
                   patch.object(core, "notify_owner", AsyncMock())]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        return parches

    async def test_acme_no_lee_fotos_ni_arma_turno_y_avisa_en_la_sala(self):
        self.regir_acme()
        self.assertFalse(perfil.habilita(perfil.actual(), gastos.GRUPO_PERFIL))
        self._bloqueos()
        ch = SimpleNamespace(name="matrix", send_grupo=AsyncMock(return_value="m1"))
        lote = self._lote()
        with patch.object(lotes, "registrar_privado") as privado, \
             self.assertLogs("motor.gastos", level="WARNING") as logs:
            self.assertIsNone(await gastos._lote_cerrado(ch, lote))    # None: lotes lo marca cerrado
        ch.send_grupo.assert_awaited_once_with("!gastos", AVISO)
        privado.assert_called_once_with(lote, AVISO)
        core.notify_owner.assert_not_awaited()
        self.assertTrue(any("L000004" in linea for linea in logs.output))

    async def test_restrictivo_igual_que_acme(self):
        self.regir_restrictivo()
        self._bloqueos()
        ch = SimpleNamespace(name="matrix", send_grupo=AsyncMock(return_value="m1"))
        with patch.object(lotes, "registrar_privado"):
            self.assertIsNone(await gastos._lote_cerrado(ch, self._lote()))
        ch.send_grupo.assert_awaited_once_with("!gastos", AVISO)

    async def test_sin_canal_solo_log_y_no_revienta(self):
        self.regir_acme()
        self._bloqueos()
        with self.assertLogs("motor.gastos", level="WARNING"):
            self.assertIsNone(await gastos._lote_cerrado(None, self._lote()))

    async def test_acme_con_el_grupo_y_completo_llegan_al_turno(self):
        for nombre, regir in (("configurado con gastos", lambda: self.regir_acme(("nucleo", "gastos"))),
                              ("completo", self.regir_todo)):
            with self.subTest(perfil=nombre):
                regir()
                self.assertTrue(perfil.habilita(perfil.actual(), gastos.GRUPO_PERFIL))
                extra = {}
                store = SimpleNamespace(por_pk=lambda _: {"extra": dict(extra)},
                                        set_extra=lambda _pk, e: extra.update(e))
                user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@ana.gomez:x")
                ch = SimpleNamespace(name="matrix", send_grupo=AsyncMock(return_value="m1"))
                on_text = AsyncMock(return_value=getattr(core, "TURNO_COMPLETO", None))   # el turno se completó (motor 0.6.8)
                with patch.object(lotes, "store", MagicMock(return_value=store)), \
                     patch("bot.identity.User.load", return_value=user), \
                     patch.object(core.sessions, "get", return_value=object()), \
                     patch.object(gastos, "_leer_foto", AsyncMock(return_value={"comercio": "x"})) as leer, \
                     patch.object(core, "on_text", on_text), patch("bot.turnos.turno", _noop_turno):
                    self.assertIsNone(await gastos._lote_cerrado(ch, self._lote()))
                leer.assert_awaited_once()
                on_text.assert_awaited_once()
                self.assertEqual(on_text.await_args.args[1], "matrix:@ana.gomez:x")
                self.assertIn("MATERIAL DEL LOTE L000004", on_text.await_args.args[2])
                self.assertTrue(extra.get("gastos_despachado"))
                self.assertEqual(ch.send_grupo.await_args.args, ("!gastos", "Cerré el lote; estoy leyendo los comprobantes…"))

