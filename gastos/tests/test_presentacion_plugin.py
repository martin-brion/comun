"""El mail que presenta un lote cargado (0.1.0-dev.20): lo arma el plugin desde el resultado y la casa dice a quién va, por
gastos.presentacion/1. El plugin no manda nada: deja el mail exacto para mail_preparar, que despacha con el OK de la persona.

Todo inventado: direcciones en example.test, CUIT de prueba con dígito verificador inválido, IDs y comercios ficticios.
"""
import hashlib
from types import SimpleNamespace as NS
from tests import test_borrador_plugin as borrador
from tests import test_fiscal_plugin as base
from bot import config, plugins

g = base.gastos
TERCERO = "30555555550"         # dígito verificador inválido: no puede ser de nadie
ADMIN, JEFE = "administracion@example.test", "responsable@example.test"


class PresentacionTests(borrador.BorradorTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pedidos = []
        self.respuesta = {"para": [ADMIN], "cc": [JEFE, ADMIN]}

        async def presentar(ctx, marco, datos):
            self.pedidos.append(datos)
            if isinstance(self.respuesta, Exception):
                raise self.respuesta
            return self.respuesta
        plugins.registrar(plugins.Plugin(nombre="presentacion_prueba", grupo="presentacion_prueba", extensiones=(
            plugins.Extension("gastos.presentacion", 1, "presentacion_prueba", NS(presentar=presentar)),)))
        self.addCleanup(plugins.quitar, "presentacion_prueba")

    def tres_comprobantes(self, **extra):
        """Dos facturas que entran limpias y una con el proveedor rechazado por el padrón, que queda marcada."""
        self.borrador(presentacion="presentacion_prueba", **extra)
        path = config.DATA_DIR / "lotes" / "ana.gomez" / "L000123" / "003_foto.jpg"
        path.write_bytes(b"photo" + bytes([3]))
        self.items.append({"tipo": "foto", "ruta": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.rows.append({"n": 3, "fecha": "2026-09-23", "importe": "121.00", "moneda": "XTS", "comercio": "Café Este",
                          "categoria": "Traslados", "pago": "company_account", "referencia": "A-125"})
        self.socio(9003, "Proveedor Tres", vat=TERCERO)
        lecturas = {1: base.lectura(), 2: base.lectura(cuit=borrador.OTRO, numero="0012-00034568"),
                    3: base.lectura(cuit=TERCERO, numero="0012-00034569")}
        base.fiscal.guardar_lecturas("L000123", {str(n): {"sha256": self.items[n - 1]["sha256"], "lectura": lec}
                                                 for n, lec in lecturas.items()})

    async def cargar_con_rechazo(self):
        with self.toolbelt():
            await self.preparar(destino="principal")
        with self.toolbelt(rc=1, crea=False, pendiente=True, error="no validó"):
            return await self.aprobar(destino="principal")

    async def test_la_presentacion_lista_las_tres_con_su_estado_y_se_arma_una_vez(self):
        self.tres_comprobantes()
        r = await self.cargar_con_rechazo()
        self.assertNotIn("is_error", r, r)
        (datos,) = self.pedidos
        self.assertEqual([(f["foto"], f["marcada"]) for f in datos["facturas"]], [(1, True), (2, False), (3, False)])
        self.assertEqual(datos["totales"], {"XTS": "363.00"})
        self.assertEqual(datos["persona"]["login"], "ana.gomez")
        texto = r["content"][0]["text"]
        self.assertIn("mail_preparar", texto)
        self.assertIn(ADMIN, r["publico"])
        p = g.presentacion
        with g._contexto(self.ctx, self.args("principal")) as c:
            guardada = g.estado.leer(p._ruta("L000123", c))["presentacion"]
        self.assertEqual(guardada["para"], [ADMIN])
        self.assertEqual(guardada["cc"], [JEFE], "la copia no repite al destinatario")
        for linea in ("Foto 1 · Café Norte", "para revisar", "Foto 2", "Foto 3 · Café Este", "Total cargado: XTS 363,00."):
            self.assertIn(linea, guardada["cuerpo"])
        self.assertIn(f"id={self.factura_de(3)['id']}&model=account.move", guardada["cuerpo"])
        # Un segundo OK devuelve lo mismo, sin volver a pedirle a la casa.
        with self.toolbelt():
            otra = await self.aprobar(destino="principal")
        self.assertEqual(len(self.pedidos), 1)
        self.assertIn(ADMIN, otra["publico"])
        # Y la tool, para cuando la persona lo pide después.
        r = await g._tool_presentacion(self.ctx, self.args("principal"))
        self.assertIn("mail_preparar", r["content"][0]["text"])
        self.assertEqual(len(self.pedidos), 1)

    async def test_sin_la_clave_no_se_ofrece_nada(self):
        self.borrador()
        await self.cargar_lote()
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        self.assertNotIn("mail_preparar", r["content"][0]["text"])
        self.assertNotIn("mail", r.get("publico", ""))
        self.assertEqual(self.pedidos, [])
        r = await g._tool_presentacion(self.ctx, self.args("principal"))
        self.assertIn("no tiene configurado", r["content"][0]["text"])

    async def falla(self, respuesta):
        self.respuesta = respuesta
        self.tres_comprobantes()
        r = await self.cargar_con_rechazo()
        self.assertNotIn("is_error", r, r)
        self.assertEqual(len(self.facturas()), 3)
        self.assertIn("No pude armar el mail que presenta el lote", r["publico"])
        self.assertNotIn("mail_preparar", r["content"][0]["text"])
        self.assertEqual(self.recibo()["estado"], "cargado")

    async def test_un_proveedor_que_falla_no_toca_la_carga(self):
        await self.falla(RuntimeError("sin red"))

    async def test_una_direccion_invalida_es_una_falla(self):
        await self.falla({"para": ["no es una dirección"]})

    async def test_una_respuesta_con_claves_de_mas_es_una_falla(self):
        await self.falla({"para": [ADMIN], "otra": 1})

    async def test_no_corresponde(self):
        self.respuesta = None
        self.tres_comprobantes()
        r = await self.cargar_con_rechazo()
        self.assertNotIn("mail", r["publico"])
        self.assertNotIn("mail_preparar", r["content"][0]["text"])

    async def test_un_lote_cortado_tambien_se_presenta_y_dice_lo_que_falta(self):
        self.tres_comprobantes()
        with self.toolbelt():
            await self.preparar(destino="principal")
        busquedas = []

        def caer(modelo, metodo, args):
            if modelo == "account.move" and metodo == "search_read" and any(
                    isinstance(t, tuple) and t[0] == "invoice_origin" for t in args[0]):
                busquedas.append(1)
                return len(busquedas) == 3
            return False
        self.caer = caer
        with self.toolbelt():
            r = await self.aprobar(destino="principal")
        datos = self.pedidos[-1]
        self.assertEqual(datos["estado"], "parcial")
        self.assertEqual([f["foto"] for f in datos["no_cargadas"]], [3])
        self.assertIn("No se cargaron (1)", datos["texto"])
        self.assertIn("quedó a medias", datos["texto"])
        self.assertIn(ADMIN, r["publico"])

    def test_la_clave_no_cambia_la_firma(self):
        antes = self.seleccion("principal")["firma"]
        self.raiz["empresas"]["taller"]["presentacion"] = "presentacion_prueba"
        self.assertEqual(self.seleccion("principal")["firma"], antes)

    def test_lo_que_devuelve_la_casa_se_valida(self):
        p = g.presentacion
        d = {"persona": {"nombre": "Ana"}, "lote_id": "L000123", "facturas": [1], "texto": "Cuerpo del plugin."}
        self.assertIsNone(p.normalizar(None, d))
        listo = p.normalizar({"para": [" a@example.test ", "A@example.test"]}, d)
        self.assertEqual((listo["para"], listo["cc"], listo["cuerpo"]), (["a@example.test"], [], "Cuerpo del plugin."))
        self.assertIn("lote L000123 (1 factura)", listo["asunto"])
        for malo in ({"para": []}, {"para": "a@example.test"}, {"para": ["a@example.test"], "asunto": "x\ny"},
                     {"para": ["a@example.test"], "cuerpo": "x" * 4001}, {"para": ["sin arroba"]}):
            with self.subTest(malo=malo), self.assertRaises(p.PresentacionError):
                p.normalizar(malo, d)


# Esta suite no repite las de sus bases: solo corre lo propio.
for _nombre in dir(borrador.BorradorTests):
    if _nombre.startswith("test_") and _nombre not in PresentacionTests.__dict__:
        setattr(PresentacionTests, _nombre, None)
