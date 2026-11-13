import json
import unittest

from vigia import fuentes

from datos import H, H2, H3, Red, tag

REG = "registro.ejemplo.test"


def registro(tags, con_token=True, creada="2027-01-05T10:00:00Z"):
    """Un registro OCI de mentira: pide token anónimo, lista etiquetas y sirve manifiesto y configuración."""
    def protegido(cuerpo, cabeceras=None):
        def responder(url, cab):
            if con_token and cab.get("Authorization") != "Bearer ficha-anonima":
                return 401, {"www-authenticate": f'Bearer realm="https://{REG}/token",service="{REG}",scope="x"'}, b""
            return 200, cabeceras or {}, json.dumps(cuerpo).encode()
        return responder
    return Red({
        f"https://{REG}/token": (200, {}, json.dumps({"token": "ficha-anonima"}).encode()),
        f"https://{REG}/v2/equipo/forja/tags/list": protegido({"tags": tags}),
        f"https://{REG}/v2/equipo/forja/manifests/15.0.11-rootless": protegido(
            {"manifests": [{"digest": H2, "platform": {"os": "linux", "architecture": "arm64"}},
                           {"digest": H3, "platform": {"os": "linux", "architecture": "amd64"}}]},
            {"docker-content-digest": H}),
        f"https://{REG}/v2/equipo/forja/manifests/{H3}": protegido({"config": {"digest": H2}}),
        f"https://{REG}/v2/equipo/forja/blobs/{H2}": protegido({"created": creada}),
    })


class DockerHub(unittest.TestCase):
    def test_trae_nombre_fecha_y_las_dos_huellas(self):
        red = Red().hub("library/postgres", [tag("16.16", "2027-01-10T00:00:00Z", H, H2), tag("16.15", "2026-12-01T00:00:00Z", H3)])
        r = fuentes.docker_hub("library/postgres", red)
        self.assertEqual((r["estado"], r["hay_mas"]), ("ok", False))
        self.assertEqual(r["etiquetas"][0], {"nombre": "16.16", "publicada": "2027-01-10T00:00:00Z", "indice": H, "digests": sorted([H, H2])})

    def test_avisa_si_hay_mas_etiquetas_que_las_consultadas(self):
        red = Red().hub("library/postgres", [tag("16.16")], siguiente="https://hub.docker.com/siguiente")
        self.assertTrue(fuentes.docker_hub("library/postgres", red)["hay_mas"])

    def test_una_falla_es_desconocido_nunca_una_lista_vacia_buena(self):
        def caida(url, cabeceras=None, **_):
            raise fuentes.FuenteError("URLError")
        for red in (caida, Red(), Red({"https://hub.docker.com/v2/repositories/library/postgres/tags": (200, {}, b"<html>")}),
                    Red({"https://hub.docker.com/v2/repositories/library/postgres/tags": (200, {}, b'{"otra": 1}')})):
            r = fuentes.docker_hub("library/postgres", red)
            self.assertEqual((r["estado"], r["etiquetas"]), ("desconocido", []))
            self.assertTrue(r["motivo"])

    def test_un_nombre_raro_no_llega_a_la_red(self):
        red = Red()
        self.assertEqual(fuentes.docker_hub("../../x?y=1", red)["estado"], "desconocido")
        self.assertEqual(red.pedidos, [])


class RegistroOCI(unittest.TestCase):
    def test_lista_etiquetas_con_token_anonimo(self):
        red = registro(["15.0.9-rootless", "15.0.11-rootless", "17.0.1-rootless"])
        r = fuentes.registro_oci(REG, "equipo/forja", red)
        self.assertEqual(r["estado"], "ok")
        self.assertEqual([e["nombre"] for e in r["etiquetas"]], ["15.0.9-rootless", "15.0.11-rootless", "17.0.1-rootless"])
        token = [u for u, _ in red.pedidos if "/token?" in u][0]
        self.assertIn("scope=repository%3Aequipo%2Fforja%3Apull", token)

    def test_fecha_y_huella_de_una_etiqueta(self):
        d = fuentes.detalle_oci(REG, "equipo/forja", "15.0.11-rootless", registro([]))
        self.assertEqual(d, {"publicada": "2027-01-05T10:00:00Z", "indice": H, "digests": sorted([H, H3])})

    def test_un_registro_que_pide_credencial_por_http_llano_no_se_sigue(self):
        red = Red({f"https://{REG}/v2/equipo/forja/tags/list": (401, {"www-authenticate": 'Bearer realm="http://otro.test/t"'}, b"")})
        r = fuentes.registro_oci(REG, "equipo/forja", red)
        self.assertEqual(r["estado"], "desconocido")
        self.assertEqual(len(red.pedidos), 1)

    def test_sin_manifiesto_no_inventa_fecha(self):
        self.assertIsNone(fuentes.detalle_oci(REG, "equipo/forja", "9.9.9", registro([])))

    def test_pedir_solo_acepta_https(self):
        with self.assertRaises(fuentes.FuenteError):
            fuentes.pedir("http://registro.ejemplo.test/v2/")
        with self.assertRaises(fuentes.FuenteError):
            fuentes.pedir("file:///etc/passwd")


class Kev(unittest.TestCase):
    def test_lee_los_cve(self):
        red = Red({fuentes.KEV: (200, {}, json.dumps({"vulnerabilities": [{"cveID": "CVE-2099-0001"}, {"cveID": "CVE-2099-0002"}]}).encode())})
        r = fuentes.kev(red)
        self.assertEqual((r["estado"], r["cves"]), ("ok", {"CVE-2099-0001", "CVE-2099-0002"}))

    def test_caida_o_vacia_es_desconocido(self):
        self.assertEqual(fuentes.kev(Red())["estado"], "desconocido")
        vacia = Red({fuentes.KEV: (200, {}, b'{"vulnerabilities": []}')})
        self.assertEqual(fuentes.kev(vacia)["estado"], "desconocido")


if __name__ == "__main__":
    unittest.main()
