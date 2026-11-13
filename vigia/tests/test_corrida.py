import json
import os
import tempfile
import unittest

from vigia import __main__ as cli
from vigia import censo, config, corrida, escaner, estado, fuentes

import datos
from datos import AHORA, H, Red, imagen_id, tag

VIEJA = "2026-12-01T00:00:00Z"
DIA = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config.validar(datos.config(self.tmp.name))
        self.contenedores = [("db", "postgres:16.15", 1), ("bot", "propio/bot:0.6.30", 2)]
        self.red = Red().hub("library/postgres", [tag("16.16", VIEJA), tag("16.15", VIEJA)])
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": "CVE-2099-0001"}]}).encode())
        self.falla = None

    def tomar(self, servidor, ahora, vigencia):
        if self.falla:
            return {"nombre": servidor["nombre"], "estado": "desconocido", "motivo": self.falla, "foto": None}
        cruda = datos.foto(self.contenedores)
        cruda["tomada"] = ahora
        return {"nombre": servidor["nombre"], "estado": "ok", "motivo": "", "foto": censo.limpiar(cruda, ahora, vigencia)}

    def correr(self, ahora=AHORA, **k):
        return corrida.correr(self.cfg, ahora, tomar=self.tomar, pedir=self.red, **k)

    def candidatas(self):
        db = estado.abrir(self.cfg["estado_dir"])
        try:
            return {f["propuesta"]: dict(f) for f in db.execute("SELECT * FROM candidatas")}
        finally:
            db.close()


class Corrida(Base):
    def test_detecta_registra_y_deja_ficha_y_resumen(self):
        r = self.correr()
        self.assertEqual(len(r["cambios"]["nuevas"]), 1)
        self.assertIn("postgres: 16.15 → 16.16 (parche)", r["resumen"])
        self.assertIn("No se aplicó nada", r["resumen"])
        self.assertIn("Al día: 0 · atrasadas: 1 · sin poder saber: 0 · propias: 1", r["resumen"])
        with open(r["ficha"], encoding="utf-8") as f:
            ficha = f.read()
        self.assertIn("| postgres | 16.15 | atrasada | 16.16 | parche | 2026-12-01 | pasaría | 2027-02-14 |", ficha)
        self.assertIn("desconocido (sin escáner configurado)", ficha)          # nunca «0 vulnerabilidades»
        self.assertIn("| propio/bot | 0.6.30 | propia |", ficha)
        self.assertIn("propio/bot: sin imagen base declarada", ficha)
        with open(os.path.join(self.cfg["registro_dir"], "indice.md"), encoding="utf-8") as f:
            self.assertIn("2027/01/270115-080000.md", f.read())
        self.assertEqual(self.candidatas()["16.16"]["estado"], "detectada")

    def test_el_plazo_no_se_reinicia_entre_corridas(self):
        self.correr()
        r = self.correr(AHORA + 3 * DIA)
        self.assertEqual(r["cambios"]["nuevas"], [])
        c = r["servidores"][0]["piezas"][0]["candidata"]
        self.assertEqual((c["detectada"], c["plazo"]["vence"]), (AHORA, "2027-02-14T08:00:00Z"))

    def test_cuando_la_pieza_se_actualiza_la_candidata_se_cierra(self):
        self.correr()
        self.contenedores[0] = ("db", "postgres:16.16", 3)
        r = self.correr(AHORA + DIA)
        self.assertEqual(len(r["cambios"]["cerradas"]), 1)
        self.assertEqual(self.candidatas()["16.16"]["motivo"], "la pieza corre 16.16")

    def test_una_candidata_mas_nueva_reemplaza_a_la_anterior(self):
        self.correr()
        self.red.hub("library/postgres", [tag("16.17", VIEJA), tag("16.16", VIEJA), tag("16.15", VIEJA)])
        r = self.correr(AHORA + DIA)
        self.assertEqual((len(r["cambios"]["nuevas"]), len(r["cambios"]["descartadas"])), (1, 1))
        c = self.candidatas()
        self.assertEqual((c["16.16"]["estado"], c["16.16"]["motivo"], c["16.17"]["estado"]),
                         ("descartada", "la reemplazó 16.17", "detectada"))

    def test_una_fuente_caida_no_cierra_ni_descarta(self):
        self.correr()
        del self.red.respuestas["https://hub.docker.com/v2/repositories/library/postgres/tags"]
        r = self.correr(AHORA + DIA)
        self.assertEqual(r["cambios"], {"nuevas": [], "cerradas": [], "descartadas": []})
        self.assertEqual(self.candidatas()["16.16"]["estado"], "detectada")
        self.assertIn("postgres: sin poder saber", r["resumen"])
        self.assertEqual(r["fuentes"]["docker.io/library/postgres"]["estado"], "desconocido")

    def test_un_servidor_que_no_se_pudo_medir_no_da_bajas_falsas(self):
        self.correr()
        self.falla = "foto vencida"
        r = self.correr(AHORA + DIA)
        self.assertEqual(r["cambios"], {"nuevas": [], "cerradas": [], "descartadas": []})
        self.assertEqual(self.candidatas()["16.16"]["estado"], "detectada")
        self.assertIn("principal: no se pudo medir (foto vencida)", r["resumen"])

    def test_una_pieza_que_deja_de_correr_se_descarta(self):
        self.correr()
        self.contenedores.pop(0)
        self.correr(AHORA + DIA)
        self.assertEqual(self.candidatas()["16.16"]["motivo"], "la pieza ya no corre en el servidor")

    def test_sin_fuentes_no_sale_a_internet(self):
        r = self.correr(con_fuentes=False)
        self.assertEqual(self.red.pedidos, [])
        self.assertIn("sin poder saber: 1", r["resumen"])

    def test_la_imagen_propia_nunca_se_nombra_hacia_afuera(self):
        self.correr()
        self.assertTrue(all("propio" not in url for url, _ in self.red.pedidos))

    def test_dos_corridas_a_la_vez_no(self):
        os.makedirs(self.cfg["estado_dir"], exist_ok=True)
        candado = os.path.join(self.cfg["estado_dir"], "corrida.candado")
        open(candado, "w").close()
        os.utime(candado, (AHORA, AHORA))
        with self.assertRaises(corrida.Ocupado):
            self.correr()
        os.utime(candado, (AHORA - 3 * 3600, AHORA - 3 * 3600))        # el de una corrida que murió se toma
        self.correr()
        self.assertFalse(os.path.exists(candado))


class Escaner(Base):
    INFORME = {"Results": [{"Vulnerabilities": [
        {"VulnerabilityID": "CVE-2099-0001", "Severity": "CRITICAL", "FixedVersion": "1.2"},
        {"VulnerabilityID": "CVE-2099-0002", "Severity": "HIGH"},
        {"VulnerabilityID": "CVE-2099-0003", "Severity": "LOW", "FixedVersion": "9"}]},
        {"Vulnerabilities": [{"VulnerabilityID": "CVE-2099-0001", "Severity": "CRITICAL", "FixedVersion": "1.2"}]},
        {"Target": "sin hallazgos"}]}

    def test_cuenta_sin_repetir_y_cruza_con_la_lista_de_explotadas(self):
        r = escaner.resumir(self.INFORME, {"CVE-2099-0001"})
        self.assertEqual((r["total"], r["altas"], r["altas_con_arreglo"], r["kev"], r["kev_aplicable"]),
                         (3, 2, 1, ["CVE-2099-0001"], False))

    def test_sin_escaner_cortado_o_ilegible_es_desconocido_nunca_cero(self):
        ids = [imagen_id(1), imagen_id(2)]
        self.assertEqual({v["estado"] for v in escaner.escanear(None, ids, set(), 60).values()}, {"desconocido"})
        for correr in (lambda a, t: (None, b""), lambda a, t: (1, b"{}"), lambda a, t: (0, b"no es json"),
                       lambda a, t: (0, b'{"Results": 7}')):
            r = escaner.escanear(["escaner"], ids, set(), 60, correr=correr)
            self.assertEqual({v["estado"] for v in r.values()}, {"desconocido"})
            self.assertTrue(all("total" not in v for v in r.values()))

    def test_el_tope_es_de_toda_la_pasada(self):
        reloj = iter([0, 0, 50, 100, 200, 300])
        llamadas = []

        def correr(argv, tope):
            llamadas.append((argv[-1], tope))
            return 0, json.dumps(self.INFORME).encode()
        r = escaner.escanear(["escaner", "--json"], [imagen_id(1), imagen_id(2), imagen_id(3)], set(), 60,
                             correr=correr, reloj=lambda: next(reloj))
        self.assertEqual([i for i, _ in llamadas], [imagen_id(1), imagen_id(2)])
        self.assertEqual(r[imagen_id(3)], {"estado": "desconocido", "motivo": "se acabó el tiempo de la pasada"})

    def test_la_corrida_lleva_las_vulnerabilidades_a_la_ficha_y_al_plazo(self):
        def escanear(comando, ids, cves, tope):
            return {i: escaner.resumir(self.INFORME, cves) for i in ids}
        r = corrida.correr(self.cfg, AHORA, tomar=self.tomar, pedir=self.red, escanear=escanear)
        db = r["servidores"][0]["piezas"][0]
        self.assertEqual(db["pieza"], "docker.io/library/postgres")
        self.assertEqual(db["candidata"]["plazo"]["clase"], "alta_interna")
        self.assertIn("tiene vulnerabilidades con explotación conocida; falta verificar si aplican", r["resumen"])
        with open(r["ficha"], encoding="utf-8") as f:
            self.assertIn("3 (2 altas, 1 con arreglo); en KEV sin aplicabilidad verificada: CVE-2099-0001", f.read())


class Configuracion(Base):
    def test_lo_desconocido_no_arranca(self):
        casos = [{"sorpresa": 1}, {"servidores": []}, {"servidores": [{"nombre": "Con Espacios"}]},
                 {"servidores": [{"nombre": "a"}, {"nombre": "a"}]}, {"servidores": [{"nombre": "a", "lector": "ssh hoja"}]},
                 {"componentes": {"x": {"nivel": "todo"}}}, {"componentes": {"x": {"aplicar": True}}},
                 {"plazos_h": {"ordinario": 0}}, {"maduracion_dias": -1}, {"escaner": {"comando": "escaner --json"}},
                 {"estado_dir": ""}, {"fuentes": {"registros": "uno"}}]
        for cambio in casos:
            with self.assertRaises(config.ConfigError, msg=cambio):
                config.validar(datos.config(self.tmp.name, **cambio))

    def test_por_defecto_ninguna_pieza_admite_aplicacion_automatica(self):
        self.assertEqual(config.componente(self.cfg, "docker.io/ejemplo/sin-declarar")["nivel"], "nunca")

    def test_el_ejemplo_del_componente_es_valido(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ejemplo", "vigia.json")
        self.assertEqual(config.cargar(ruta)["servidores"][0]["lector"], "local")


class Consola(Base):
    def test_resumen_sin_corrida_y_configuracion_rota(self):
        ruta = os.path.join(self.tmp.name, "vigia.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos.config(self.tmp.name), f)
        self.assertEqual(cli.main(["resumen", "--config", ruta]), 1)
        self.assertEqual(cli.main(["resumen", "--config", ruta + ".no"]), 2)


if __name__ == "__main__":
    unittest.main()
