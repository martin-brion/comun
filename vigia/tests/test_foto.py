import json
import unittest

from vigia import censo, foto

from datos import AHORA, imagen_id

APT = """Listing...
tzdata/stable-updates 2027a-0 all [upgradable from: 2026c-0]
openssl/stable-security 3.5.1-1 amd64 [upgradable from: 3.5.0-1]
motor-contenedores/pila 5:29.9.3-1 amd64 [upgradable from: 5:29.8.2-1]
"""


def servidor(apt=(0, APT), docker=True, contenedores=None, imagenes=None):
    contenedores = contenedores if contenedores is not None else [{
        "Name": "/pila-db-1", "Image": imagen_id(1), "Created": "2027-01-01T00:00:00Z",
        "Config": {"Image": "postgres:16.15", "Env": ["CLAVE=no-deberia-salir"],
                   "Labels": {"com.docker.compose.project": "pila", "com.docker.compose.service": "db"}},
        "State": {"Status": "running", "Health": {"Status": "healthy"}}, "Mounts": [{"Source": "/datos/privados"}]}]
    imagenes = imagenes if imagenes is not None else [{
        "Id": imagen_id(1), "RepoTags": ["postgres:16.15"], "RepoDigests": ["postgres@sha256:" + "a" * 64],
        "Created": "2026-12-01T00:00:00Z",
        "Config": {"Labels": {"org.opencontainers.image.version": "16.15", "otra.cosa": "no-deberia-salir"}}}]

    def correr(argv, tope=60):
        if argv[0] == "uname":
            return 0, "6.12.1-amd64\n"
        if argv[0] == "apt":
            return apt
        if not docker:
            return None, ""
        if argv[:2] == ["docker", "version"]:
            return 0, "29.0.0\n"
        if argv[:2] == ["docker", "ps"]:
            return 0, "\n".join(c["Image"] for c in contenedores) + "\n"
        if argv[:3] == ["docker", "image", "inspect"]:
            return 0, json.dumps(imagenes)
        if argv[:2] == ["docker", "inspect"]:
            return 0, json.dumps(contenedores)
        return 1, ""

    archivos = {"/etc/os-release": 'ID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian GNU/Linux 13"\n',
                "/etc/apt/apt.conf.d/20auto-upgrades": 'APT::Periodic::Unattended-Upgrade "1";\n'}
    return dict(correr=correr, leer=archivos.get, listar=lambda r: ["config-6.12.1-amd64", "vmlinuz-6.12.1-amd64"],
                mtime=lambda r: AHORA - 100, ahora=AHORA)


class Foto(unittest.TestCase):
    def test_mide_sistema_contenedores_e_imagenes(self):
        f = foto.tomar(**servidor())
        s = f["sistema"]
        self.assertEqual((s["id"], s["version"], s["kernel_en_uso"], s["kernels"]), ("debian", "13", "6.12.1-amd64", ["6.12.1-amd64"]))
        self.assertTrue(s["desatendidas"])
        self.assertFalse(s["reinicio_pedido"])
        self.assertEqual([(p["nombre"], p["de"], p["a"], p["seguridad"]) for p in s["paquetes"]],
                         [("tzdata", "2026c-0", "2027a-0", False), ("openssl", "3.5.0-1", "3.5.1-1", True),
                          ("motor-contenedores", "5:29.8.2-1", "5:29.9.3-1", False)])
        self.assertEqual(f["contenedores"][0]["nombre"], "pila-db-1")
        self.assertEqual(f["contenedores"][0]["imagen_ref"], "postgres:16.15")
        self.assertEqual(f["imagenes"][imagen_id(1)]["digests"], ["postgres@sha256:" + "a" * 64])
        self.assertEqual(f["errores"], [])

    def test_no_saca_entorno_montajes_ni_rotulos_ajenos(self):
        crudo = json.dumps(foto.tomar(**servidor()))
        for prohibido in ("no-deberia-salir", "/datos/privados", "CLAVE", "otra.cosa"):
            self.assertNotIn(prohibido, crudo)

    def test_sin_gestor_de_paquetes_queda_sin_dato_no_en_cero(self):
        f = foto.tomar(**servidor(apt=(None, "")))
        self.assertIsNone(f["sistema"]["paquetes"])
        self.assertTrue(any("paquetes" in e for e in f["errores"]))

    def test_sin_docker_los_contenedores_quedan_sin_dato(self):
        f = foto.tomar(**servidor(docker=False))
        self.assertIsNone(f["docker"]["version"])
        self.assertTrue(any("docker" in e for e in f["errores"]))
        limpia = censo.limpiar(f, AHORA, 900)
        self.assertIsNone(limpia["contenedores"])          # sin dato: nunca «no corre nada»

    def test_la_foto_propia_pasa_la_limpieza(self):
        limpia = censo.limpiar(json.loads(json.dumps(foto.tomar(**servidor()))), AHORA, 900)
        self.assertEqual(limpia["contenedores"][0]["servicio"], "db")
        self.assertEqual(limpia["sistema"]["paquetes"][1]["seguridad"], True)


class Limpieza(unittest.TestCase):
    def cruda(self):
        return json.loads(json.dumps(foto.tomar(**servidor())))

    def test_una_foto_vieja_o_del_futuro_no_vale(self):
        for corrimiento in (-901, 901):
            with self.assertRaisesRegex(censo.FotoError, "vencida"):
                censo.limpiar(self.cruda(), AHORA + corrimiento, 900)

    def test_esquema_desconocido(self):
        f = self.cruda()
        f["esquema"] = 2
        with self.assertRaises(censo.FotoError):
            censo.limpiar(f, AHORA, 900)

    def test_un_campo_de_mas_no_pasa(self):
        f = self.cruda()
        f["contenedores"][0]["comando"] = "rm -rf /"
        f["orden"] = "ejecutar"
        limpia = censo.limpiar(f, AHORA, 900)
        self.assertNotIn("comando", limpia["contenedores"][0])
        self.assertNotIn("orden", limpia)

    def test_texto_con_caracteres_de_control_o_demasiado_largo_frena(self):
        for valor in ("a\nb", "x" * 301, "a`b", "a$(b)", {"x": 1}):
            f = self.cruda()
            f["contenedores"][0]["nombre"] = valor
            with self.assertRaises(censo.FotoError):
                censo.limpiar(f, AHORA, 900)

    def test_un_lector_que_falla_deja_el_servidor_en_desconocido(self):
        def remota(lector):
            raise censo.FotoError("el lector no respondió")
        r = censo.tomar({"nombre": "hoja", "lector": ["leer", "hoja"]}, AHORA, 900, remota=remota)
        self.assertEqual((r["estado"], r["foto"]), ("desconocido", None))
        self.assertIn("no respondió", r["motivo"])

    def test_el_lector_remoto_recibe_el_archivo_de_la_foto(self):
        visto = {}

        def remota(lector):
            visto["lector"] = lector
            return self.cruda()
        r = censo.tomar({"nombre": "hoja", "lector": ["leer", "hoja"]}, AHORA, 900, remota=remota)
        self.assertEqual((r["estado"], visto["lector"]), ("ok", ["leer", "hoja"]))


if __name__ == "__main__":
    unittest.main()
