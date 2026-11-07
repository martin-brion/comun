"""Compuerta por hashes, con identificadores inventados: lo que frena, lo que nunca muestra y dónde falla cerrada."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import verificar_identificadores as gate  # noqa: E402


class Compuerta(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.arbol = self.base / "arbol"
        self.arbol.mkdir()
        (self.arbol / "doc.md").write_text("contenido limpio\n", encoding="utf-8")
        self.manifiesto = self.base / "hashes.json"
        self.datos = {"schema_version": 1, "normalization": gate.NORMALIZACION, "source_sha256": "0" * 64,
                      "rules": [], "exceptions": []}
        self.agregar("casa-lila", "substring")
        self.agregar("loma", "word")

    def agregar(self, valor, tipo):
        valor = gate.normalizar(valor)
        self.datos["rules"].append({"kind": tipo, "length": len(valor), "sha256": gate.huella(valor)})
        self.manifiesto.write_text(json.dumps(self.datos), encoding="utf-8")

    def correr(self):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
            codigo = gate.main(["--tree", str(self.arbol), "--manifest", str(self.manifiesto)])
        return codigo, salida.getvalue()

    def test_limpio(self):
        self.assertEqual(self.correr()[0], 0)

    def test_frena_y_no_muestra_el_valor(self):
        (self.arbol / "doc.md").write_text("limpio\nvisita a CASA-LILA hoy\n", encoding="utf-8")
        codigo, salida = self.correr()
        self.assertEqual(codigo, 1)
        self.assertIn("doc.md:2:", salida)
        self.assertNotIn("casa-lila", salida.casefold())

    def test_nombre_de_sesion_con_la_instalacion(self):
        (self.arbol / "doc.md").write_text("avisar a casa-lila-54\n", encoding="utf-8")
        self.assertEqual(self.correr()[0], 1)

    def test_nombre_de_archivo_oculto(self):
        (self.arbol / "casa-lila.py").write_text("pass\n", encoding="utf-8")
        codigo, salida = self.correr()
        self.assertEqual(codigo, 1)
        self.assertNotIn("casa-lila", salida.casefold())

    def test_palabra_entera(self):
        (self.arbol / "doc.md").write_text("palomas y lomas\n", encoding="utf-8")
        self.assertEqual(self.correr()[0], 0)
        (self.arbol / "doc.md").write_text("en la loma\n", encoding="utf-8")
        self.assertEqual(self.correr()[0], 1)

    def test_binario_frena(self):
        (self.arbol / "dato.bin").write_bytes(b"\xff\x00")
        self.assertEqual(self.correr()[0], 2)

    def test_manifiesto_ausente_frena(self):
        self.manifiesto.unlink()
        self.assertEqual(self.correr()[0], 2)


class Inventario(unittest.TestCase):
    def test_sin_excepciones_y_con_reglas(self):
        datos = json.loads(gate.MANIFIESTO.read_text(encoding="utf-8"))
        self.assertEqual(datos["exceptions"], [])
        self.assertGreaterEqual(len(datos["rules"]), 100)
        gate.cargar(gate.MANIFIESTO, gate.MANIFIESTO.parent.parent.resolve())


if __name__ == "__main__":
    unittest.main()
