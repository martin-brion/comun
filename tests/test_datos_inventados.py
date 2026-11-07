"""Un CUIT de prueba no puede tener dígito verificador válido: si lo tiene, puede ser de alguien."""
from pathlib import Path
import re
import unittest

RAIZ = Path(__file__).resolve().parent.parent
CUIT = re.compile(rb"(?<![\d])(?:20|23|24|27|30|33|34)-?\d{8}-?\d(?![\d])")
PESOS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def valido(numero):
    digitos = [int(c) for c in numero if c.isdigit()]
    resto = 11 - sum(d * p for d, p in zip(digitos, PESOS)) % 11
    return {11: 0, 10: 9}.get(resto, resto) == digitos[10]


def hallados(raiz):
    for ruta in sorted(raiz.rglob("*")):
        if ruta.is_file() and ".git" not in ruta.parts and "__pycache__" not in ruta.parts:
            for numero in CUIT.findall(ruta.read_bytes()):
                if valido(numero.decode()):
                    yield ruta.relative_to(raiz).as_posix()


class DatosInventados(unittest.TestCase):
    def test_el_verificador_distingue(self):
        cuerpo = "3012345678"                # partido a propósito, para que este archivo tampoco lleve uno válido
        self.assertTrue(valido(cuerpo + "1"))
        self.assertFalse(valido(cuerpo + "0"))

    def test_ningun_cuit_valido_en_el_repo(self):
        self.assertEqual(sorted(set(hallados(RAIZ))), [])


if __name__ == "__main__":
    unittest.main()
