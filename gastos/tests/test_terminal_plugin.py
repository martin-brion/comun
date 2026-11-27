"""`gastos cargar` desde una terminal (0.1.0-dev.21), sobre bot.terminal del motor 0.6.33: el mismo preparar → OK → cargar
del chat, con un lote local, las lecturas y la propuesta del agente de código, el OK tecleando la huella y la presentación
como .eml sin enviar. Con un motor anterior, la suite no corre: el plugin carga igual, sin el comando.

Todo inventado: CUIT de prueba con dígito verificador inválido, direcciones en example.test, IDs y comercios ficticios.
"""
import copy
import importlib
import email
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_presentacion_plugin as presentacion
from tests import test_fiscal_plugin as base
from bot import config, lotes, plugins

g = base.gastos
try:
    from bot import terminal
except ImportError:                                   # motor anterior a 0.6.33
    terminal = None


@unittest.skipUnless(terminal is not None, "el motor no trae bot.terminal (0.6.33)")
class TerminalTests(presentacion.PresentacionTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tres_comprobantes()
        self.carpeta = Path(self.tmp.name) / "material"
        self.carpeta.mkdir()
        for n, item in enumerate(self.items, 1):
            shutil.copyfile(item["ruta"], self.carpeta / f"{n:02d}.jpg")
        with open(self.carpeta / "lecturas.json", "w", encoding="utf-8") as f:
            leidas = base.estado.leer(base.fiscal._ruta_lecturas("L000123"))
            json.dump({f"{int(n):02d}.jpg": v["lectura"] for n, v in leidas["fotos"].items()}, f, ensure_ascii=False)
        self.escribir_propuesta()
        self.dicho = []
        self.tecleado = None

    def escribir_propuesta(self, **cambios):
        with open(self.carpeta / "propuesta.json", "w", encoding="utf-8") as f:
            json.dump({"archivos": ["01.jpg", "02.jpg", "03.jpg"], "comprobantes": self.rows, **cambios}, f,
                      ensure_ascii=False)

    def terminal(self, nombre="Ana Gomez"):
        prueba = self

        class Terminal(terminal.Terminal):
            def lote_local(self, consumidor, archivos):
                lote = super().lote_local(consumidor, archivos)
                prueba.lotes[lote["lote_id"]] = lote          # el lote_ver de las suites base es un diccionario
                return lote

        def teclear(_):
            return self.tecleado(self) if callable(self.tecleado) else (self.tecleado or "")
        ctx = terminal.contexto("ana.gomez", nombre, entorno={"PRINCIPAL_LOGIN": "persona_prueba",
                                                              "PRINCIPAL_KEY": "falso_principal"})
        return Terminal(ctx, entrada=teclear, salida=self.dicho.append, interactiva=True)

    def argv(self, **extra):
        a = {"--empresa": "taller", "--destino": "principal", "--carpeta": str(self.carpeta),
             "--lecturas": str(self.carpeta / "lecturas.json"), "--propuesta": str(self.carpeta / "propuesta.json"),
             **extra}
        return [x for k, v in a.items() for x in (k, v)]

    async def correr(self, nombre="Ana Gomez", banderas=(), **extra):
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            return await g._terminal_cargar(self.terminal(nombre), self.argv(**extra) + list(banderas))

    def teclear_la_huella(self):
        """La persona mira la huella que muestra la terminal y teclea sus primeros 8 caracteres."""
        def leer(prueba):
            huella = next(x for x in reversed(prueba.dicho) if "Huella: " in x).rsplit("Huella: ", 1)[1].strip()
            return huella[:8]
        self.tecleado = leer

    def lote_de_terminal(self):
        return next(i for i in self.lotes if i.startswith("L9"))

    async def test_carga_el_lote_con_el_ok_de_la_huella_y_deja_el_eml(self):
        self.teclear_la_huella()
        rc = await self.correr()
        self.assertEqual(rc, 0, "\n".join(self.dicho))
        lote_id = self.lote_de_terminal()
        self.assertTrue(900000 <= int(lote_id[1:]) <= 999999)
        self.assertEqual(len(self.facturas()), 3)
        self.assertTrue(all(f["invoice_origin"].startswith(f"[{lote_id}/") for f in self.facturas()))
        texto = "\n".join(self.dicho)
        self.assertIn("Cargué 3 en borrador; 1 quedó marcado", texto)
        self.assertNotIn("¿lo armo", texto)
        mail = email.message_from_bytes((self.carpeta / f"{lote_id}.eml").read_bytes())
        self.assertEqual((mail["To"], mail["X-Unsent"]), (presentacion.ADMIN, "1"))
        self.assertIn("Foto 3 · Café Este", mail.get_payload(decode=True).decode())
        # Sin alta en la terminal: el proveedor rechazado del chat acá va con el proveedor a regularizar.
        self.assertFalse(any(aplicar for *_, aplicar in self.corridas))
        self.assertEqual(self.factura_de_lote(lote_id, 1)["partner_id"][0], presentacion.borrador.GENERICO)

    def factura_de_lote(self, lote_id, n):
        return next(f for f in self.facturas() if f.get("invoice_origin") == f"[{lote_id}/F{n:02d}]")

    async def test_sin_la_huella_no_escribe(self):
        for tecleado in ("", "si", "00000000"):
            with self.subTest(tecleado=tecleado):
                self.tecleado = tecleado
                self.assertEqual(await self.correr(), 1)
                self.assertEqual(self.facturas(), [])
                self.assertIn("No se cargó nada.", self.dicho)

    async def test_repetir_no_duplica(self):
        self.teclear_la_huella()
        self.assertEqual(await self.correr(), 0)
        antes = copy.deepcopy(self.erps["principal"]["account.move"])
        lote_id = self.lote_de_terminal()
        self.dicho.clear()
        self.assertEqual(await self.correr(), 0, "\n".join(self.dicho))
        self.assertEqual(set(self.erps["principal"]["account.move"]), set(antes))
        self.assertIn("retomo la propuesta original", "\n".join(self.dicho))
        self.assertEqual([i for i in self.lotes if i.startswith("L9")], [lote_id])

    async def test_misma_propuesta_que_el_chat_con_las_mismas_lecturas(self):
        with self.toolbelt():
            await self.preparar(destino="principal")
        with g._contexto(self.ctx, self.args("principal")):
            del_chat = json.loads(json.dumps(g._leer("L000123")))
        self.tecleado = ""                               # sin OK: solo prepara
        self.assertEqual(await self.correr(), 1)
        lote_id = self.lote_de_terminal()
        ruta = g.estado.propuesta(lote_id, g.configuracion.seleccionar(self.raiz, self.user, self.args("principal")))
        de_la_terminal = g.estado.leer(ruta)

        def neutra(plan, lote):
            plan = {k: v for k, v in plan.items() if k not in ("revision", "reemplazado_por")}
            return json.loads(json.dumps(plan).replace(lote, "LOTE"))
        # El alta no existe en la terminal: ese comprobante difiere en el proveedor, y lo demás es igual.
        for plan in (del_chat, de_la_terminal):
            for x in plan["comprobantes"]:
                if x["n"] == 1:
                    x["fiscal"] = {k: v for k, v in x["fiscal"].items()
                                   if k not in ("alta_proveedor", "partner_id", "proveedor", "a_regularizar", "revision")}
        self.assertEqual(neutra(del_chat, "L000123"), neutra(de_la_terminal, lote_id))

    async def test_un_numero_usado_en_odoo_se_descarta(self):
        usados = []
        original = terminal.lote_local

        def con_choque(login, consumidor, archivos):
            lote = original(login, consumidor, archivos)
            if not usados:
                usados.append(lote["lote_id"])
                self.erps["principal"]["account.move"][38500] = {
                    "id": 38500, "company_id": [3, "Casa"], "invoice_origin": f"[{lote['lote_id']}/F01]",
                    "move_type": "in_invoice", "state": "posted"}
            return lote
        self.teclear_la_huella()
        with patch.object(terminal, "lote_local", side_effect=con_choque):
            self.assertEqual(await self.correr(), 0, "\n".join(self.dicho))
        self.assertIn(f"El número {usados[0]} ya tiene facturas en Odoo: tomo otro.", self.dicho)
        self.assertEqual(lotes.store().lote(usados[0])["extra"]["descartado"], True)

    async def test_la_carpeta_cambiada_no_se_prepara(self):
        (self.carpeta / "01.jpg").rename(self.carpeta / "04.jpg")
        self.assertEqual(await self.correr(), 2)
        self.assertIn("la carpeta cambió", "\n".join(self.dicho))
        self.assertEqual(self.facturas(), [])

    async def test_una_lectura_que_no_es_un_objeto(self):
        with open(self.carpeta / "lecturas.json", "w", encoding="utf-8") as f:
            json.dump({"01.jpg": []}, f)
        self.assertEqual(await self.correr(), 2)
        self.assertIn("La lectura de 01.jpg (foto 1) no es un objeto", "\n".join(self.dicho))

    async def test_sin_nombre_no_corre(self):
        self.assertEqual(await self.correr(nombre=""), 2)
        self.assertIn("Falta --nombre", "\n".join(self.dicho))

    async def test_una_foto_que_no_se_puede_adjuntar_no_escribe_nada(self):
        from bot import imagen

        async def rota(datos, **kw):
            raise imagen.ImagenError("No pude abrir esa imagen")
        self.teclear_la_huella()
        with patch("bot.imagen.normalizar", side_effect=rota):
            self.assertEqual(await self.correr(), 2)
        self.assertIn("01.jpg: No pude abrir esa imagen", "\n".join(self.dicho))
        self.assertEqual(self.facturas(), [])

    async def test_un_pdf_en_la_carpeta(self):
        (self.carpeta / "05.pdf").write_bytes(b"%PDF")
        self.assertEqual(await self.correr(), 2)
        self.assertIn("tiene un PDF", "\n".join(self.dicho))

    async def test_una_factura_ajena_con_el_mismo_marcador_no_se_adopta(self):
        original = terminal.lote_local

        def con_ajena(login, consumidor, archivos):
            lote = original(login, consumidor, archivos)
            ajena = 38600 + len(self.erps["principal"]["account.move"])
            self.erps["principal"]["account.move"][ajena] = {
                "id": ajena, "company_id": [3, "Casa"], "invoice_origin": f"[{lote['lote_id']}/F02]",
                "move_type": "in_invoice", "state": "draft"}
            return lote
        self.teclear_la_huella()
        with patch.object(terminal, "lote_local", side_effect=con_ajena), \
                patch.object(importlib.import_module(g.__name__ + ".terminal"), "_numero_libre", return_value=True):
            rc = await self.correr()
        self.assertEqual(rc, 1, "\n".join(self.dicho))
        self.assertIn("no creó esta carga", "\n".join(self.dicho))

    async def test_lote_nuevo(self):
        self.teclear_la_huella()
        self.tecleado = ""
        await self.correr()
        primero = self.lote_de_terminal()
        await self.correr(banderas=["--lote-nuevo"])
        self.assertEqual(len([i for i in self.lotes if i.startswith("L9")]), 2)
        self.assertTrue(lotes.store().lote(primero)["extra"]["descartado"])

    async def test_uso(self):
        for extra, texto in (({"--carpeta": "/no/existe"}, "no es una carpeta"),
                             ({"--empresa": "otra"}, "no está en la configuración"),
                             ({"--lecturas": "/no/existe.json"}, "No pude leer las lecturas")):
            with self.subTest(texto=texto):
                self.dicho.clear()
                self.assertEqual(await self.correr(**extra), 2)
                self.assertIn(texto, "\n".join(self.dicho))
        self.assertEqual(self.facturas(), [])


# Esta suite no repite las de sus bases: solo corre lo propio.
for _nombre in dir(presentacion.PresentacionTests):
    if _nombre.startswith("test_") and _nombre not in TerminalTests.__dict__:
        setattr(TerminalTests, _nombre, None)
