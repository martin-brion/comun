"""Pruebas del mapa por columnas. El dibujo sale de Python, así que se prueba entero y sin navegador."""
import copy
import json
import re
import unittest
from pathlib import Path

from informe.armado import componer
from informe.datos import ErrorDatos, validar
from informe.mapa import _partir, dibujar

EJEMPLO = Path(__file__).resolve().parent.parent / "ejemplo"
RAICES = {"casa": str(EJEMPLO / "fuentes")}


def ejemplo():
    return json.loads((EJEMPLO / "datos.json").read_text(encoding="utf-8"))


def mapa(d=None, cual=0):
    d = d or ejemplo()
    return [b for s in d["secciones"] for b in s["bloques"] if b["tipo"] == "mapa"][cual]


def cajas(svg):
    """{título: (x, y, ancho, alto)} de cada caja con título, leyendo el dibujo."""
    vistas = {}
    for grupo in re.findall(r'<g class="mp-(?:normal|acento|aviso|apagado)"[^>]*>(.*?)</g>', svg):
        rect = re.search(r'<rect class="mp-caja" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', grupo)
        titulo = re.search(r'<text class="mp-titulo"[^>]*>([^<]*)</text>', grupo)
        vistas[titulo.group(1)] = tuple(float(v) for v in rect.groups())
    return vistas


class Dibujo(unittest.TestCase):
    def test_parte_renglones_por_palabras(self):
        self.assertEqual(_partir("uno dos tres cuatro", 8), ["uno dos", "tres", "cuatro"])
        self.assertEqual(_partir("palabralarguisima", 6), ["palabr", "alargu", "isima"])

    def test_las_columnas_no_se_pisan_y_las_filas_se_alinean(self):
        _, dibujo = dibujar(mapa(), 1)
        c = cajas(dibujo)
        self.assertLess(c["clientes/"][0] + c["clientes/"][2], c["Equipo de altas"][0])
        self.assertLess(c["Equipo de altas"][0] + c["Equipo de altas"][2], c["fichas/norte.md"][0])
        self.assertEqual(c["clientes/"][1], c["Equipo de altas"][1])
        self.assertEqual(c["varios/"][1], c["Revisión a mano"][1])

    def test_una_caja_de_varias_filas_llega_hasta_el_final_de_la_ultima(self):
        _, dibujo = dibujar(mapa(), 1)
        c = cajas(dibujo)
        fondo_clientes = c["clientes/"][1] + c["clientes/"][3]
        self.assertGreater(fondo_clientes, c["Equipo de altas"][1] + c["Equipo de altas"][3])
        self.assertLess(fondo_clientes, c["varios/"][1])

    def test_ninguna_caja_se_superpone_con_otra_de_su_columna(self):
        for cual in (0, 1):
            _, dibujo = dibujar(mapa(cual=cual), 1)
            por_columna = {}
            for x, y, _w, h in cajas(dibujo).values():
                por_columna.setdefault(x, []).append((y, y + h))
            for tramos in por_columna.values():
                tramos.sort()
                for (_, fin), (inicio, _) in zip(tramos, tramos[1:]):
                    self.assertLessEqual(fin, inicio)

    def test_dos_cajas_enfrentadas_se_unen_con_una_flecha_derecha(self):
        _, dibujo = dibujar(mapa(), 1)
        rectas = re.findall(r'd="M([\d.]+) ([\d.]+) L([\d.]+) ([\d.]+)"', dibujo)
        self.assertTrue(rectas)
        self.assertTrue(any(y1 == y2 for _, y1, _, y2 in rectas))

    def test_el_mismo_dato_da_el_mismo_dibujo(self):
        self.assertEqual(dibujar(mapa(), 1), dibujar(mapa(), 1))

    def test_el_texto_se_escapa(self):
        m = mapa()
        m["columnas"][0]["items"][0]["titulo"] = "<script>x</script>"
        m["columnas"][0]["titulo"] = '"><img src=x>'
        m["flechas"][0]["texto"] = "<b>"
        botones, dibujo = dibujar(m, 1)
        self.assertNotIn("<script>", dibujo)
        self.assertNotIn("<img", dibujo)
        self.assertNotIn("<b>", dibujo)


class Vistas(unittest.TestCase):
    def test_siempre_esta_todas_y_va_primera(self):
        botones, _ = dibujar(mapa(), 1)
        nombres = re.findall(r"<button[^>]*>([^<]*)</button>", botones)
        self.assertEqual(nombres, ["Todas", "Ejemplo Norte", "Ejemplo Sur"])
        self.assertIn('data-vista="" aria-pressed="true">Todas', botones)

    def test_sin_vistas_no_hay_botones(self):
        m = mapa()
        m.pop("vistas")
        for c in m["columnas"]:
            for item in c["items"]:
                item.pop("vistas", None)
                for ficha in item.get("dentro", []):
                    ficha.pop("vistas", None)
        for f in m["flechas"]:
            f.pop("vistas", None)
        validar_mapa(m)
        botones, dibujo = dibujar(m, 1)
        self.assertEqual(botones, "")
        self.assertNotIn("data-v=", dibujo)

    def test_cada_cosa_dice_en_que_vistas_esta(self):
        _, dibujo = dibujar(mapa(), 1)
        self.assertIn('<g class="mp-aviso" data-v="sur">', dibujo)
        self.assertIn('<g class="mp-acento">', dibujo)                 # sin lista: está en todas

    def test_una_ficha_hereda_las_vistas_de_su_caja(self):
        _, dibujo = dibujar(mapa(), 1)
        self.assertRegex(dibujo, r'<g class="mp-ficha mp-aviso" data-v="sur">')
        self.assertRegex(dibujo, r'<g class="mp-ficha mp-normal" data-v="norte">')

    def test_una_flecha_sin_vistas_toma_las_que_comparten_sus_cajas(self):
        _, dibujo = dibujar(mapa(), 1)
        flechas = re.findall(r'<path class="mp-flecha"((?: data-v="[^"]*")?) d=', dibujo)
        self.assertEqual(flechas, ["", ' data-v="norte"', ' data-v="sur"', ' data-v="sur"', ' data-v="sur"'])

    def test_el_modo_va_en_la_figura(self):
        indice = componer(ejemplo(), RAICES)["index.html"].decode("utf-8")
        self.assertIn('<figure class="mapa" data-modo="atenuar">', indice)
        self.assertIn('<figure class="mapa" data-modo="ocultar">', indice)

    def test_cada_mapa_tiene_su_propia_punta_de_flecha(self):
        indice = componer(ejemplo(), RAICES)["index.html"].decode("utf-8")
        self.assertEqual(sorted(set(re.findall(r'<marker id="(mp-punta-\d+)"', indice))), ["mp-punta-1", "mp-punta-2"])


def validar_mapa(m):
    d = ejemplo()
    d["secciones"][1]["bloques"] = [m]
    return validar(d)


class Contrato(unittest.TestCase):
    def falla(self, cambio, espera):
        m = copy.deepcopy(mapa())
        cambio(m)
        with self.assertRaises(ErrorDatos) as contexto:
            validar_mapa(m)
        self.assertIn(espera, str(contexto.exception))

    def test_el_ejemplo_vale(self):
        validar_mapa(mapa())
        validar_mapa(mapa(cual=1))

    def test_una_flecha_solo_une_columnas_vecinas(self):
        self.falla(lambda m: m["flechas"].append({"de": "clientes", "a": "ficha-norte"}), "columnas vecinas")

    def test_una_flecha_nombra_cajas_que_existen(self):
        self.falla(lambda m: m["flechas"].append({"de": "equipo", "a": "nadie"}), "tiene que nombrar una caja")

    def test_una_vista_no_declarada_no_vale(self):
        self.falla(lambda m: m["columnas"][0]["items"][1].update(vistas=["oeste"]), "vistas declaradas")

    def test_todas_no_se_declara(self):
        self.falla(lambda m: m["vistas"].append({"id": "todas", "nombre": "Todas"}), "ya existe siempre")

    def test_una_caja_repetida(self):
        self.falla(lambda m: m["columnas"][1]["items"].append({"id": "equipo", "fila": 5, "titulo": "x"}), "está repetida")

    def test_una_caja_larga_no_comparte_sus_filas(self):
        self.falla(lambda m: m["columnas"][0]["items"].append({"id": "otra", "fila": 2, "titulo": "x"}), "no comparte sus filas")

    def test_mas_de_cinco_columnas_no(self):
        self.falla(lambda m: m["columnas"].extend([{"titulo": "x", "items": [{"nota": "n", "fila": 1}]}] * 3), "hasta cinco")

    def test_etiqueta_larga_no(self):
        self.falla(lambda m: m["flechas"][0].update(texto="una etiqueta demasiado larga"), "pasa de 18")

    def test_el_bloque_viejo_ya_no_existe(self):
        d = ejemplo()
        d["secciones"][0]["bloques"].append({"tipo": "antes_despues"})
        with self.assertRaises(ErrorDatos):
            validar(d)


if __name__ == "__main__":
    unittest.main()
