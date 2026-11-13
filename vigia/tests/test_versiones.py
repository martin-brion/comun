import unittest

from vigia import versiones as V


class Referencias(unittest.TestCase):
    def test_una_imagen_oficial_sin_registro_va_al_indice_publico(self):
        r = V.referencia("postgres:16.15")
        self.assertEqual((r.registro, r.repo, r.tag, r.digest), ("docker.io", "library/postgres", "16.15", ""))

    def test_registro_propio_puerto_y_huella(self):
        h = "sha256:" + "a" * 64
        r = V.referencia(f"registro.ejemplo.test:5000/equipo/forja:15.0.9-rootless@{h}")
        self.assertEqual((r.registro, r.repo, r.tag, r.digest),
                         ("registro.ejemplo.test:5000", "equipo/forja", "15.0.9-rootless", h))
        self.assertEqual(r.pieza, "registro.ejemplo.test:5000/equipo/forja")

    def test_lo_que_no_es_una_referencia_no_pasa(self):
        for texto in ("", "a b", "x@sha256:corta", "../x", "x;rm", None, 7, "x" * 400):
            self.assertIsNone(V.referencia(texto), texto)


class Etiquetas(unittest.TestCase):
    def test_partes(self):
        e = V.etiqueta("v1.165.0")
        self.assertEqual((e.prefijo, e.numeros, e.variante, e.fecha, e.previa), ("v", (1, 165, 0), "", "", False))
        e = V.etiqueta("15.0.9-rootless")
        self.assertEqual((e.numeros, e.variante), ((15, 0, 9), "rootless"))
        e = V.etiqueta("18.0-20261112")
        self.assertEqual((e.numeros, e.variante, e.fecha), ((18, 0), "", "20261112"))

    def test_las_preliminares_se_reconocen(self):
        for texto in ("v1.165.0rc1", "1.12.34-rc.1", "2.4.5-dev", "3.0.0-beta2", "4.1.0-alpha", "1.2.3-nightly"):
            self.assertTrue(V.etiqueta(texto).previa, texto)
        for texto in ("1.2.3", "1.2.3-alpine", "15.0.9-rootless", "2.4.5-root"):
            self.assertFalse(V.etiqueta(texto).previa, texto)

    def test_lo_que_no_es_version_no_se_compara(self):
        for texto in ("latest", "stable", "", "alpine", None):
            self.assertIsNone(V.etiqueta(texto), texto)

    def test_familia_misma_variante_y_misma_cantidad_de_numeros(self):
        a = V.etiqueta("1.30.5-alpine")
        self.assertTrue(V.misma_familia(a, V.etiqueta("1.30.6-alpine")))
        self.assertFalse(V.misma_familia(a, V.etiqueta("1.30.6")))
        self.assertFalse(V.misma_familia(a, V.etiqueta("1.30.6-alpine3.22")))
        self.assertFalse(V.misma_familia(a, V.etiqueta("1.31-alpine")))
        self.assertFalse(V.misma_familia(a, V.etiqueta("v1.30.6-alpine")))
        self.assertFalse(V.misma_familia(V.etiqueta("18.0"), V.etiqueta("18.0-20261112")))

    def test_tamano_del_salto(self):
        s = lambda a, b, linea=None: V.salto(V.etiqueta(a), V.etiqueta(b), linea)
        self.assertEqual(s("1.162.0", "1.162.1"), "patch")
        self.assertEqual(s("1.162.0", "1.165.0"), "minor")
        self.assertEqual(s("15.0.9", "17.0.1"), "mayor")
        self.assertEqual(s("16.15", "16.16"), "patch")              # dos números: el segundo es el parche
        self.assertEqual(s("16.15", "17.1"), "mayor")
        self.assertEqual(s("18.0-20260817", "18.0-20261112"), "patch")
        self.assertEqual(s("2026.10.0", "2026.11.0"), "minor")
        self.assertEqual(s("2026.10.0", "2026.11.0", linea=1), "patch")  # versión por calendario, declarada en la instancia

    def test_etiqueta_flotante(self):
        hermanas = ["1.30-alpine", "1.30.6-alpine", "1.30.6", "18", "18.0", "18.0-20261112", "latest", "16.15"]
        self.assertTrue(V.flotante("latest", hermanas))
        self.assertTrue(V.flotante("1.30-alpine", hermanas))
        self.assertTrue(V.flotante("18", hermanas))
        self.assertTrue(V.flotante("18.0", hermanas))
        self.assertFalse(V.flotante("1.30.6-alpine", hermanas))
        self.assertFalse(V.flotante("18.0-20261112", hermanas))
        self.assertFalse(V.flotante("16.15", hermanas))

    def test_el_nivel_nunca_cubre_un_salto_mayor(self):
        self.assertTrue(V.permite("patch", "patch"))
        self.assertFalse(V.permite("patch", "minor"))
        self.assertTrue(V.permite("minor", "minor"))
        for nivel in V.NIVELES:
            self.assertFalse(V.permite(nivel, "mayor"))
        self.assertFalse(V.permite("nunca", "patch"))
        self.assertFalse(V.permite("todo", "patch"))


if __name__ == "__main__":
    unittest.main()
