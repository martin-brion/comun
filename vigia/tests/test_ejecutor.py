import json
import os
import tempfile
import time
import unittest

from vigia import censo, config, corrida, ejecutor, estado, fuentes

import datos
from datos import AHORA, H, H2, H3, H4, Red, tag

VIEJA = "2026-12-01T00:00:00Z"
DIA = 86400
PG = "docker.io/library/postgres"
WEB = "docker.io/ejemplo/web"
ACCIONES = {"respaldo_vigente": ["accion", "respaldo"],
            "aplicar_imagen": ["accion", "aplicar", "{servicio}", "{referencia}"],
            "verificar": ["accion", "verificar", "{servicio}"],
            "volver_imagen": ["accion", "volver", "{servicio}", "{referencia}"]}


class Corte(Exception):
    """Simula que el proceso muere en medio de una acción."""


class Servidor:
    """Un servidor de mentira: servicios de compose con su imagen, y acciones que los cambian."""

    def __init__(self, servicios):
        self.servicios = dict(servicios)            # nombre -> referencia con huella
        self.llamadas = []
        self.respaldo = True
        self.falla_aplicar = set()                  # servicios cuyo aplicar sale mal
        self.falla_verificar = set()                # servicios que no verifican con la imagen NUEVA
        self.falla_volver = set()
        self.corte = None                           # ("antes" | "despues", servicio): muere al aplicar
        self.medible = True
        self.originales = dict(servicios)

    def tomar(self, servidor, ahora, vigencia):
        if not self.medible:
            return {"nombre": servidor["nombre"], "estado": "desconocido", "motivo": "el lector no respondió", "foto": None}
        contenedores, imagenes = [], {}
        for i, (nombre, ref) in enumerate(sorted(self.servicios.items())):
            huella = ref.rpartition("@")[2]
            numero = int(huella[-8:], 16)
            contenedores.append((nombre, ref, numero))
            imagenes[numero] = {"digests": [ref.partition(":")[0].replace("docker.io/library/", "").replace("docker.io/", "")
                                            + "@" + huella], "creada": "2026-11-01T00:00:00Z"}
        cruda = datos.foto(contenedores, imagenes)
        cruda["tomada"] = ahora
        return {"nombre": servidor["nombre"], "estado": "ok", "motivo": "", "foto": censo.limpiar(cruda, ahora, vigencia)}

    def correr(self, argv, tope):
        self.llamadas.append(argv[1:])
        orden, resto = argv[1], argv[2:]
        if orden == "respaldo":
            return 0 if self.respaldo else 1
        if orden == "aplicar":
            servicio, ref = resto
            if self.corte == ("antes", servicio):
                raise Corte()
            if servicio in self.falla_aplicar:
                return 1
            self.servicios[servicio] = ref
            if self.corte == ("despues", servicio):
                raise Corte()
            return 0
        if orden == "verificar":
            nueva = self.servicios[resto[0]] != self.originales[resto[0]]
            return 1 if (nueva and resto[0] in self.falla_verificar) else 0
        if orden == "volver":
            servicio, ref = resto
            if servicio in self.falla_volver:
                return 1
            self.servicios[servicio] = ref
            return 0
        return 1


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = self.config()
        self.s = Servidor({"db": f"postgres:16.15@{H}"})
        self.red = Red().hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.ahora = AHORA                           # viernes 08:00 UTC

    def config(self, **cambios):
        base = datos.config(self.tmp.name, componentes={
            PG: {"nivel": "patch", "playbook": "imagen"}, WEB: {"nivel": "patch", "playbook": "imagen"}},
            acciones={"principal": ACCIONES}, playbooks=["imagen"], espera_salud_s=20,
            ventana={"dias": [4], "desde": "07:00", "hasta": "09:00"})
        base.update(cambios)
        return config.validar(base)

    def reloj(self):
        self.ahora += 1
        return self.ahora

    def aplicar(self, presente=False, cfg=None, s=None):
        s = s or self.s
        return ejecutor.aplicar(cfg or self.cfg, presente=presente, tomar=s.tomar, pedir=self.red, correr=s.correr,
                                reloj=self.reloj, dormir=lambda n: setattr(self, "ahora", self.ahora + n), local=time.gmtime)

    def db(self):
        db = estado.abrir(self.cfg["estado_dir"])
        self.addCleanup(db.close)
        return db

    def candidata(self, propuesta="16.16"):
        return dict(self.db().execute("SELECT * FROM candidatas WHERE propuesta = ?", (propuesta,)).fetchone())

    def ordenes(self, s=None):
        return [l[0] for l in (s or self.s).llamadas]


class Compuertas(Base):
    def test_fuera_de_la_ventana_no_hace_nada(self):
        self.ahora = AHORA + 3 * 3600                # 11:00
        acta = self.aplicar()
        self.assertEqual((acta["motivo"], acta["pasos"], self.s.llamadas), ("fuera de la ventana", [], []))

    def test_sin_ventana_definida_nunca_aplica_solo(self):
        acta = self.aplicar(cfg=self.config(ventana=None))
        self.assertEqual(acta["motivo"], "fuera de la ventana")

    def test_el_estreno_de_un_tipo_de_accion_pide_al_dueno_presente(self):
        acta = self.aplicar()
        (paso,) = acta["pasos"]
        self.assertEqual(paso["resultado"], "bloqueada")
        frenos = [n for n, ok, _ in paso["compuertas"] if not ok]
        self.assertEqual(frenos, ["estreno"])
        self.assertEqual(self.s.llamadas, [])        # ni siquiera consultó el respaldo
        self.assertEqual(self.s.servicios["db"], f"postgres:16.15@{H}")

    def test_sin_respaldo_vigente_no_aplica(self):
        self.s.respaldo = False
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["respaldo"])
        self.assertEqual(self.ordenes(), ["respaldo"])

    def test_una_pieza_sin_tipo_de_accion_habilitado_no_se_toca(self):
        cfg = self.config(playbooks=[])
        (paso,) = self.aplicar(presente=True, cfg=cfg)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["tipo de acción"])
        self.assertEqual(self.s.llamadas, [])

    def test_la_compuerta_de_version_manda_antes_que_todo(self):
        self.red.hub("library/postgres", [tag("16.16", "2027-01-14T00:00:00Z", H2), tag("16.15", VIEJA, H)])
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertEqual(paso["compuertas"][0], ("versión", False, "espera: madura el 2027-01-21"))
        self.assertEqual(self.s.llamadas, [])

    def test_sin_huella_de_la_version_propuesta_no_se_puede_fijar(self):
        self.red.hub("library/postgres", [tag("16.16", VIEJA), tag("16.15", VIEJA, H)])
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertIn("imagen fija", [n for n, ok, _ in paso["compuertas"] if not ok])
        self.assertEqual(self.s.llamadas, [])

    def test_en_pausa_no_hace_nada(self):
        os.makedirs(self.cfg["estado_dir"], exist_ok=True)
        open(os.path.join(self.cfg["estado_dir"], "PAUSA"), "w").close()
        acta = self.aplicar(presente=True)
        self.assertIn("en pausa", acta["motivo"])
        self.assertEqual(self.s.llamadas, [])


class Aplicar(Base):
    def test_aplica_verifica_y_cierra(self):
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.s.llamadas, [["respaldo"], ["aplicar", "db", f"{PG}:16.16@{H2}"], ["verificar", "db"]])
        self.assertEqual(self.s.servicios["db"], f"{PG}:16.16@{H2}")
        c = self.candidata()
        self.assertEqual((c["estado"], c["motivo"]), ("cerrada", "aplicada por el vigía y verificada"))
        (e,) = estado.diario(self.db())
        self.assertEqual((e["paso"], e["resultado"], e["datos"]),
                         ("aplicar", "hecho", {"servicio": "db", "de": f"postgres:16.15@{H}", "a": f"{PG}:16.16@{H2}"}))
        with open(acta["ficha"], encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("postgres: 16.15 → 16.16 · aplicada", texto)
        self.assertIn("pasa · respaldo: verificado", texto)

    def test_despues_del_estreno_aplica_solo_en_la_ventana(self):
        self.aplicar(presente=True)
        self.red.hub("library/postgres", [tag("16.17", VIEJA, H3), tag("16.16", VIEJA, H2)])
        self.ahora = AHORA + 7 * DIA                 # el viernes siguiente, 08:00
        acta = self.aplicar()
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.s.servicios["db"], f"{PG}:16.17@{H3}")

    def test_la_intencion_queda_escrita_antes_del_efecto(self):
        vistos = []
        original = self.s.correr

        def espiar(argv, tope):
            if argv[1] == "aplicar":
                vistos.append([(e["paso"], e["resultado"]) for e in estado.diario(self.db())])
            return original(argv, tope)
        self.s.correr = espiar
        self.aplicar(presente=True)
        self.assertEqual(vistos, [[("aplicar", None)]])

    def test_una_imagen_que_usan_dos_servicios_se_aplica_en_los_dos(self):
        self.s = Servidor({"db": f"postgres:16.15@{H}", "db-chat": f"postgres:16.15@{H}"})
        self.aplicar(presente=True)
        self.assertEqual(self.ordenes(), ["respaldo", "aplicar", "verificar", "aplicar", "verificar"])
        self.assertEqual(set(self.s.servicios.values()), {f"{PG}:16.16@{H2}"})

    def test_tope_por_ventana(self):
        self.s = Servidor({"db": f"postgres:16.15@{H}", "web": f"ejemplo/web:1.2.3@{H3}"})
        self.red.hub("ejemplo/web", [tag("1.2.4", VIEJA, H4), tag("1.2.3", VIEJA, H3)])
        acta = self.aplicar(presente=True, cfg=self.config(max_por_ventana=1))
        self.assertEqual([p["resultado"] for p in acta["pasos"]], ["aplicada", "bloqueada"])
        self.assertEqual(acta["pasos"][1]["compuertas"][0][0], "tope de la ventana")

    def test_un_servidor_por_ventana(self):
        cfg = self.config(servidores=[{"nombre": "principal"}, {"nombre": "segundo"}],
                          acciones={"principal": ACCIONES, "segundo": ACCIONES})
        primero = self.aplicar(presente=True, cfg=cfg)
        mismo_dia = self.aplicar(presente=True, cfg=cfg)
        self.ahora = AHORA + 7 * DIA
        otra = self.aplicar(presente=True, cfg=cfg)
        self.assertEqual((primero["servidor"], mismo_dia["servidor"], otra["servidor"]), ("principal", "principal", "segundo"))


class Fallas(Base):
    def test_si_no_verifica_vuelve_atras_y_queda_como_estaba(self):
        self.s.falla_verificar = {"db"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.ordenes(), ["respaldo", "aplicar", "verificar", "volver", "verificar"])
        self.assertEqual(self.s.llamadas[3], ["volver", "db", f"postgres:16.15@{H}"])
        self.assertEqual(self.s.servicios["db"], f"postgres:16.15@{H}")
        self.assertEqual(self.candidata()["estado"], "esperando")
        self.assertEqual([(e["paso"], e["resultado"]) for e in estado.diario(self.db())], [("aplicar", "fallo"), ("volver", "hecho")])
        self.assertFalse(estado.estrenado(self.db(), "imagen"))        # un estreno fallido no cuenta
        self.assertEqual(estado.incidentes(self.db()), [])

    def test_un_comando_que_sale_bien_pero_deja_la_imagen_vieja_no_cuenta(self):
        original = self.s.correr

        def miente(argv, tope):
            if argv[1] == "aplicar":
                self.s.llamadas.append(argv[1:])
                return 0                                               # dice que aplicó y no cambió nada
            return original(argv, tope)
        self.s.correr = miente
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")

    def test_a_la_segunda_falla_se_escala_y_avisa_una_sola_vez(self):
        self.s.falla_verificar = {"db"}
        self.aplicar(presente=True)
        self.ahora = AHORA + 7 * DIA
        acta = self.aplicar(presente=True)
        self.assertEqual(self.candidata()["estado"], "escalada")
        (i,) = estado.incidentes(self.db())
        self.assertEqual(i["clase"], "no entró")
        self.assertEqual(len(acta["avisos"]), 1)
        with open(acta["avisos"][0], encoding="utf-8") as f:
            self.assertIn("falló 2 veces", f.read())
        self.ahora = AHORA + 7 * DIA + 600
        tercera = self.aplicar(presente=True)
        self.assertEqual(tercera["avisos"], [])                         # el recordatorio es diario, no por pasada
        self.assertIn("sin escalar", [n for n, ok, _ in tercera["pasos"][0]["compuertas"] if not ok])
        self.assertEqual(self.ordenes().count("aplicar"), 2)            # no lo intenta una tercera vez

    def test_si_la_vuelta_atras_falla_frena_la_ventana_y_avisa(self):
        self.s = Servidor({"db": f"postgres:16.15@{H}", "web": f"ejemplo/web:1.2.3@{H3}"})
        self.red.hub("ejemplo/web", [tag("1.2.4", VIEJA, H4), tag("1.2.3", VIEJA, H3)])
        self.s.falla_verificar = {"web"}                                 # la primera de la ventana, por orden
        self.s.falla_volver = {"web"}
        acta = self.aplicar(presente=True)
        self.assertEqual(len(acta["pasos"]), 1)                          # no siguió con la otra candidata
        self.assertEqual(acta["pasos"][0]["resultado"], "falló y la vuelta atrás también")
        self.assertIn("se frenó la ventana", acta["motivo"])
        self.assertEqual(self.candidata("1.2.4")["estado"], "escalada")
        self.assertEqual(self.candidata("16.16")["estado"], "detectada")
        self.assertEqual([i["clase"] for i in estado.incidentes(self.db())], ["vuelta atrás fallida"])
        self.assertEqual(len(acta["avisos"]), 1)
        self.assertEqual(self.s.servicios["db"], f"postgres:16.15@{H}")

    def test_el_aviso_usa_el_comando_de_la_instancia(self):
        self.s.falla_verificar = {"db"}
        self.s.falla_volver = {"db"}
        acta = self.aplicar(presente=True, cfg=self.config(aviso=["accion", "avisar"]))
        self.assertEqual(self.s.llamadas[-1], ["avisar", acta["avisos"][0]])


class Cortes(Base):
    def cortar(self, cuando):
        self.s.corte = (cuando, "db")
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.corte = None
        self.assertEqual([(e["paso"], e["resultado"]) for e in estado.a_medias(self.db())], [("aplicar", None)])
        self.assertFalse(os.path.exists(os.path.join(self.cfg["estado_dir"], "corrida.candado")))
        self.s.llamadas.clear()

    def test_corte_despues_de_aplicar_no_repite_la_accion(self):
        self.cortar("despues")
        acta = self.aplicar(presente=True)
        self.assertEqual(self.s.llamadas, [])                            # midió; no volvió a aplicar
        (e,) = estado.diario(self.db())
        self.assertEqual((e["resultado"], e["detalle"]), ("hecho", "recuperado tras un corte: ya corría la versión nueva"))
        self.assertEqual(self.candidata()["estado"], "cerrada")
        self.assertEqual(acta["pasos"], [])

    def test_corte_antes_de_aplicar_deja_la_candidata_lista_para_la_proxima(self):
        self.cortar("antes")
        acta = self.aplicar(presente=True)
        primera = estado.diario(self.db())[0]
        self.assertEqual(primera["resultado"], "no aplicado")
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")      # medido que no entró, se aplica de nuevo
        self.assertEqual(self.s.servicios["db"], f"{PG}:16.16@{H2}")

    def test_corte_sin_poder_medir_es_ambiguo_no_se_toca_y_avisa(self):
        self.cortar("despues")
        self.s.medible = False
        acta = self.aplicar(presente=True)
        self.assertEqual(self.s.llamadas, [])
        self.assertIn("a medias", acta["motivo"])
        self.assertEqual(self.candidata()["estado"], "escalada")
        self.assertEqual([i["clase"] for i in estado.incidentes(self.db())], ["a medias"])
        self.assertEqual(estado.diario(self.db())[0]["resultado"], "ambiguo")

    def test_corte_con_el_servicio_en_otra_imagen_es_ambiguo(self):
        self.cortar("despues")
        self.s.servicios["db"] = f"postgres:16.99@{H4}"
        self.aplicar(presente=True)
        self.assertEqual(self.candidata()["estado"], "escalada")
        self.assertEqual(self.s.llamadas, [])


class Acciones(Base):
    def test_los_valores_se_validan_antes_de_armar_el_comando(self):
        llamadas = []
        correr = lambda argv, tope: llamadas.append(argv) or 0
        bien = {"servicio": "db", "referencia": f"{PG}:16.16@{H2}"}
        self.assertTrue(ejecutor.accion(self.cfg, "principal", "aplicar_imagen", bien, correr))
        for mal in ({"servicio": "db; rm -rf /", "referencia": bien["referencia"]}, {"servicio": "db", "referencia": "postgres:16.16"},
                    {"servicio": "db", "referencia": bien["referencia"] + " --privileged"}, {"servicio": "../x", "referencia": bien["referencia"]},
                    {"servicio": "db"}):
            self.assertFalse(ejecutor.accion(self.cfg, "principal", "aplicar_imagen", mal, correr), mal)
        self.assertEqual(len(llamadas), 1)
        self.assertIsNone(ejecutor.accion(self.cfg, "otro", "aplicar_imagen", bien, correr))

    def test_la_configuracion_de_acciones_es_cerrada(self):
        for cambio in ({"acciones": {"principal": {"borrar_todo": ["rm"]}}},
                       {"acciones": {"principal": {"verificar": "accion verificar {servicio}"}}},
                       {"acciones": {"principal": {"verificar": ["accion", "{referencia}"]}}},
                       {"acciones": {"principal": {"verificar": ["accion", "{servicio!r}"]}}},
                       {"acciones": {"principal": {"verificar": ["accion", "{servicio"]}}},
                       {"acciones": {"fantasma": ACCIONES}}, {"playbooks": ["formatear"]},
                       {"componentes": {PG: {"playbook": "formatear"}}}, {"ventana": {"dias": [7], "desde": "02:00", "hasta": "05:00"}},
                       {"ventana": {"dias": [1], "desde": "05:00", "hasta": "02:00"}}, {"max_por_ventana": 0}, {"aviso": "mail"}):
            with self.assertRaises(config.ConfigError, msg=cambio):
                self.config(**cambio)


class Plazo(Base):
    def test_un_plazo_vencido_abre_un_incidente_y_se_cierra_con_la_candidata(self):
        observar = lambda t: corrida.correr(self.cfg, t, tomar=self.s.tomar, pedir=self.red)
        self.assertEqual(observar(AHORA)["avisos"], [])
        r = observar(AHORA + 31 * DIA)
        self.assertEqual(len(r["avisos"]), 1)
        with open(r["avisos"][0], encoding="utf-8") as f:
            self.assertIn("venció el 2027-02-14", f.read())
        self.s.servicios["db"] = f"{PG}:16.16@{H2}"
        observar(AHORA + 32 * DIA)
        self.assertEqual(estado.incidentes(self.db()), [])


if __name__ == "__main__":
    unittest.main()
