"""Factura con impuestos desde la lectura de la foto, en un ERP estándar sintético con documentos LATAM.

Todo es inventado: CUIT de prueba, IDs y comercios ficticios. El Odoo falso calcula el IVA como Odoo con redondeo global
(base de cada impuesto por la alícuota, redondeada una vez), un impuesto fijo como su monto por cada línea que lo lleva, una
línea de impuesto por impuesto con importe, y el total como neto más impuestos. Escribir el importe de una línea de impuesto
recalcula el total, como Odoo.
"""
import copy
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests import test_destinos_plugin as destinos
from tests import test_empresas_plugin as empresas
from tests import test_gastos_plugin as banco
from bot import config, lotes, odoo

gastos, estado = destinos.gastos, destinos.estado
fiscal = gastos.fiscal
CUIT = "30999999990"      # dígito verificador inválido: no puede ser de nadie
PRINCIPAL = "https://principal.example.test"


def lectura(*, total="121.00", codigo="1", letra="A", numero="0012-00034567", cuit=CUIT, moneda="XTS",
            iva=(("21", "100.00", "21.00"),), no_gravado="", exento="", percepciones=(), otros=(),
            lineas=(("2", "Combo", "100.00"),)):
    return {"fecha": "2026-09-23", "importe": total, "moneda": moneda, "comercio": "Café Norte", "referencia": numero,
            "tipo": "Factura", "categoria_sugerida": "", "pago_visible": "", "dudas": "",
            "lineas": [{"cantidad": c, "descripcion": d, "importe": i} for c, d, i in lineas],
            "fiscal": {"emisor_id_fiscal": cuit, "documento_codigo": codigo, "documento_letra": letra,
                       "documento_numero": numero, "cae": "71234567890123", "no_gravado": no_gravado, "exento": exento,
                       "iva": [{"alicuota": a, "neto": n, "importe": i} for a, n, i in iva],
                       # (descripción, importe) o (descripción, importe, tipo, jurisdicción)
                       "percepciones": [{"descripcion": p[0], "importe": p[1], "tipo": (p[2:3] or ("",))[0],
                                         "jurisdiccion": (p[3:4] or ("",))[0]} for p in percepciones],
                       "otros_tributos": [{"descripcion": d, "importe": i} for d, i in otros]}}


class FiscalTests(destinos.DestinosTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        c = self.raiz["empresas"]["taller"]
        c["rendicion"] = False
        for d in c["destinos"].values():
            d["capacidades"] = {"factura": "estandar", "rendicion": "ninguna"}
        ids = lambda e, p: {"ids": {"ensayo": e, "principal": p}}
        c["impuestos"] = {"iva": {"21": ids(821, 721), "10.5": ids(822, 722)}, "no_gravado": ids(823, 723),
                          "exento": ids(824, 724), "sin_credito": ids(825, 725),
                          "percepciones": {"iva": ids(832, 732), "iibb": {"B": ids(831, 731), "C": ids(833, 733)}}}
        c["documentos"] = {"1": {**ids(901, 801), "iva": "discriminado"}, "6": {**ids(906, 806), "iva": "incluido"}}
        self.escribir()
        erp = self.erps["principal"]
        tasa = lambda ident, nombre, monto, tipo="percent": {
            "id": ident, "name": nombre, "type_tax_use": "purchase", "company_id": [3, "Casa"], "active": True,
            "amount_type": tipo, "amount": monto, "price_include": False}
        erp["account.tax"] = {721: tasa(721, "IVA 21%", 21.0), 722: tasa(722, "IVA 10.5%", 10.5),
                              723: tasa(723, "IVA No Grav", 0.0), 724: tasa(724, "IVA Exento", 0.0, "fixed"),
                              725: tasa(725, "IVA No Corresp", 0.0),
                              # Percepciones: de monto fijo 1,00; la carga pone el importe leído en su línea de impuesto.
                              731: tasa(731, "Perc IIBB B", 1.0, "fixed"), 732: tasa(732, "Perc IVA", 1.0, "fixed"),
                              733: tasa(733, "Perc IIBB C", 1.0, "fixed")}
        erp["l10n_latam.document.type"] = {
            801: {"id": 801, "name": "FACTURAS A", "code": "1", "active": True, "internal_type": "invoice"},
            806: {"id": 806, "name": "FACTURAS B", "code": "6", "active": True, "internal_type": "invoice"}}
        erp["res.partner"] = {9001: {"id": 9001, "name": "Proveedor Uno", "vat": CUIT, "parent_id": False,
                                     "company_id": False}}
        self.desvio_iva = Decimal(0)
        self.fallar_ajuste = self.ignorar_ajuste = self.cruzar_ajuste = False
        self.rows[0]["importe"] = "121.00"
        self.guardar(lectura())

    def guardar(self, primera=None, segunda=None, lote_id="L000123", items=None):
        items = items or self.lotes[lote_id]["items"]
        fotos = {}
        for n, lec in ((1, primera), (2, segunda)):
            if lec is not None:
                fotos[str(n)] = {"sha256": items[n - 1]["sha256"], "lectura": lec}
        fiscal.guardar_lecturas(lote_id, fotos)

    async def rpc(self, ctx, modelo, metodo, args, kw, *, destino=None):
        if destino["url"] != PRINCIPAL:
            return await super().rpc(ctx, modelo, metodo, args, kw, destino=destino)
        erp = self.erps["principal"]
        if modelo in ("account.tax", "l10n_latam.document.type", "res.partner"):
            self.rpc_llamadas.append(("principal", modelo, metodo, copy.deepcopy(args)))
            tabla = erp[modelo]
            if metodo == "read":
                return copy.deepcopy([tabla[i] for i in args[0] if i in tabla])
            self.assertEqual(metodo, "search_read")
            self.assertLessEqual(kw.get("limit", 999), 20)
            return copy.deepcopy([r for r in tabla.values() if empresas.cumple(r, args[0])][:kw.get("limit", 999)])
        if modelo == "account.move" and metodo == "fields_get":
            r = await super().rpc(ctx, modelo, metodo, args, kw, destino=destino)
            r.update(l10n_latam_document_type_id={"type": "many2one"}, l10n_latam_document_number={"type": "char"},
                     amount_tax={"type": "monetary"}, narration={"type": "html"}, line_ids={"type": "one2many"},
                     date={"type": "date"})
            return r
        if modelo == "account.move.line" and metodo == "fields_get":
            r = await super().rpc(ctx, modelo, metodo, args, kw, destino=destino)
            r.update(tax_line_id={"type": "many2one"}, amount_currency={"type": "monetary"})
            return r
        if modelo == "account.move" and metodo == "write":
            self.rpc_llamadas.append(("principal", modelo, metodo, copy.deepcopy(args)))
            if self.fallar_ajuste:
                self.fallar_ajuste = False
                raise odoo.OdooError("Resultado incierto sintético")
            (ident,), valores = args
            fila = erp["account.move"][ident]
            for _, lid, cambio in valores["line_ids"]:
                self.assertIn(lid, fila["line_ids"])
                if not self.ignorar_ajuste:         # ignorar: Odoo recalcula y la percepción vuelve a N × 1,00
                    erp["account.move.line"][lid].update(cambio)
                if self.cruzar_ajuste:
                    iva = next(erp["account.move.line"][i] for i in fila["line_ids"]
                               if erp["account.move.line"][i].get("tax_line_id") and erp["account.move.line"][i]["tax_line_id"][0] == 721)
                    iva["amount_currency"] -= 1.0
                    erp["account.move.line"][lid]["amount_currency"] += 1.0
            self.totales(erp, fila)
            return True
        if modelo == "account.move" and metodo == "search_count":
            self.rpc_llamadas.append(("principal", modelo, metodo, copy.deepcopy(args)))
            return sum(1 for r in erp["account.move"].values() if empresas.cumple(r, args[0]))
        if modelo == "account.move" and metodo == "create":
            self.rpc_llamadas.append(("principal", modelo, metodo, copy.deepcopy(args)))
            return self.crear_factura(erp, copy.deepcopy(args[0]))
        return await super().rpc(ctx, modelo, metodo, args, kw, destino=destino)

    def crear_factura(self, erp, v):
        tabla = erp["account.move"]
        ident = 40000 + len(tabla)
        bases, cuantas, neto, lids = {}, {}, Decimal(0), []
        for i, (_, _, linea) in enumerate(v.pop("invoice_line_ids")):
            lid = ident * 100 + i
            impuestos = list(linea["tax_ids"][0][2])
            erp["account.move.line"][lid] = {"id": lid, "product_id": False, "discount": 0, "display_type": "product",
                                             **linea, "tax_ids": impuestos, "account_id": [linea["account_id"], "Cuenta"],
                                             "product_id": [linea["product_id"], "Producto"] if linea.get("product_id") else False}
            precio = Decimal(str(linea["price_unit"]))
            neto += precio
            for t in impuestos:
                bases[t] = bases.get(t, Decimal(0)) + precio
                cuantas[t] = cuantas.get(t, 0) + 1
            lids.append(lid)
        montos = {}
        for t, b in bases.items():
            tasa = erp["account.tax"][t]
            montos[t] = ((Decimal(str(tasa["amount"])) * b / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
                         if tasa["amount_type"] == "percent" else Decimal(str(tasa["amount"])) * cuantas[t])
        primero = next((t for t, m in montos.items() if m and erp["account.tax"][t]["amount_type"] == "percent"), None)
        if primero:
            montos[primero] += self.desvio_iva
        de_impuesto = []
        for t, m in montos.items():
            if m:
                lid = ident * 100 + 50 + len(de_impuesto)
                erp["account.move.line"][lid] = {"id": lid, "display_type": "tax", "tax_line_id": [t, "Impuesto"],
                                                 "amount_currency": float(m)}
                de_impuesto.append(lid)
        pago = ident * 100 + 99
        erp["account.move.line"][pago] = {"id": pago, "display_type": "payment_term", "tax_line_id": False,
                                          "amount_currency": 0.0}
        fila = {"state": "draft", "ref": False, "auto_post": "no", **v, "id": ident, "create_uid": [erp["uid"], "Persona"],
                "invoice_line_ids": lids, "line_ids": lids + de_impuesto + [pago], "amount_untaxed": float(neto)}
        self.totales(erp, fila)
        for k in ("company_id", "currency_id", "journal_id", "partner_id", "l10n_latam_document_type_id"):
            if fila.get(k):
                fila[k] = [fila[k], "Prueba"]
        if self.inyectar:
            campo, valor = self.inyectar
            if campo.startswith("linea."):
                erp["account.move.line"][lids[0]][campo[6:]] = valor
            else:
                fila[campo] = valor
        tabla[ident] = fila
        return ident

    def totales(self, erp, fila):
        """Como Odoo: el impuesto es la suma de las líneas de impuesto, y el total y el vencimiento se recalculan."""
        lineas = [erp["account.move.line"][i] for i in fila["line_ids"]]
        impuesto = sum((Decimal(str(l["amount_currency"])) for l in lineas if l["display_type"] == "tax"), Decimal(0))
        total = Decimal(str(fila["amount_untaxed"])) + impuesto
        fila.update(amount_tax=float(impuesto), amount_total=float(total))
        next(l for l in lineas if l["display_type"] == "payment_term")["amount_currency"] = float(-total)

    def de_impuesto(self, factura):
        """{impuesto: importe} de las líneas de impuesto de la factura."""
        tabla = self.erps["principal"]["account.move.line"]
        return {tabla[i]["tax_line_id"][0]: tabla[i]["amount_currency"] for i in factura["line_ids"]
                if tabla[i]["display_type"] == "tax"}

    def repetir_lote(self, ident):
        """Otro lote con las MISMAS fotos (mismos bytes): la persona las reenvió."""
        lote = copy.deepcopy(self.lote); lote["lote_id"] = ident
        for item in lote["items"]:
            original = Path(item["ruta"]); nuevo = original.parent.parent / ident / original.name
            nuevo.parent.mkdir(parents=True, exist_ok=True); nuevo.write_bytes(original.read_bytes())
            item["ruta"] = str(nuevo)
        self.lotes[ident] = lote
        return lote

    def facturas(self):
        return sorted(self.erps["principal"]["account.move"].values(), key=lambda f: f["id"])

    def lineas(self, factura):
        return [self.erps["principal"]["account.move.line"][i] for i in factura["invoice_line_ids"]]

    async def cargar(self):
        p = await self.preparar(destino="principal")
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        return p, self.facturas()

    async def test_factura_a_con_iva_por_alicuota_suma_el_total(self):
        p, (una, otra) = await self.cargar()
        self.assertEqual((una["partner_id"][0], una["l10n_latam_document_type_id"][0], una["l10n_latam_document_number"]),
                         (9001, 801, "00012-00034567"))
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(100.0, [721])])
        self.assertEqual((una["amount_tax"], una["amount_total"]), (21.0, 121.0))
        self.assertIn("2 × Combo", self.lineas(una)[0]["name"])
        self.assertIn("CAE 71234567890123", una["narration"])
        # La foto 2 no tiene lectura: va sin impuestos, como antes.
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(otra)], [(22.0, [])])
        self.assertFalse(otra.get("partner_id") or otra.get("l10n_latam_document_type_id"))
        vista = p["propuesta"]["comprobantes"]
        self.assertEqual(vista[0]["factura"]["impuestos"], "sí")
        self.assertEqual(vista[1]["factura"]["impuestos"], "no")

    async def test_cada_linea_lleva_el_producto_de_su_categoria(self):
        _, (una, otra) = await self.cargar()
        with gastos._contexto(self.ctx, self.args(destino="principal")):
            productos = {x["product_id"] for x in gastos._leer("L000123")["comprobantes"]}   # el de su categoria
        self.assertTrue(all(productos))
        self.assertEqual({l["product_id"][0] for f in (una, otra) for l in self.lineas(f)}, productos)
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(100.0, [721])])

    async def test_factura_de_antes_sin_producto_se_retoma(self):
        # Cargada antes de la dev.13 (sin producto) y cortada antes de terminar: el reintento la verifica y sigue.
        await self.preparar(destino="principal")
        self.adjunto_confirmado_error = True
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        for f in self.facturas():
            for linea in self.lineas(f):
                linea["product_id"] = False
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        self.assertEqual(len(self.facturas()), 2)

    async def test_dos_alicuotas_dos_lineas(self):
        self.rows[0]["importe"] = "176.25"
        self.guardar(lectura(total="176.25", iva=(("21", "100.00", "21.00"), ("10.5", "50.00", "5.25"))))
        _, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(100.0, [721]), (50.0, [722])])
        self.assertEqual((una["amount_tax"], una["amount_total"]), (26.25, 176.25))

    async def test_no_gravado_va_en_su_linea(self):
        self.rows[0]["importe"] = "131.00"
        self.guardar(lectura(total="131.00", no_gravado="10.00"))
        _, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(100.0, [721]), (10.0, [723])])
        self.assertEqual(una["amount_total"], 131.0)

    async def test_factura_b_una_linea_iva_no_corresponde(self):
        self.rows[0]["importe"] = "22.00"
        self.guardar(lectura(total="22.00", codigo="6", letra="B", iva=()))
        _, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(22.0, [725])])
        self.assertEqual((una["amount_tax"], una["l10n_latam_document_type_id"][0]), (0.0, 806))

    async def test_percepcion_va_en_linea_aparte_con_la_imputacion(self):
        self.rows[0]["importe"] = "123.45"
        self.guardar(lectura(total="123.45", percepciones=(("Percepción IIBB CABA", "2.45"),)))
        p, (una, _) = await self.cargar()
        lineas = self.lineas(una)
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in lineas], [(100.0, [721]), (2.45, [])])
        self.assertIn("Percepción IIBB CABA: pasar a impuesto", lineas[1]["name"])
        self.assertEqual(una["amount_total"], 123.45)
        tareas = p["propuesta"]["comprobantes"][0]["factura"]["completa_administracion"]
        self.assertTrue(any("percepción" in t for t in tareas), tareas)

    async def test_impuesto_interno_va_en_su_linea_con_no_gravado(self):
        # Ninguna línea sin IVA: la localización no deja publicar una. El otro tributo va con el no gravado.
        self.rows[0]["importe"] = "126.00"
        self.guardar(lectura(total="126.00", otros=(("Impuesto interno", "5.00"),)))
        _, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(100.0, [721]), (5.0, [723])])
        self.assertIn("Impuesto interno: revisar", self.lineas(una)[1]["name"])

    async def test_otro_tributo_en_factura_b_va_con_sin_credito(self):
        self.rows[0]["importe"] = "27.00"
        self.guardar(lectura(total="27.00", codigo="6", letra="B", iva=(), otros=(("Tasa vial", "5.00"),)))
        _, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(22.0, [725]), (5.0, [725])])

    async def test_otro_tributo_sin_no_gravado_configurado_va_sin_impuestos(self):
        del self.raiz["empresas"]["taller"]["impuestos"]["no_gravado"]
        self.escribir()
        await self.sin_impuestos(lectura(total="126.00", otros=(("Impuesto interno", "5.00"),)), "126.00",
                                 "otros tributos y no hay impuesto no gravado")

    async def test_todas_las_lineas_llevan_la_imputacion(self):
        self.rows[0]["importe"] = "138.00"
        self.guardar(lectura(total="138.00", no_gravado="5.00", exento="3.00", percepciones=(("Percepción IVA", "4.00"),),
                             otros=(("Impuesto interno", "5.00"),)))
        _, (una, _) = await self.cargar()
        lineas = self.lineas(una)
        self.assertEqual(len(lineas), 5)
        self.assertTrue(all(l["analytic_distribution"] == {"41": 100} and l["account_id"][0] == 701 for l in lineas))

    async def test_suma_con_percepcion_da_el_total(self):
        f = fiscal.armar(fiscal.normalizar(lectura(total="123.45", percepciones=(("Percepción", "2.45"),))),
                         {"importe": "123.45", "moneda": "XTS", "comercio": "Café", "marker": "[L000123/F01]"},
                         self.seleccion(destino="principal"))
        self.assertEqual(f["modo"], fiscal.CON_IMPUESTOS)
        self.assertEqual(sum(Decimal(l["importe"]) for l in f["lineas"]) + Decimal(f["iva"]), Decimal("123.45"))

    async def sin_impuestos(self, lec, importe, motivo):
        self.rows[0]["importe"] = importe
        self.guardar(lec)
        p, (una, _) = await self.cargar()
        factura = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertEqual(factura["impuestos"], "no")
        self.assertRegex(factura["motivo"], motivo)
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(float(importe), [])])
        self.assertFalse(una.get("partner_id") or una.get("l10n_latam_document_type_id") or
                         una.get("l10n_latam_document_number"))
        self.assertIn("Lectura del bot", una["narration"])

    async def test_iva_que_no_cuadra_por_mas_de_un_peso_queda_como_hoy(self):
        await self.sin_impuestos(lectura(total="122.50", iva=(("21", "100.00", "22.50"),)), "122.50", "IVA 21 %")

    async def test_partes_que_no_suman_el_total_por_mas_de_un_peso_quedan_como_hoy(self):
        await self.sin_impuestos(lectura(total="121.00", iva=(("21", "102.00", "21.42"),)), "121.00", "suman 123.42")

    async def test_iva_impreso_con_redondeo_se_carga_con_el_impreso(self):
        # El comercio imprime el IVA como el total menos el neto: un centavo menos que el neto por la alícuota.
        self.rows[0]["importe"] = "50000.00"
        self.guardar(lectura(total="50000.00", iva=(("21", "41322.32", "8677.68"),)))
        p, (una, _) = await self.cargar()
        self.assertEqual(p["propuesta"]["comprobantes"][0]["factura"]["impuestos"], "sí")
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(41322.32, [721])])
        self.assertEqual((una["amount_tax"], una["amount_total"]), (8677.68, 50000.0))
        self.assertEqual(self.de_impuesto(una), {721: 8677.68})
        self.assertIn("Redondeo del comprobante: el IVA 21 % impreso es 8677.68 y el neto por la alícuota da 8677.69",
                      una["narration"])

    async def test_tres_decimales_y_redondeo_de_la_suma_van_al_neto(self):
        # Importes impresos con tres decimales: redondeados por separado suman un centavo más que el total.
        self.rows[0]["importe"] = "200941.15"
        self.guardar(lectura(total="200941.15", iva=(("21", "156985.276", "32966.908"),),
                             percepciones=(("Perc.IIBB Bs.As.", "6279.41", "iibb", "B"), ("Perc.IVA 3%", "4709.56", "iva"))))
        _, (una, _) = await self.cargar()
        self.assertEqual([l["price_unit"] for l in self.lineas(una)], [156985.27])
        self.assertEqual(self.de_impuesto(una), {721: 32966.91, 731: 6279.41, 732: 4709.56})
        self.assertEqual(una["amount_total"], 200941.15)
        self.assertIn("las partes impresas suman 200941.16 y el total es 200941.15: la diferencia va al neto",
                      una["narration"])

    async def test_la_correccion_de_la_persona_manda_sobre_la_lectura(self):
        self.guardar(lectura(numero="0012-00034999"))
        for mala, texto in (({"receptor_id_fiscal": "1"}, "lleva solo"), ({"emisor_id_fiscal": "ab"}, "sus dígitos"),
                            ({"documento_numero": "-"}, "no es un número"), ("0012", "lleva solo")):
            with self.subTest(mala=mala):
                with self.assertRaisesRegex(gastos.GastoError, texto):
                    await gastos.preparar(self.ctx, {**self.args(destino="principal"), "comprobantes": [
                        {**self.rows[0], "correccion": mala}, self.rows[1]]})
        p = await gastos.preparar(self.ctx, {**self.args(destino="principal"), "comprobantes": [
            {**self.rows[0], "correccion": {"documento_numero": "0012-00034567"}}, self.rows[1]]})
        self.assertIn("0012-00034567", p["propuesta"]["comprobantes"][0]["factura"]["documento"])
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        una = self.facturas()[0]
        self.assertEqual(una["l10n_latam_document_number"], "00012-00034567")
        self.assertIn("Corregido por la persona sobre lo leído: número 0012-00034567 (se leyó 0012-00034999).",
                      una["narration"])

    async def test_la_carga_devuelve_una_linea_por_factura_con_su_link(self):
        await self.preparar(destino="principal")
        r = await self.aprobar(destino="principal")
        texto = r["content"][0]["text"]
        una, otra = self.facturas()
        self.assertTrue(texto.startswith("Quedaron 2 facturas en borrador; las revisa y confirma Administración:"), texto)
        self.assertIn(f"• FACTURAS A 0012-00034567 · Proveedor Uno · XTS 121,00 → "
                      f"{PRINCIPAL}/web#id={una['id']}&model=account.move&view_type=form", texto)
        self.assertIn(f"• ⚠️ Café Sur · XTS 22,00, sin IVA desglosado (no hay lectura automática del comprobante): la "
                      f"completa Administración → {PRINCIPAL}/web#id={otra['id']}&model=account.move&view_type=form", texto)
        self.assertNotIn("{", texto)
        self.assertEqual((una["date"], otra["date"]), (una["invoice_date"], otra["invoice_date"]))
        with self.assertRaisesRegex(gastos.GastoError, "El lote L000123 ya está cargado en Odoo"):
            await self.preparar(destino="principal")
        # El agente de la sala no ve el OK aplicado fuera de su turno: el cierre del lote siguiente le dice qué ya se cargó.
        self.assertEqual(estado.cargados(["L000124", "L000123", "Lmalo"]), ["L000123"])
        sala = [{"lote_id": n, "consumidor": "gastos", "login": "ana"} for n in ("L000124", "L000123")]
        with patch.object(lotes, "store", return_value=SimpleNamespace(por_room=lambda *_: sala)):
            self.assertEqual(gastos._cargados_de_la_sala({"lote_id": "L000124", "chat_id": "!s", "login": "ana"}),
                             ["L000123"])

    async def test_alicuota_o_tipo_sin_configurar_queda_como_hoy(self):
        await self.sin_impuestos(lectura(codigo="11", letra="C", iva=()), "121.00", "tipo de comprobante 11")

    async def test_alicuota_sin_configurar_queda_como_hoy(self):
        await self.sin_impuestos(lectura(total="127.00", iva=(("27", "100.00", "27.00"),)), "127.00", "alícuota 27")

    async def test_numeros_fiscales_vienen_de_la_lectura(self):
        # El agente confirma otro total y manda campos fiscales: no cuentan, y la factura va sin impuestos.
        self.rows[0]["fiscal"] = {"iva": [{"alicuota": "21", "neto": "100", "importe": "21"}]}
        await self.sin_impuestos(lectura(), "120.00", "total confirmado")

    async def test_proveedor_por_cuit_unico_o_vacio(self):
        p, (una, _) = await self.cargar()
        self.assertEqual(una["partner_id"][0], 9001)
        self.assertNotIn("aviso_proveedor", p["propuesta"]["comprobantes"][0]["factura"])
        self.assertFalse(any(x[1:3] == ("account.move", "search_count") for x in self.rpc_llamadas))
        lote = self.crear_lote("L000130")
        self.guardar(lectura(numero="0012-00007770"), lote_id=lote["lote_id"], items=lote["items"])
        self.erps["principal"]["res.partner"] = {}
        p = await self.preparar(destino="principal", lote_id=lote["lote_id"])
        tareas = p["propuesta"]["comprobantes"][0]["factura"]["completa_administracion"]
        self.assertTrue(any("Dar de alta al proveedor" in t for t in tareas), tareas)

    def socio(self, ident, nombre, vat=CUIT, **otros):
        self.erps["principal"]["res.partner"][ident] = {"id": ident, "name": nombre, "vat": vat, "parent_id": False,
                                                        "company_id": False, **otros}

    def publicada(self, socio, compania=3, state="posted", tipo="in_invoice"):
        tabla = self.erps["principal"]["account.move"]
        ident = 38000 + len(tabla)
        tabla[ident] = {"id": ident, "name": f"FA-A {ident}", "company_id": [compania, "Casa"], "move_type": tipo,
                        "state": state, "partner_id": [socio, "Socio"], "commercial_partner_id": [socio, "Socio"]}

    async def test_cuit_repetido_usa_el_de_mas_facturas_publicadas(self):
        self.socio(9002, "Proveedor Dos", vat="30-99999999-0")
        self.socio(9003, "Contacto de Dos", parent_id=[9002, "Proveedor Dos"])      # contacto: no es proveedor
        self.socio(9004, "De otra compañía", company_id=[99, "Otra"])
        self.publicada(9002); self.publicada(9002); self.publicada(9001)
        # No cuentan: borrador, nota de crédito ni otra compañía.
        self.publicada(9001, state="draft"); self.publicada(9001, tipo="in_refund"); self.publicada(9001, compania=99)
        self.publicada(9001, compania=99)
        p = await self.preparar(destino="principal")
        vista = p["propuesta"]["comprobantes"][0]["factura"]
        aviso = ("Había 2 proveedores con el CUIT/RUT 30999999990; usé Proveedor Dos, id 9002, el que tiene más facturas "
                 "de proveedor publicadas (2).")
        self.assertEqual((vista["proveedor"], vista["aviso_proveedor"]), ("Proveedor Dos", aviso))
        self.assertTrue(any("Confirmar el proveedor" in t for t in vista["completa_administracion"]), vista)
        # Una factura publicada entre la propuesta y el OK no cambia la elección ni frena la carga.
        self.publicada(9001); self.publicada(9001)
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        una = next(f for f in self.facturas() if f["id"] >= 40000)
        self.assertEqual(una["partner_id"][0], 9002)
        self.assertIn(aviso, una["narration"])
        tareas = estado.recibo("L000123", self.seleccion(destino="principal"))["resultado"]["pendiente_administracion"]["1"]
        self.assertTrue(any("Confirmar el proveedor" in t for t in tareas), tareas)

    async def test_cuit_repetido_con_empate_usa_el_de_id_menor(self):
        self.socio(9000, "Proveedor Cero", vat="30-99999999-0")
        self.publicada(9000); self.publicada(9001)
        p = await self.preparar(destino="principal")
        vista = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertEqual(vista["proveedor"], "Proveedor Cero")
        self.assertIn("usé Proveedor Cero, id 9000, el de ID menor entre los que tienen 1 factura de proveedor publicada.",
                      vista["aviso_proveedor"])
        # Si el elegido deja de estar entre los del CUIT, el OK frena: hay que preparar otra propuesta.
        self.erps["principal"]["res.partner"][9000]["vat"] = "20999999990"
        r = await self.aprobar(destino="principal")
        self.assertTrue(r["is_error"])
        self.assertIn("cambió el proveedor", r["content"][0]["text"])
        self.assertFalse(any(x[1:3] == ("account.move", "create") for x in self.rpc_llamadas))

    async def test_comprobante_ya_cargado_no_se_duplica(self):
        erp = self.erps["principal"]
        previa = {"id": 39000, "name": "FA-A 00012-00034567", "state": "draft", "company_id": [3, "Casa"],
                  "move_type": "in_invoice", "partner_id": [9001, "Proveedor Uno"], "invoice_origin": False,
                  "l10n_latam_document_type_id": [801, "FACTURAS A"], "l10n_latam_document_number": "00012-00034567"}
        erp["account.move"][39000] = dict(previa)
        with self.assertRaisesRegex(gastos.GastoError, r"ya está en Odoo \(factura #39000"):
            await self.preparar(destino="principal")
        # Cancelada no cuenta; si aparece entre la propuesta y el OK, la carga frena sin crear nada.
        erp["account.move"][39000]["state"] = "cancel"
        await self.preparar(destino="principal")
        erp["account.move"][39000]["state"] = "draft"
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        self.assertFalse(any(x[1:3] == ("account.move", "create") for x in self.rpc_llamadas if x[0] == "principal"))

    async def test_odoo_calcula_un_redondeo_distinto_y_queda_el_impreso(self):
        await self.preparar(destino="principal")
        self.desvio_iva = Decimal("0.01")
        self.assertNotIn("is_error", await self.aprobar(destino="principal"))
        una = self.facturas()[0]
        self.assertEqual((self.de_impuesto(una), una["amount_total"]), ({721: 21.0}, 121.0))

    async def test_odoo_calcula_distinto_frena_sin_duplicar(self):
        await self.preparar(destino="principal")
        self.desvio_iva = Decimal("5.00")
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        self.assertEqual(len(self.facturas()), 1)

    async def test_impuesto_cambiado_frena_la_verificacion(self):
        await self.preparar(destino="principal")
        self.inyectar = ("linea.tax_ids", [722])
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        self.assertEqual(len(self.facturas()), 1)

    async def test_impuesto_configurado_invalido_es_error_de_configuracion(self):
        original = copy.deepcopy(self.erps["principal"]["account.tax"][721])
        for campo, valor in (("amount", 10.5), ("type_tax_use", "sale"), ("company_id", [99, "Otra"]),
                             ("price_include", True), ("active", False)):
            with self.subTest(campo=campo):
                self.erps["principal"]["account.tax"][721] = {**original, campo: valor}
                with self.assertRaisesRegex(gastos.GastoError, r"impuesto 721.*gastos\.json"):
                    await self.preparar(destino="principal")
        self.erps["principal"]["account.tax"][721] = original
        self.erps["principal"]["l10n_latam.document.type"][801]["code"] = "6"
        with self.assertRaisesRegex(gastos.GastoError, r"tipo de documento 801.*gastos\.json"):
            await self.preparar(destino="principal")
        self.sin_escrituras()

    async def test_propuesta_muestra_desglose_y_quien_completa(self):
        self.rows[0]["importe"] = "123.45"
        self.guardar(lectura(total="123.45", percepciones=(("Percepción IIBB CABA", "2.45"),)))
        p = await self.preparar(destino="principal")
        con, sin = (x["factura"] for x in p["propuesta"]["comprobantes"])
        self.assertEqual((con["documento"], con["proveedor"], con["iva"]), ("FACTURAS A 0012-00034567", "Proveedor Uno", "21.00"))
        self.assertEqual([l["importe"] for l in con["lineas"]], ["100.00", "2.45"])
        self.assertIn("Revisar contra la foto y confirmar.", con["completa_administracion"])
        self.assertIn("lectura automática", sin["motivo"])
        self.assertTrue(any("desglose leído" in t for t in sin["completa_administracion"]))
        self.assertIn("Administración", p["aviso"])
        _, tarjeta = await gastos._resumen_cargar(self.ctx, self.args(destino="principal"), False)
        self.assertIn("Factura: FACTURAS A 0012-00034567 · Proveedor Uno · IVA 21 % 100.00 + Percepción IIBB CABA: "
                      "pasar a impuesto 2.45 · IVA 21.00", tarjeta)
        self.assertIn("Factura: sin impuestos", tarjeta)

    # ------------------------------------------------------------------------------- percepciones como impuesto
    async def test_percepcion_con_impuesto_va_como_impuesto_con_el_importe_leido(self):
        self.rows[0]["importe"] = "145.00"
        self.guardar(lectura(total="145.00", no_gravado="10.00",
                             percepciones=(("Percepción IIBB Prov. B", "6.00", "IIBB", "b"), ("Percepción IVA", "8.00", "iva"))))
        p, (una, _) = await self.cargar()
        # El impuesto de cada percepción va en todas las líneas; Odoo arma su línea con 2 × 1,00 y la carga le pone lo leído.
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)],
                         [(100.0, [721, 731, 732]), (10.0, [723, 731, 732])])
        self.assertEqual(self.de_impuesto(una), {721: 21.0, 731: 6.0, 732: 8.0})
        self.assertEqual((una["amount_tax"], una["amount_total"]), (35.0, 145.0))
        ajustes = [x for x in self.rpc_llamadas if x[1:3] == ("account.move", "write")]
        self.assertEqual(len(ajustes), 1)
        self.assertEqual(sorted(c[2]["amount_currency"] for c in ajustes[0][3][1]["line_ids"]), [6.0, 8.0])
        vista = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertEqual(vista["percepciones"], [{"texto": "Percepción IIBB Prov. B", "importe": "6.00"},
                                                 {"texto": "Percepción IVA", "importe": "8.00"}])
        self.assertTrue(any("Odoo las recalcula" in t for t in vista["completa_administracion"]), vista)
        self.assertFalse(any("borrar su línea" in t for t in vista["completa_administracion"]), vista)
        _, tarjeta = await gastos._resumen_cargar(self.ctx, self.args(destino="principal"), False)
        self.assertIn("· IVA 21.00 · Percepción IIBB Prov. B 6.00 · Percepción IVA 8.00", tarjeta)

    async def test_percepcion_sin_impuesto_configurado_queda_en_linea_aparte(self):
        # Sin jurisdicción leída, o con una que no está configurada: no se infiere, va como antes (Odoo no la deja publicar).
        self.rows[0]["importe"] = "130.00"
        self.guardar(lectura(total="130.00", percepciones=(("Percepción IIBB", "4.00", "iibb"),
                                                           ("Percepción IIBB Prov. X", "5.00", "iibb", "X"))))
        p, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)],
                         [(100.0, [721]), (4.0, []), (5.0, [])])
        self.assertEqual(self.de_impuesto(una), {721: 21.0})
        self.assertFalse(any(x[1:3] == ("account.move", "write") for x in self.rpc_llamadas))
        self.assertNotIn("percepciones", p["propuesta"]["comprobantes"][0]["factura"])

    async def test_dos_percepciones_con_el_mismo_impuesto_van_en_una_linea(self):
        self.rows[0]["importe"] = "131.00"
        self.guardar(lectura(total="131.00", percepciones=(("Perc. AGIP", "4.00", "iibb", "C"),
                                                           ("Perc. AGIP RG", "6.00", "iibb", "C"))))
        p, (una, _) = await self.cargar()
        self.assertEqual(self.de_impuesto(una), {721: 21.0, 733: 10.0})
        self.assertEqual(p["propuesta"]["comprobantes"][0]["factura"]["percepciones"],
                         [{"texto": "Perc. AGIP + Perc. AGIP RG", "importe": "10.00"}])

    async def test_corte_antes_del_ajuste_lo_reintenta_sin_duplicar(self):
        self.rows[0]["importe"] = "127.00"
        self.guardar(lectura(total="127.00", percepciones=(("Perc. ARBA", "6.00", "iibb", "B"),)))
        await self.preparar(destino="principal")
        self.fallar_ajuste = True
        self.assertTrue((await self.aprobar(destino="principal"))["is_error"])
        r = await self.aprobar(destino="principal")
        self.assertNotIn("is_error", r, r)
        una = next(f for f in self.facturas() if f["id"] >= 40000)
        self.assertEqual((self.de_impuesto(una), una["amount_total"]), ({721: 21.0, 731: 6.0}, 127.0))
        self.assertEqual(sum(1 for x in self.rpc_llamadas if x[1:3] == ("account.move", "create")), 2)   # 2 fotos

    async def test_odoo_que_no_toma_el_importe_de_la_percepcion_frena(self):
        self.rows[0]["importe"] = "127.00"
        self.guardar(lectura(total="127.00", percepciones=(("Perc. ARBA", "6.00", "iibb", "B"),)))
        await self.preparar(destino="principal")
        self.ignorar_ajuste = True
        for _ in range(2):      # el reintento no vuelve a escribir ni crea otra factura
            r = await self.aprobar(destino="principal")
            self.assertTrue(r["is_error"])
        self.assertIn("no coincide con el comprobante", r["content"][0]["text"])       # el total ya no es el leído
        self.assertEqual(sum(1 for x in self.rpc_llamadas if x[1:3] == ("account.move", "write")), 1)
        self.assertEqual(len([f for f in self.facturas() if f["id"] >= 40000]), 1)

    async def test_linea_de_impuesto_con_otro_importe_frena_aunque_el_total_cuadre(self):
        self.rows[0]["importe"] = "127.00"
        self.guardar(lectura(total="127.00", percepciones=(("Perc. ARBA", "6.00", "iibb", "B"),)))
        await self.preparar(destino="principal")
        self.cruzar_ajuste = True          # 1,00 del IVA pasa a la percepción: el total y el impuesto total no cambian
        r = await self.aprobar(destino="principal")
        self.assertTrue(r["is_error"])
        self.assertIn("líneas de impuesto de la factura", r["content"][0]["text"])
        una = next(f for f in self.facturas() if f["id"] >= 40000)
        self.assertEqual((una["amount_tax"], una["amount_total"]), (27.0, 127.0))

    async def test_percepcion_con_impuesto_que_no_es_fijo_es_error_de_configuracion(self):
        self.guardar(lectura(total="127.00", percepciones=(("Perc. ARBA", "6.00", "iibb", "B"),)))
        self.rows[0]["importe"] = "127.00"
        self.erps["principal"]["account.tax"][731].update(amount_type="percent", amount=3.0)
        with self.assertRaisesRegex(gastos.GastoError, r"impuesto 731 configurado como «percepción iibb\.B».*monto fijo"):
            await self.preparar(destino="principal")
        self.sin_escrituras()

    async def test_configuracion_de_percepciones(self):
        ids = lambda e, p: {"ids": {"ensayo": e, "principal": p}}
        for malo in ({"IVA": ids(832, 732)}, {"iibb": {"b": ids(831, 731)}}, {"iibb": {"B": {"ids": {"principal": 731}}}},
                     {"iva": ids(821, 721)}, {"iibb": {}}, [], {"iva": {**ids(832, 732), "B": ids(831, 731)}}):
            with self.subTest(percepciones=malo):
                raiz = copy.deepcopy(self.raiz)
                raiz["empresas"]["taller"]["impuestos"]["percepciones"] = malo
                with self.assertRaisesRegex(gastos.configuracion.ConfigError, r"^Configuración del bot.*percepciones"):
                    gastos.configuracion.validar(raiz)
        imp = self.seleccion(destino="principal")["impuestos"]
        self.assertEqual(imp["percepciones"], {"iva": 732, "iibb": {"B": 731, "C": 733}})

    # ------------------------------------------------------------------------------- cargar_como
    def cargar_como(self, **documentos):
        self.raiz["empresas"]["taller"]["documentos"].update(documentos)
        self.escribir()

    async def test_cargar_como_carga_con_el_tipo_de_destino(self):
        tique = {**lectura(codigo="81"), "tipo": "Tique Factura A"}
        # Con su propio ID apuntando al tipo 1, Odoo dice que el tipo no es el código 81: está bien que frene.
        self.cargar_como(**{"81": {"ids": {"ensayo": 901, "principal": 801}, "iva": "discriminado"}})
        self.guardar(tique)
        with self.assertRaisesRegex(gastos.GastoError, r"tipo de documento 801 configurado para el código 81"):
            await self.preparar(destino="principal")
        self.cargar_como(**{"81": {"cargar_como": "1"}})
        p, (una, _) = await self.cargar()
        self.assertEqual((una["l10n_latam_document_type_id"][0], una["l10n_latam_document_number"]), (801, "00012-00034567"))
        vista = p["propuesta"]["comprobantes"][0]["factura"]
        self.assertEqual(vista["documento"], "Tique Factura A (código 81), se carga como FACTURAS A 0012-00034567")
        self.assertIn("El comprobante leído es de tipo 81: por la configuración del bot se carga con el tipo 1 (FACTURAS A).",
                      una["narration"])
        _, tarjeta = await gastos._resumen_cargar(self.ctx, self.args(destino="principal"), False)
        self.assertIn("Factura: Tique Factura A (código 81), se carga como FACTURAS A 0012-00034567 · Proveedor Uno", tarjeta)

    async def test_cargar_como_hereda_el_iva_del_tipo_de_destino(self):
        self.cargar_como(**{"82": {"cargar_como": "6"}})
        self.rows[0]["importe"] = "22.00"
        self.guardar(lectura(total="22.00", codigo="82", letra="B", iva=()))
        _, (una, _) = await self.cargar()
        self.assertEqual([(l["price_unit"], l["tax_ids"]) for l in self.lineas(una)], [(22.0, [725])])
        self.assertEqual(una["l10n_latam_document_type_id"][0], 806)

    async def test_cargar_como_detecta_el_duplicado_con_el_tipo_de_carga(self):
        # El tique ya se cargó como factura A (a mano, o por otro lote): se busca con el tipo de carga, no con el leído.
        self.cargar_como(**{"81": {"cargar_como": "1"}})
        self.erps["principal"]["account.move"][39000] = {
            "id": 39000, "name": "FA-A 00012-00034567", "state": "posted", "company_id": [3, "Casa"],
            "move_type": "in_invoice", "partner_id": [9001, "Proveedor Uno"], "invoice_origin": False,
            "l10n_latam_document_type_id": [801, "FACTURAS A"], "l10n_latam_document_number": "00012-00034567"}
        for total in ("121.00", "120.00"):          # con impuestos y sin impuestos (el total no cuadra)
            with self.subTest(total=total):
                self.rows[0]["importe"] = total
                self.guardar({**lectura(codigo="81"), "tipo": "Tique Factura A"})
                with self.assertRaisesRegex(gastos.GastoError, r"ya está en Odoo \(factura #39000"):
                    await self.preparar(destino="principal")
        self.sin_escrituras()

    async def test_cargar_como_valida_la_configuracion(self):
        casos = (({"81": {"cargar_como": "2"}}, "tiene que ser otro código"),
                 ({"81": {"cargar_como": "1"}, "82": {"cargar_como": "81"}}, "también es un cargar_como"),
                 ({"81": {"cargar_como": "81"}}, "también es un cargar_como"),
                 ({"81": {"cargar_como": 1}}, "tiene que ser otro código"),
                 ({"81": {"cargar_como": "1", "iva": "discriminado"}}, "documentos.81"))
        for documentos, error in casos:
            with self.subTest(documentos=documentos):
                raiz = copy.deepcopy(self.raiz)
                raiz["empresas"]["taller"]["documentos"].update(documentos)
                with self.assertRaisesRegex(gastos.configuracion.ConfigError, rf"^Configuración del bot.*{error}"):
                    gastos.configuracion.validar(raiz)
        # Sin la clave, los documentos quedan como antes; con ella, el alias lleva solo su código de carga.
        antes = self.seleccion(destino="principal")
        self.assertEqual(antes["documentos"], {"1": {"id": 801, "iva": "discriminado"}, "6": {"id": 806, "iva": "incluido"}})
        self.cargar_como(**{"81": {"cargar_como": "1"}})
        despues = self.seleccion(destino="principal")
        self.assertEqual(despues["documentos"]["81"], {"cargar_como": "1"})
        self.assertNotEqual(antes["firma"], despues["firma"])

    # ------------------------------------------------------------------------------- sin código impreso
    FACTURA = {"letra": "A", "tipo": "^(?!.*(credito|debito)).*factura"}

    def test_sin_codigo_impreso_se_carga_por_su_letra_y_su_tipo(self):
        x = {"importe": "121.00", "moneda": "XTS", "comercio": "Café", "marker": "[L000123/F01]"}
        c = {"documentos": {"1": {"id": 801, "iva": "discriminado", "sin_codigo": dict(self.FACTURA)}},
             "impuestos": {"iva": {"21": 721}, "sin_credito": 725}}
        leer = lambda tipo, letra="A", codigo="": fiscal.normalizar({**lectura(codigo=codigo, letra=letra), "tipo": tipo})
        f = fiscal.armar(leer("Factura A electrónica"), x, c)
        self.assertEqual((f["modo"], f["documento"], f.get("documento_por_letra")), (fiscal.CON_IMPUESTOS, "1", True))
        self.assertIn("no trae impreso el código de su tipo: por su letra A", fiscal.aviso_carga(f))
        for tipo, letra in (("Nota de Crédito A", "A"), ("Factura B", "B"), ("Factura A", "")):
            with self.subTest(tipo=tipo, letra=letra):
                f = fiscal.armar(leer(tipo, letra), x, c)
                self.assertEqual((f["modo"], f["motivo"]), (fiscal.SIN_IMPUESTOS, "no leí el tipo de comprobante"))
        f = fiscal.armar(leer("Factura A", codigo="1"), x, c)              # con el código impreso manda el impreso
        self.assertNotIn("documento_por_letra", f)
        self.assertEqual(fiscal.aviso_carga(f), "")
        c["documentos"]["51"] = {"id": 851, "iva": "discriminado", "sin_codigo": dict(self.FACTURA)}
        self.assertEqual(fiscal.armar(leer("Factura A"), x, c)["modo"], fiscal.SIN_IMPUESTOS)   # dos candidatos: no elige

    async def test_sin_codigo_impreso_carga_la_factura_con_impuestos(self):
        self.cargar_como(**{"1": {"ids": {"ensayo": 901, "principal": 801}, "iva": "discriminado",
                                  "sin_codigo": dict(self.FACTURA)}})
        self.guardar({**lectura(codigo=""), "tipo": "Factura A electrónica"})
        _, (una, _) = await self.cargar()
        self.assertEqual((una["l10n_latam_document_type_id"][0], una["l10n_latam_document_number"]), (801, "00012-00034567"))
        self.assertIn("no trae impreso el código de su tipo: por su letra A y su tipo se carga con el tipo 1", una["narration"])

    async def test_sin_codigo_valida_la_configuracion(self):
        base = {"ids": {"ensayo": 901, "principal": 801}, "iva": "discriminado"}
        for malo in ({"letra": "a", "tipo": "factura"}, {"letra": "A", "tipo": "("}, {"letra": "A"},
                     {"letra": "A", "tipo": ""}, "A"):
            with self.subTest(malo=malo):
                raiz = copy.deepcopy(self.raiz)
                raiz["empresas"]["taller"]["documentos"]["1"] = {**base, "sin_codigo": malo}
                with self.assertRaisesRegex(gastos.configuracion.ConfigError,
                                            r"^Configuración del bot.*documentos\.1\.sin_codigo"):
                    gastos.configuracion.validar(raiz)
        antes = self.seleccion(destino="principal")
        self.cargar_como(**{"1": {**base, "sin_codigo": dict(self.FACTURA)}})
        despues = self.seleccion(destino="principal")
        self.assertEqual(despues["documentos"]["1"], {"id": 801, "iva": "discriminado", "sin_codigo": self.FACTURA})
        self.assertNotEqual(antes["firma"], despues["firma"])

    async def test_lote_descartado_no_se_prepara(self):
        self.lotes["L000123"]["extra"] = {"descartado": True}
        with self.assertRaisesRegex(gastos.GastoError, "El lote L000123 se descartó"):
            await self.preparar(destino="principal")

    async def test_foto_ya_cargada_en_otro_lote_no_se_vuelve_a_cargar(self):
        await self.cargar()
        self.repetir_lote("L000124")
        with self.assertRaisesRegex(gastos.GastoError, "ya se carg(ó|aron) en el lote L000123"):
            await self.preparar(destino="principal", lote_id="L000124")

    async def test_lote_repetido_marca_el_viejo(self):
        await self.preparar(destino="principal")
        self.repetir_lote("L000124")
        p = await self.preparar(destino="principal", lote_id="L000124")
        self.assertIn("lote L000123, que quedó sin efecto", " ".join(p["propuesta"]["avisos"]))
        r = await self.aprobar(destino="principal")
        self.assertTrue(r["is_error"])
        self.assertIn("L000124", json.dumps(r, ensure_ascii=False))
        self.assertNotIn("is_error", await self.aprobar(destino="principal", lote_id="L000124"))

    async def test_lote_cerrado_guarda_las_lecturas(self):
        from contextlib import asynccontextmanager
        from bot import core, turnos
        lote = {**copy.deepcopy(self.lote), "id": 7, "lote_id": "L000125", "chat_id": "!gastos",
                "persona_uid": "@persona:ejemplo.test"}
        user = SimpleNamespace(login="ana.gomez", uid_en=lambda _: "matrix:@persona:ejemplo.test")

        @asynccontextmanager
        async def turno(_):
            yield
        store = SimpleNamespace(por_pk=lambda _: {"extra": {}}, set_extra=lambda *_: None)
        with (patch.object(lotes, "store", return_value=store), patch("bot.identity.User.load", return_value=user),
              patch.object(core.sessions, "get", return_value=object()),
              patch.object(core, "on_text", AsyncMock(return_value=getattr(core, "TURNO_COMPLETO", "ok"))),
              patch.object(turnos, "turno", turno),
              patch.object(gastos, "_leer_foto", AsyncMock(side_effect=[lectura(), {"importe": "22"}]))):
            await gastos._lote_cerrado(SimpleNamespace(name="matrix", send_grupo=AsyncMock()), lote)
        primera = fiscal.lectura("L000125", 1, lote["items"][0]["sha256"])
        self.assertEqual(primera["fiscal"]["documento_numero"], "0012-00034567")
        self.assertIsNone(fiscal.lectura("L000125", 1, "otra foto"))
        self.assertEqual(fiscal.lectura("L000125", 2, lote["items"][1]["sha256"])["fiscal"]["iva"], [])


# Los tests de destinos ya corren en su suite: acá solo se hereda su banco.
for _nombre in [n for n in vars(destinos.DestinosTests) if n.startswith("test_")]:
    if _nombre not in vars(FiscalTests):
        setattr(FiscalTests, _nombre, None)


class FiscalUnidadTests(unittest.TestCase):
    def test_receptor_con_un_digito_mal_leido_es_el_de_la_empresa(self):
        c = {"id_fiscal": "30123456780"}
        leida = lambda r: fiscal.normalizar({"fiscal": {"receptor_id_fiscal": r}})
        self.assertEqual((fiscal.receptor_ajeno(c, leida("30123556780")), fiscal.receptor_mal_leido(c, leida("30123556780"))),
                         ("", "30123556780"))
        for otro in ("30123556781", "27987654321", "3012345678"):         # dos dígitos, otro CUIT, otro largo
            self.assertEqual((fiscal.receptor_ajeno(c, leida(otro)), fiscal.receptor_mal_leido(c, leida(otro))), (otro, ""))
        self.assertEqual((fiscal.receptor_ajeno(c, leida("30-12345678-0")), fiscal.receptor_mal_leido(c, leida(""))), ("", ""))

    def test_referencia_de_la_factura_es_la_imputacion(self):
        estandar = gastos.estandar
        self.assertEqual(estandar.referencia({"imputacion": {"texto": "  Gastos de   representación "}}),
                         "Gastos de representación")
        self.assertEqual(estandar.referencia({}), "")

    def test_numeros_e_importes(self):
        self.assertEqual(fiscal.numero_odoo("0012-00034567"), "00012-00034567")
        self.assertEqual(fiscal.numero_canonico("00012-00034567"), fiscal.numero_canonico("12 34567"))
        self.assertEqual(fiscal.numero_canonico("A0001234"), "A-1234")
        self.assertEqual(fiscal.numero_odoo("A0001234"), "A0001234")
        for texto, valor in (("1234.56", "1234.56"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("", "0.00"),
                             ("$ 15000", "15000.00")):
            self.assertEqual(fiscal.importe(texto), Decimal(valor), texto)
        for texto, valor in (("156985.276", "156985.28"), ("32966.908", "32966.91"), ("1.2345", "1.23")):
            self.assertEqual(fiscal.importe(texto), Decimal(valor), texto)     # tres o cuatro decimales: al centavo
        for malo in ("abc", "-3", "1.23456"):
            self.assertIsNone(fiscal.importe(malo), malo)
        self.assertEqual(fiscal.codigo_documento("COD. 001"), "1")

    def test_moneda_como_simbolo_no_cuenta(self):
        x = {"importe": "121.00", "moneda": "XTS", "comercio": "Café", "marker": "[L000123/F01]"}
        c = {"documentos": {"1": {"id": 801, "iva": "discriminado"}},
             "impuestos": {"iva": {"21": 721}, "sin_credito": 725}}
        self.assertEqual(fiscal.armar(fiscal.normalizar({**lectura(), "moneda": "$"}), x, c)["modo"], fiscal.CON_IMPUESTOS)
        f = fiscal.armar(fiscal.normalizar({**lectura(), "moneda": "USD"}), x, c)
        self.assertEqual(f["modo"], fiscal.SIN_IMPUESTOS)
        self.assertIn("moneda", f["motivo"])

    def test_la_unica_escritura_es_el_importe_de_lineas_existentes(self):
        ajuste = ([40000], {"line_ids": [(1, 4000050, {"amount_currency": 6.0})]})
        self.assertTrue(gastos._ajuste_de_impuesto("account.move", "write", ajuste))
        for modelo, metodo, args in (
                ("account.move.line", "write", ajuste), ("account.move", "unlink", ajuste),
                ("account.move", "write", ([40000], {"line_ids": [(1, 4000050, {"amount_currency": 6.0})], "state": "posted"})),
                ("account.move", "write", ([40000], {"line_ids": [(1, 4000050, {"price_unit": 6.0})]})),
                ("account.move", "write", ([40000], {"line_ids": [(0, 0, {"amount_currency": 6.0})]})),
                ("account.move", "write", ([40000], {"line_ids": [(2, 4000050, {"amount_currency": 6.0})]})),
                ("account.move", "write", ([40000, 40001], {"line_ids": [(1, 4000050, {"amount_currency": 6.0})]})),
                ("account.move", "write", ([40000], {"line_ids": []})),
                ("account.move", "write", ([40000], {"partner_id": 9001}))):
            with self.subTest(modelo=modelo, metodo=metodo, args=args):
                self.assertFalse(gastos._ajuste_de_impuesto(modelo, metodo, args))

    def test_un_plan_de_antes_no_verifica_lineas_de_impuesto(self):
        # Una propuesta preparada con la dev.8 se carga como entonces: sin percepciones como impuesto.
        viejo = {"iva": "21.00", "lineas": [{"tipo": "iva", "etiqueta": "IVA 21 %", "importe": "100.00", "impuestos": [721]}]}
        self.assertIsNone(fiscal.lineas_impuesto(viejo))
        self.assertEqual(fiscal.impuesto_total(viejo), Decimal("21.00"))
        nuevo = {**viejo, "lineas": [{**viejo["lineas"][0], "impuestos": [721, 731], "iva": "21.00"}],
                 "percepciones": [{"impuesto": 731, "etiqueta": "Perc", "importe": "6.00"}]}
        self.assertEqual(fiscal.lineas_impuesto(nuevo), {721: Decimal("21.00"), 731: Decimal("6.00")})
        self.assertEqual(fiscal.impuesto_total(nuevo), Decimal("27.00"))

    def test_el_lector_da_tipo_y_jurisdiccion_de_cada_percepcion(self):
        campos = fiscal.ESQUEMA["properties"]["fiscal"]["properties"]["percepciones"]["items"]["required"]
        self.assertEqual(set(campos), {"tipo", "jurisdiccion", "descripcion", "importe"})
        self.assertIn("B provincia de Buenos Aires (ARBA)", fiscal.INSTRUCCIONES)
        viejo = fiscal.normalizar({"fiscal": {"percepciones": [{"descripcion": "Perc", "importe": "1"}]}})
        self.assertEqual(viejo["fiscal"]["percepciones"][0], {"tipo": "", "jurisdiccion": "", "descripcion": "Perc",
                                                              "importe": "1"})
        imp = {"percepciones": {"iva": 732, "iibb": {"B": 731}}}
        for leida, impuesto in (({"tipo": " IIBB", "jurisdiccion": "b "}, 731), ({"tipo": "iibb", "jurisdiccion": ""}, 0),
                                ({"tipo": "iva", "jurisdiccion": "B"}, 732), ({"tipo": "ganancias", "jurisdiccion": ""}, 0),
                                ({"tipo": "", "jurisdiccion": ""}, 0)):
            self.assertEqual(fiscal.impuesto_percepcion(imp, leida), impuesto, leida)
        self.assertEqual(fiscal.impuesto_percepcion({}, {"tipo": "iva", "jurisdiccion": ""}), 0)

    def test_lector_normaliza_y_acota(self):
        sucio = {"importe": 5, "lineas": [{"descripcion": "x" * 900, "cantidad": "1", "importe": "1"}] * 100,
                 "fiscal": {"iva": "no es lista", "percepciones": [{"descripcion": "a\u0000b", "importe": "1"}]}}
        limpio = fiscal.normalizar(sucio)
        self.assertEqual(limpio["importe"], "")
        self.assertEqual(len(limpio["lineas"]), 60)
        self.assertEqual(len(limpio["lineas"][0]["descripcion"]), 200)
        self.assertEqual(limpio["fiscal"]["iva"], [])
        self.assertEqual(limpio["fiscal"]["percepciones"][0]["descripcion"], "ab")
        esquema = fiscal.ESQUEMA
        self.assertEqual(set(esquema["required"]), set(esquema["properties"]))
        interno = esquema["properties"]["fiscal"]
        self.assertEqual(set(interno["required"]), set(interno["properties"]))
