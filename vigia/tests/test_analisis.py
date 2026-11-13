import tempfile
import unittest

from vigia import analisis, censo, config, fuentes

import datos
from datos import AHORA, H, H2, H3, H4, Red, tag

VIEJA = "2026-12-01T00:00:00Z"        # publicada hace más de 7 días
NUEVA = "2027-01-13T00:00:00Z"        # publicada hace 2 días: todavía no maduró


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config.validar(datos.config(self.tmp.name))

    def piezas(self, contenedores, red, imagenes=None, cfg=None):
        cfg = cfg or self.cfg
        foto = censo.limpiar(datos.foto(contenedores, imagenes), AHORA, 900)

        def consultar(tipo, registro, repo):
            return fuentes.docker_hub(repo, red) if tipo == "docker_hub" else fuentes.registro_oci(registro, repo, red)

        def detalle(registro, repo, etiqueta):
            if registro == "docker.io":
                return fuentes.etiqueta_hub(repo, etiqueta, red)
            return fuentes.detalle_oci(registro, repo, etiqueta, red)
        return analisis.piezas(cfg, "principal", foto, consultar, detalle, AHORA)

    def una(self, *a, **k):
        (p,) = self.piezas(*a, **k)
        return p


class Versiones(Base):
    def test_parche_maduro_pasaria_la_compuerta(self):
        red = Red().hub("library/postgres", [tag("16.16", VIEJA), tag("16.15", VIEJA), tag("17.2", VIEJA)])
        p = self.una([("db", "postgres:16.15", 1)], red)
        c = p["candidata"]
        self.assertEqual((p["estado"], p["actual"], c["propuesta"], c["salto"], c["compuerta_version"]),
                         ("atrasada", "16.15", "16.16", "patch", "pasaría"))
        self.assertEqual(p["otras"], [{"version": "17.2", "salto": "mayor"}])
        self.assertIn("sin fijar por huella", p["notas"])

    def test_parche_recien_publicado_espera_la_maduracion(self):
        red = Red().hub("library/postgres", [tag("16.16", NUEVA), tag("16.15", VIEJA)])
        c = self.una([("db", "postgres:16.15", 1)], red)["candidata"]
        self.assertEqual(c["compuerta_version"], "espera: madura el 2027-01-20")

    def test_al_dia(self):
        red = Red().hub("library/postgres", [tag("16.15", VIEJA), tag("16.14", VIEJA)])
        p = self.una([("db", "postgres:16.15", 1)], red)
        self.assertEqual((p["estado"], p["candidata"], p["otras"]), ("al día", None, []))

    def test_las_preliminares_y_las_otras_variantes_no_son_candidatas(self):
        red = Red().hub("library/postgres", [tag("16.16rc1", VIEJA), tag("16.17-beta1", VIEJA), tag("16.16-alpine", VIEJA),
                                             tag("16.16-arm64", VIEJA), tag("16", VIEJA), tag("latest", VIEJA), tag("16.15", VIEJA)])
        self.assertEqual(self.una([("db", "postgres:16.15", 1)], red)["estado"], "al día")

    def test_salto_menor_sobre_nivel_patch_queda_fuera(self):
        red = Red().hub("ejemplo/chat", [tag("v1.165.0", VIEJA), tag("v1.162.0", VIEJA)])
        cfg = config.validar(datos.config(self.tmp.name, componentes={"docker.io/ejemplo/chat": {"nivel": "patch"}}))
        c = self.una([("chat", "ejemplo/chat:v1.162.0", 1)], red, cfg=cfg)["candidata"]
        self.assertEqual((c["salto"], c["compuerta_version"]), ("minor", "fuera: salto minor sobre un nivel patch"))

    def test_primero_el_paso_mas_chico_y_el_resto_se_informa(self):
        red = Red().hub("ejemplo/chat", [tag("v1.162.3", VIEJA), tag("v1.165.0", VIEJA), tag("v2.0.0", VIEJA), tag("v1.162.0", VIEJA)])
        p = self.una([("chat", "ejemplo/chat:v1.162.0", 1)], red)
        self.assertEqual((p["candidata"]["propuesta"], p["candidata"]["salto"]), ("v1.162.3", "patch"))
        self.assertEqual(p["otras"], [{"version": "v1.165.0", "salto": "minor"}, {"version": "v2.0.0", "salto": "mayor"}])

    def test_un_salto_mayor_nunca_pasa_aunque_el_nivel_sea_el_mas_alto(self):
        red = Red().hub("ejemplo/chat", [tag("v2.0.0", VIEJA), tag("v1.162.0", VIEJA)])
        c = self.una([("chat", "ejemplo/chat:v1.162.0", 1)], red)["candidata"]
        self.assertEqual(c["compuerta_version"], "fuera: salto mayor")

    def test_pieza_sin_declarar_no_admite_aplicacion_automatica(self):
        red = Red().hub("ejemplo/otro", [tag("2.4.5", VIEJA), tag("2.4.3", VIEJA)])
        c = self.una([("otro", "ejemplo/otro:2.4.3", 1)], red)["candidata"]
        self.assertEqual(c["compuerta_version"], "fuera: la pieza no admite aplicación automática")


class Flotantes(Base):
    def test_etiqueta_flotante_fijada_por_huella_se_resuelve_a_su_version(self):
        red = Red().hub("library/odoo", [tag("18", NUEVA, H3), tag("18.0", NUEVA, H3), tag("18.0-20270112", NUEVA, H3),
                                         tag("18.0-20260817", VIEJA, H, H2), tag("19.0-20270112", NUEVA, H4)])
        p = self.una([("erp", f"odoo:18@{H2}", 1)], red)
        c = p["candidata"]
        self.assertEqual((p["actual"], p["fijada"], c["propuesta"], c["salto"]), ("18.0-20260817", True, "18.0-20270112", "patch"))
        self.assertEqual(c["compuerta_version"], "fuera: la pieza no admite aplicación automática")
        self.assertTrue(any("flotante" in n for n in p["notas"]))
        self.assertEqual(p["otras"], [{"version": "19.0-20270112", "salto": "mayor"}])   # se informa, no se propone

    def test_flotante_sin_fijar_usa_la_huella_local(self):
        red = Red().hub("ejemplo/web", [tag("1.30-alpine", VIEJA, H2), tag("1.30.6-alpine", VIEJA, H2), tag("1.30.5-alpine", VIEJA, H)])
        p = self.una([("web", "ejemplo/web:1.30-alpine", 1)], red, imagenes={1: {"digests": [f"ejemplo/web@{H}"]}})
        self.assertEqual((p["actual"], p["candidata"]["propuesta"]), ("1.30.5-alpine", "1.30.6-alpine"))
        self.assertIn("sin fijar por huella", p["notas"])

    def test_flotante_que_no_se_puede_resolver_pero_ya_apunta_a_otra_imagen(self):
        red = Red().hub("library/odoo", [tag("18", NUEVA, H3), tag("18.0-20270112", NUEVA, H3)])
        p = self.una([("erp", f"odoo:18@{H}", 1)], red)
        c = p["candidata"]
        self.assertEqual((p["estado"], c["actual"], c["propuesta"]), ("atrasada", "18", "18.0-20270112"))
        self.assertIn("no se sabe cuál corre", c["nota"])

    def test_la_propuesta_de_una_flotante_prefiere_su_misma_variante(self):
        red = Red().hub("ejemplo/web", [tag("1.30-alpine", VIEJA, H2), tag("1.30.6-alpine3.24", VIEJA, H2), tag("1.30.6-alpine", VIEJA, H2)])
        p = self.una([("web", "ejemplo/web:1.30-alpine", 1)], red, imagenes={1: {"digests": [f"ejemplo/web@{H}"]}})
        self.assertEqual(p["candidata"]["propuesta"], "1.30.6-alpine")

    def test_flotante_que_sigue_apuntando_a_la_misma_imagen(self):
        red = Red().hub("library/odoo", [tag("18", VIEJA, H)])
        self.assertEqual(self.una([("erp", f"odoo:18@{H}", 1)], red)["estado"], "al día")

    def test_flotante_sin_huella_local_es_desconocido(self):
        red = Red().hub("ejemplo/web", [tag("latest", VIEJA, H)])
        p = self.una([("web", "ejemplo/web:latest", 1)], red)
        self.assertEqual((p["estado"], p["candidata"]), ("desconocido", None))


class Fuentes(Base):
    def test_una_fuente_caida_es_desconocido_nunca_al_dia(self):
        p = self.una([("db", "postgres:16.15", 1)], Red())
        self.assertEqual((p["estado"], p["candidata"]), ("desconocido", None))
        self.assertTrue(p["motivo"])

    def test_un_registro_sin_aprobar_no_se_consulta(self):
        red = Red()
        p = self.una([("x", "otro.ejemplo.test/equipo/cosa:1.2.3", 1)], red)
        self.assertEqual((p["estado"], p["motivo"]), ("desconocido", "registro sin fuente aprobada"))
        self.assertEqual(red.pedidos, [])

    def test_registro_aprobado_trae_la_fecha_de_la_candidata(self):
        from test_fuentes import registro
        red = registro(["15.0.9-rootless", "15.0.11-rootless", "17.0.1-rootless", "15.0.11"], creada=VIEJA)
        p = self.una([("forja", "registro.ejemplo.test/equipo/forja:15.0.9-rootless", 1)], red)
        c = p["candidata"]
        self.assertEqual((c["propuesta"], c["salto"], c["publicada"], c["compuerta_version"]),
                         ("15.0.11-rootless", "patch", VIEJA, "pasaría"))
        self.assertEqual(p["otras"], [{"version": "17.0.1-rootless", "salto": "mayor"}])

    def test_sin_fecha_de_publicacion_espera(self):
        from test_fuentes import registro
        red = registro(["15.0.9-rootless", "15.0.10-rootless"])
        c = self.una([("forja", "registro.ejemplo.test/equipo/forja:15.0.9-rootless", 1)], red)["candidata"]
        self.assertEqual(c["compuerta_version"], "espera: sin fecha de publicación")


class Propias(Base):
    def test_una_imagen_propia_no_sale_a_ninguna_fuente(self):
        red = Red()
        p = self.una([("bot", "propio/bot:0.6.30", 1)], red)
        self.assertEqual((p["estado"], p["propia"], p["candidata"]), ("propia", True, None))
        self.assertEqual(red.pedidos, [])
        self.assertTrue(any("sin imagen base declarada" in n for n in p["notas"]))

    def test_base_renovada_despues_de_armar_la_imagen(self):
        cfg = config.validar(datos.config(self.tmp.name, componentes={"docker.io/propio/bot": {"base": "python:3.13-slim"}}))
        red = Red().hub("library/python", [tag("3.13-slim", "2027-01-10T00:00:00Z", H)])
        p = self.una([("bot", "propio/bot:0.6.30", 1)], red, imagenes={1: {"creada": "2026-12-20T00:00:00Z"}}, cfg=cfg)
        self.assertEqual(p["estado"], "base renovada")
        self.assertTrue(any("2027-01-10" in n for n in p["notas"]))
        self.assertTrue(all("propio" not in u for u, _ in red.pedidos))

    def test_base_que_no_esta_entre_las_recientes_se_pide_aparte(self):
        import json
        cfg = config.validar(datos.config(self.tmp.name, componentes={"docker.io/propio/bot": {"base": "python:3.13-slim"}}))
        red = Red().hub("library/python", [tag("3.14-slim", NUEVA, H)])
        red.respuestas["https://hub.docker.com/v2/repositories/library/python/tags/3.13-slim"] = (
            200, {}, json.dumps(tag("3.13-slim", "2027-01-10T00:00:00Z", H2)).encode())
        p = self.una([("bot", "propio/bot:0.6.30", 1)], red, imagenes={1: {"creada": "2026-12-20T00:00:00Z"}}, cfg=cfg)
        self.assertEqual(p["estado"], "base renovada")

    def test_base_sin_cambios(self):
        cfg = config.validar(datos.config(self.tmp.name, componentes={"docker.io/propio/bot": {"base": "python:3.13-slim"}}))
        red = Red().hub("library/python", [tag("3.13-slim", "2026-12-01T00:00:00Z", H)])
        p = self.una([("bot", "propio/bot:0.6.30", 1)], red, imagenes={1: {"creada": "2026-12-20T00:00:00Z"}}, cfg=cfg)
        self.assertEqual((p["estado"], p["notas"]), ("propia", []))


class Sistema(Base):
    def test_resume_paquetes_y_reinicio(self):
        foto = censo.limpiar(datos.foto([], paquetes=[
            {"nombre": "openssl", "origen": "stable-security", "a": "2", "de": "1", "seguridad": True},
            {"nombre": "tzdata", "origen": "stable-updates", "a": "2", "de": "1", "seguridad": False}],
            kernels=["6.12.1-amd64", "6.12.9-amd64"]), AHORA, 900)
        s = analisis.sistema(foto)
        self.assertEqual((s["pendientes"], s["de_seguridad"], s["kernel_nuevo_instalado"]), (2, 1, True))

    def test_sin_dato_de_paquetes_no_es_cero(self):
        foto = censo.limpiar(datos.foto([], paquetes=None), AHORA, 900)
        foto["sistema"]["paquetes"] = None
        s = analisis.sistema(foto)
        self.assertIsNone(s["pendientes"])
        self.assertIsNone(s["de_seguridad"])


class Plazos(Base):
    def test_el_plazo_cuenta_desde_la_deteccion_y_depende_de_la_exposicion(self):
        c = {"expuesto": True}
        self.assertEqual(analisis.plazo(self.cfg, c, None, AHORA), {"clase": "ordinario", "vence": "2027-02-14T08:00:00Z"})
        self.assertEqual(analisis.plazo(self.cfg, c, {"altas": 2}, AHORA)["vence"], "2027-01-18T08:00:00Z")
        self.assertEqual(analisis.plazo(self.cfg, {"expuesto": False}, {"altas": 2}, AHORA)["vence"], "2027-01-22T08:00:00Z")

    def test_un_kev_sin_aplicabilidad_verificada_no_dispara_el_plazo_de_un_dia(self):
        c = {"expuesto": True}
        self.assertEqual(analisis.plazo(self.cfg, c, {"kev": ["CVE-2099-0001"], "kev_aplicable": False}, AHORA)["clase"], "alta_expuesta")
        self.assertEqual(analisis.plazo(self.cfg, c, {"kev": ["CVE-2099-0001"], "kev_aplicable": True}, AHORA)["clase"], "kev_expuesto")


if __name__ == "__main__":
    unittest.main()
