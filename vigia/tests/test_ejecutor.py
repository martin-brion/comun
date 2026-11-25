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
CON_DATOS = dict(ACCIONES, respaldar_datos=["accion", "respaldar", "{servicio}", "{marca}"],
                 restaurar_datos=["accion", "restaurar", "{servicio}", "{marca}"])


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
        # Los datos de cada servicio: con qué imagen se escribieron por última vez. Una imagen nueva los migra al
        # arrancar, y la anterior ya no los entiende.
        self.con_datos = set()                      # servicios que migran sus datos al cambiar de imagen
        self.formato = dict(servicios)
        self.copias = {}                          # (servicio, marca) -> formato copiado
        self.detenidos = set()
        self.falla_respaldar = set()
        self.falla_restaurar = set()
        self.corta = {}                             # "respaldar" | "restaurar" -> "antes" | "despues": muere ahí

    def tomar(self, servidor, ahora, vigencia):
        if not self.medible:
            return {"nombre": servidor["nombre"], "estado": "desconocido", "motivo": "el lector no respondió", "foto": None}
        contenedores, imagenes = [], {}
        for i, (nombre, ref) in enumerate(sorted(self.servicios.items())):
            if nombre in self.detenidos:
                continue
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
            if servicio in self.con_datos:
                self.formato[servicio] = ref
            self.detenidos.discard(servicio)
            if self.corte == ("despues", servicio):
                raise Corte()
            return 0
        if orden == "verificar":
            if resto[0] in self.detenidos or (resto[0] in self.con_datos
                                              and self.formato[resto[0]] != self.servicios[resto[0]]):
                return 1                            # detenido, o una imagen sobre datos de otra versión
            nueva = self.servicios[resto[0]] != self.originales[resto[0]]
            return 1 if (nueva and resto[0] in self.falla_verificar) else 0
        if orden == "volver":
            servicio, ref = resto
            if servicio in self.falla_volver:
                return 1
            self.servicios[servicio] = ref           # la imagen vuelve; los datos quedan como estaban
            self.detenidos.discard(servicio)
            return 0
        if orden in ("respaldar", "restaurar"):
            servicio, marca = resto
            if self.corta.get(orden) == "antes":
                raise Corte()
            self.detenidos.add(servicio)             # las dos dejan el servicio detenido
            if self.corta.get(orden) == "despues":
                raise Corte()
            if orden == "respaldar":
                if servicio in self.falla_respaldar:
                    return 1
                self.copias[(servicio, marca)] = self.formato[servicio]
                return 0
            if servicio in self.falla_restaurar or (servicio, marca) not in self.copias:
                return 1
            self.formato[servicio] = self.copias[(servicio, marca)]
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
        datos.kev(self.red)
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
                         ("aplicar", "hecho", {"servicio": "db", "proyecto": "pila", "de": f"postgres:16.15@{H}",
                                                "a": f"{PG}:16.16@{H2}"}))
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
        corrida.Candado(self.cfg["estado_dir"]).soltar()           # el corte no dejó el candado tomado
        self.s.llamadas.clear()

    def test_corte_despues_de_aplicar_no_repite_la_accion(self):
        self.cortar("despues")
        acta = self.aplicar(presente=True)
        self.assertEqual(self.s.llamadas, [["verificar", "db"]])         # midió y verificó; no volvió a aplicar
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


class Datos(Base):
    """Una pieza que migra sus datos al actualizarse: copia antes, y la vuelta atrás repone esa copia."""

    def setUp(self):
        super().setUp()
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "datos"}},
                               acciones={"principal": CON_DATOS}, playbooks=["datos"])
        self.s.con_datos = {"db"}

    def pasos(self):
        return [(e["paso"], e["resultado"]) for e in estado.diario(self.db())]

    def test_copia_antes_de_aplicar_y_cierra(self):
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.ordenes(), ["respaldo", "respaldar", "aplicar", "verificar"])
        marca = self.s.llamadas[1][2]
        self.assertRegex(marca, r"^m\d{10}$")
        self.assertEqual(self.s.llamadas[1], ["respaldar", "db", marca])
        self.assertEqual(self.pasos(), [("respaldar", "hecho"), ("aplicar", "hecho")])
        self.assertEqual(estado.diario(self.db())[1]["datos"]["marca"], marca)
        self.assertEqual((self.s.servicios["db"], self.s.formato["db"]), (f"{PG}:16.16@{H2}",) * 2)
        self.assertTrue(estado.estrenado(self.db(), "datos"))
        self.assertFalse(estado.estrenado(self.db(), "imagen"))

    def test_la_copia_queda_escrita_en_el_diario_antes_de_hacerse(self):
        vistos = []
        original = self.s.correr

        def espiar(argv, tope):
            if argv[1] in ("respaldar", "restaurar"):
                vistos.append(self.pasos())
            return original(argv, tope)
        self.s.correr = espiar
        self.s.falla_verificar = {"db"}
        self.aplicar(presente=True)
        self.assertEqual(vistos, [[("respaldar", None)],
                                  [("respaldar", "hecho"), ("aplicar", "fallo"), ("restaurar", None)]])

    def test_si_no_verifica_repone_la_copia_de_ese_intento_y_despues_vuelve(self):
        self.s.falla_verificar = {"db"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.ordenes(), ["respaldo", "respaldar", "aplicar", "verificar", "restaurar", "volver", "verificar"])
        self.assertEqual(self.s.llamadas[4], ["restaurar", "db", self.s.llamadas[1][2]])     # la misma marca, no «la última»
        self.assertEqual((self.s.servicios["db"], self.s.formato["db"]), (f"postgres:16.15@{H}",) * 2)
        self.assertEqual(self.s.detenidos, set())
        self.assertEqual(self.pasos(), [("respaldar", "hecho"), ("aplicar", "fallo"), ("restaurar", "hecho"), ("volver", "hecho")])
        self.assertEqual(self.candidata()["estado"], "esperando")
        self.assertFalse(estado.estrenado(self.db(), "datos"))

    def test_con_el_tipo_imagen_esa_misma_pieza_no_podria_volver(self):
        # Lo que el tipo `datos` evita: volver solo la imagen deja una versión vieja sobre datos ya migrados.
        cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen"}})
        self.s.falla_verificar = {"db"}
        acta = self.aplicar(presente=True, cfg=cfg)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló y la vuelta atrás también")

    def test_si_la_copia_falla_no_aplica_y_levanta_lo_que_habia(self):
        self.s.falla_respaldar = {"db"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.ordenes(), ["respaldo", "respaldar", "volver", "verificar"])       # nunca aplicó
        self.assertEqual((self.s.servicios["db"], self.s.detenidos), (f"postgres:16.15@{H}", set()))
        self.assertEqual(self.pasos(), [("respaldar", "fallo"), ("volver", "hecho")])
        self.assertEqual(self.candidata()["estado"], "esperando")

    def test_dos_copias_fallidas_escalan(self):
        self.s.falla_respaldar = {"db"}
        self.aplicar(presente=True)
        self.ahora = AHORA + 7 * DIA
        self.aplicar(presente=True)
        self.assertEqual(self.candidata()["estado"], "escalada")
        self.assertEqual([i["clase"] for i in estado.incidentes(self.db())], ["no entró"])

    def test_si_la_restauracion_falla_no_levanta_la_imagen_vieja_y_frena(self):
        self.s.falla_verificar = {"db"}
        self.s.falla_restaurar = {"db"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló y la vuelta atrás también")
        self.assertEqual(self.ordenes()[-1], "restaurar")                # no llamó a volver sobre datos migrados
        self.assertIn("se frenó la ventana", acta["motivo"])
        self.assertEqual(self.candidata()["estado"], "escalada")
        self.assertEqual([i["clase"] for i in estado.incidentes(self.db())], ["vuelta atrás fallida"])

    def test_el_estreno_es_por_tipo_de_accion(self):
        estado.estrenar(self.db(), "imagen", "otra", AHORA)
        (paso,) = self.aplicar()["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["estreno"])
        self.assertEqual(self.s.llamadas, [])

    def test_sin_las_acciones_de_datos_definidas_no_se_toca(self):
        (paso,) = self.aplicar(presente=True, cfg=self.config(
            componentes={PG: {"nivel": "patch", "playbook": "datos"}}, playbooks=["datos"]))["pasos"]
        self.assertIn(("acciones", False, "faltan: respaldar_datos, restaurar_datos"), paso["compuertas"])
        self.assertEqual(self.s.llamadas, [])

    def cortar(self, orden, cuando):
        self.s.corta = {orden: cuando}
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.corta = {}
        self.s.llamadas.clear()

    def test_corte_antes_de_copiar_no_toco_nada_y_se_reintenta(self):
        self.cortar("respaldar", "antes")
        acta = self.aplicar(presente=True)
        self.assertEqual(estado.diario(self.db())[0]["resultado"], "no aplicado")
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")

    def test_corte_durante_la_copia_con_el_servicio_detenido_es_ambiguo(self):
        self.cortar("respaldar", "despues")
        acta = self.aplicar(presente=True)
        self.assertEqual(self.s.llamadas, [])                            # no lo levanta a ciegas
        self.assertIn("a medias", acta["motivo"])
        self.assertEqual(self.candidata()["estado"], "escalada")
        (i,) = estado.incidentes(self.db())
        self.assertIn("puede estar detenido", i["texto"])
        self.assertRegex(i["texto"], r"marca m\d{10}\.")

    def test_corte_durante_la_restauracion_es_siempre_ambiguo(self):
        self.s.falla_verificar = {"db"}
        self.cortar("restaurar", "antes")                                # el servicio sigue corriendo con la nueva
        self.aplicar(presente=True)
        self.assertEqual(self.s.llamadas, [])
        self.assertEqual(self.pasos()[-1], ("restaurar", "ambiguo"))
        self.assertEqual(self.candidata()["estado"], "escalada")


class Acciones(Base):
    def test_la_marca_de_una_copia_tambien_se_valida(self):
        cfg = self.config(acciones={"principal": CON_DATOS})
        llamadas = []
        correr = lambda argv, tope: llamadas.append(argv) or 0
        self.assertTrue(ejecutor.accion(cfg, "principal", "restaurar_datos", {"servicio": "db", "marca": "m1800000000"}, correr))
        for mal in ("ultima", "m1800000000; rm", "../m1800000000", "", None):
            self.assertFalse(ejecutor.accion(cfg, "principal", "restaurar_datos", {"servicio": "db", "marca": mal}, correr), mal)
        self.assertEqual(llamadas, [["accion", "restaurar", "db", "m1800000000"]])

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
        observar = lambda t: corrida.correr(self.cfg, t, tomar=self.s.tomar, pedir=datos.kev(self.red))
        self.assertEqual(observar(AHORA)["avisos"], [])
        r = observar(AHORA + 31 * DIA)
        self.assertEqual(len(r["avisos"]), 1)
        with open(r["avisos"][0], encoding="utf-8") as f:
            self.assertIn("venció el 2027-02-14", f.read())
        self.s.servicios["db"] = f"{PG}:16.16@{H2}"
        observar(AHORA + 32 * DIA)
        self.assertEqual(estado.incidentes(self.db()), [])


BOT = "docker.io/propio/bot"
BASE = "docker.io/library/python:3.13-slim"
CON_PROPIAS = {"respaldo_vigente": ["accion", "respaldo"],
               "construir_propia": ["accion", "construir", "{servicio}", "{base}"],
               "aplicar_propia": ["accion", "aplicar-propia", "{servicio}", "{base}"],
               "verificar": ["accion", "verificar", "{servicio}"],
               "volver_propia": ["accion", "volver-propia", "{servicio}", "{base}"]}


class ServidorPropio:
    """Imágenes propias armadas en el servidor: cada servicio corre la imagen armada sobre una base (por su huella)."""

    def __init__(self, servicios):
        self.servicios = dict(servicios)            # nombre -> huella de la base con que corre
        self.originales = dict(servicios)
        self.armadas = set(servicios.values())      # bases sobre las que hay una imagen armada y guardada
        self.llamadas = []
        self.respaldo = True
        self.falla_construir = set()
        self.falla_verificar = set()                # no verifica sobre una base distinta de la original
        self.falla_volver = set()
        self.corte = None                           # (orden, servicio): muere en esa acción, antes del efecto
        self.versiones = {}                         # servicio -> versión con que corre, si no es la de siempre
        self.version_al_volver = None               # volver_propia levanta otra versión armada sobre esa base

    def tomar(self, servidor, ahora, vigencia):
        contenedores, imagenes = [], {}
        for nombre, huella in sorted(self.servicios.items()):
            numero = int(huella[-8:], 16)
            contenedores.append((nombre, "propio/bot:0.6.30", numero))
            imagenes[numero] = {"creada": "2026-12-20T00:00:00Z", "tags": ["propio/bot:0.6.30"],
                                "etiquetas": {"org.opencontainers.image.base.name": BASE,
                                              "org.opencontainers.image.base.digest": huella,
                                              "org.opencontainers.image.version": self.versiones.get(nombre, "0.6.30")}}
        cruda = datos.foto(contenedores, imagenes)
        cruda["tomada"] = ahora
        return {"nombre": servidor["nombre"], "estado": "ok", "motivo": "", "foto": censo.limpiar(cruda, ahora, vigencia)}

    def correr(self, argv, tope):
        self.llamadas.append(argv[1:])
        orden, resto = argv[1], argv[2:]
        if self.corte == (orden, resto[0] if resto else None):
            raise Corte()
        if orden == "respaldo":
            return 0 if self.respaldo else 1
        if orden == "construir":
            servicio, base = resto
            if servicio in self.falla_construir:
                return 1
            self.armadas.add(base.rpartition("@")[2])
            return 0
        if orden in ("aplicar-propia", "volver-propia"):
            servicio, base = resto
            huella = base.rpartition("@")[2]
            if huella not in self.armadas or (orden == "volver-propia" and servicio in self.falla_volver):
                return 1
            self.servicios[servicio] = huella
            if orden == "volver-propia" and self.version_al_volver:
                self.versiones[servicio] = self.version_al_volver
            return 0
        if orden == "verificar":
            nueva = self.servicios[resto[0]] != self.originales[resto[0]]
            return 1 if (nueva and resto[0] in self.falla_verificar) else 0
        return 1


class Propias(Base):
    def setUp(self):
        super().setUp()
        self.cfg = self.config(componentes={BOT: {"base": "python:3.13-slim", "nivel": "patch", "playbook": "propia"}},
                               acciones={"principal": CON_PROPIAS}, playbooks=["propia"])
        self.s = ServidorPropio({"bot": H})
        self.red = Red().hub("library/python", [tag("3.13-slim", VIEJA, H2)])

    def pasos(self):
        return [(e["paso"], e["resultado"]) for e in estado.diario(self.db())]

    def test_arma_aplica_verifica_y_cierra(self):
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.s.llamadas, [["respaldo"], ["construir", "bot", f"{BASE}@{H2}"],
                                           ["aplicar-propia", "bot", f"{BASE}@{H2}"], ["verificar", "bot"]])
        self.assertEqual(self.s.servicios["bot"], H2)
        self.assertEqual(self.pasos(), [("construir", "hecho"), ("aplicar", "hecho")])
        self.assertTrue(estado.estrenado(self.db(), "propia"))
        self.assertFalse(estado.estrenado(self.db(), "imagen"))
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "cerrada")

    def test_la_corrida_siguiente_la_ve_al_dia(self):
        self.aplicar(presente=True)
        cambios = corrida.correr(self.cfg, AHORA + 60, tomar=self.s.tomar, pedir=self.red,
                                 escanear=lambda *a: {})["cambios"]
        self.assertEqual(cambios["nuevas"], [])
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "cerrada")

    def test_el_estreno_de_propia_pide_al_dueno_aunque_imagen_ya_se_estreno(self):
        estado.estrenar(self.db(), "imagen", "otra", AHORA)
        (paso,) = self.aplicar()["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["estreno"])
        self.assertEqual(self.s.llamadas, [])

    def test_si_no_se_arma_no_toca_lo_que_corre(self):
        self.s.falla_construir = {"bot"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.ordenes(), ["respaldo", "construir"])
        self.assertEqual(self.s.servicios["bot"], H)
        self.assertEqual(self.pasos(), [("construir", "fallo")])
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "esperando")
        self.assertFalse(estado.estrenado(self.db(), "propia"))

    def test_dos_armados_fallidos_escalan(self):
        self.s.falla_construir = {"bot"}
        self.aplicar(presente=True)
        self.ahora = AHORA + 7 * DIA
        self.aplicar(presente=True)
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "escalada")
        self.assertEqual([i["clase"] for i in estado.incidentes(self.db())], ["no entró"])

    def test_si_no_verifica_vuelve_a_la_imagen_de_la_base_anterior(self):
        self.s.falla_verificar = {"bot"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.s.llamadas[3:], [["verificar", "bot"], ["volver-propia", "bot", f"{BASE}@{H}"],
                                               ["verificar", "bot"]])
        self.assertEqual(self.s.servicios["bot"], H)
        self.assertEqual(self.pasos(), [("construir", "hecho"), ("aplicar", "fallo"), ("volver", "hecho")])

    def test_si_la_vuelta_atras_falla_escala_y_frena(self):
        self.s.falla_verificar = {"bot"}
        self.s.falla_volver = {"bot"}
        acta = self.aplicar(presente=True, cfg=self.config(
            componentes={BOT: {"base": "python:3.13-slim", "nivel": "patch", "playbook": "propia"}},
            acciones={"principal": CON_PROPIAS}, playbooks=["propia"], aviso=["accion", "avisar"]))
        self.assertEqual(acta["pasos"][0]["resultado"], "falló y la vuelta atrás también")
        self.assertEqual([i["clase"] for i in estado.incidentes(self.db())], ["vuelta atrás fallida"])
        self.assertEqual(self.s.llamadas[-1], ["avisar", acta["avisos"][0]])

    def test_una_imagen_que_usan_dos_servicios_se_rehace_en_los_dos(self):
        self.s = ServidorPropio({"bot-a": H, "bot-b": H})
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.s.servicios, {"bot-a": H2, "bot-b": H2})

    def test_corte_durante_el_armado_no_toco_nada_y_se_reintenta(self):
        self.s.corte = ("construir", "bot")
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.corte = None
        self.s.llamadas.clear()
        acta = self.aplicar(presente=True)
        self.assertEqual(estado.diario(self.db())[0]["resultado"], "no aplicado")
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")

    def test_corte_despues_de_aplicar_no_repite(self):
        original = self.s.correr

        def muere(argv, tope):
            r = original(argv, tope)
            if argv[1] == "aplicar-propia":
                raise Corte()
            return r
        self.s.correr = muere
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.correr = original
        self.s.llamadas.clear()
        self.aplicar(presente=True)
        self.assertNotIn("aplicar-propia", self.ordenes())
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "cerrada")

    def test_la_base_se_valida_antes_de_armar_el_comando(self):
        for mala in ("python:3.13-slim; rm -rf /", "python:3.13-slim", "-x@" + H2):
            self.assertFalse(ejecutor.accion(self.cfg, "principal", "construir_propia",
                                             {"servicio": "bot", "base": mala}, self.s.correr))
        self.assertEqual(self.s.llamadas, [])

    def test_una_pieza_propia_con_otro_tipo_de_accion_no_se_toca(self):
        cfg = self.config(componentes={BOT: {"base": "python:3.13-slim", "nivel": "patch", "playbook": "imagen"}},
                          acciones={"principal": dict(ACCIONES, **CON_PROPIAS)}, playbooks=["imagen", "propia"])
        (paso,) = self.aplicar(presente=True, cfg=cfg)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["tipo de acción"])
        self.assertEqual(self.s.llamadas, [])

    def test_si_falla_en_el_segundo_servicio_el_primero_vuelve(self):
        self.s = ServidorPropio({"bot-a": H, "bot-b": H})
        self.s.falla_verificar = {"bot-b"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.s.servicios, {"bot-a": H, "bot-b": H})
        self.assertEqual(self.ordenes()[-3:], ["verificar", "volver-propia", "verificar"])

    def test_si_vuelve_a_otra_version_sobre_la_misma_base_no_cuenta_como_vuelta(self):
        self.s.falla_verificar = {"bot"}
        self.s.version_al_volver = "0.6.29"
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló y la vuelta atrás también")

    def test_dos_imagenes_de_la_misma_pieza_se_intentan_una_vez_por_pasada(self):
        self.s = ServidorPropio({"bot-a": H, "bot-b": H3})
        self.s.versiones = {"bot-b": "0.6.30"}
        self.s.falla_construir = {"bot-a", "bot-b"}
        self.red = Red().hub("library/python", [tag("3.13-slim", VIEJA, H4)])
        acta = self.aplicar(presente=True)
        self.assertEqual(self.ordenes().count("construir"), 1)
        self.assertIn("una vez por pasada", [n for p in acta["pasos"] for n, ok, _ in p["compuertas"] if not ok])

    def test_el_aviso_de_dos_armados_fallidos_no_dice_que_volvio(self):
        self.s.falla_construir = {"bot"}
        self.aplicar(presente=True)
        self.ahora = AHORA + 7 * DIA
        self.aplicar(presente=True)
        (i,) = estado.incidentes(self.db())
        self.assertIn("no se pudo armar", i["texto"])
        self.assertNotIn("volvió atrás", i["texto"])

    def test_una_candidata_vieja_se_cierra_cuando_la_base_ya_esta_al_dia(self):
        self.cfg = self.config(componentes={BOT: {"base": "python:3.13-slim", "nivel": "nunca", "playbook": "propia"}},
                               acciones={"principal": CON_PROPIAS}, playbooks=["propia"])
        corrida.correr(self.cfg, AHORA, tomar=self.s.tomar, pedir=self.red, escanear=lambda *a: {})
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "detectada")
        self.s.servicios["bot"] = H3                        # alguien la rehízo a mano sobre una base más nueva aún
        red = Red().hub("library/python", [tag("3.13-slim", VIEJA, H3)])
        corrida.correr(self.cfg, AHORA + 60, tomar=self.s.tomar, pedir=red, escanear=lambda *a: {})
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "cerrada")

    def test_con_la_fuente_caida_no_se_cierra_por_una_sola_imagen(self):
        self.s = ServidorPropio({"bot-a": H2, "bot-b": H})
        self.s.versiones = {"bot-b": "0.6.30"}
        corrida.correr(self.cfg, AHORA, tomar=self.s.tomar, pedir=self.red, escanear=lambda *a: {})
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "detectada")
        corrida.correr(self.cfg, AHORA + 60, tomar=self.s.tomar, pedir=Red(), escanear=lambda *a: {})
        self.assertEqual(self.candidata("base " + H2[7:19])["estado"], "detectada")


if __name__ == "__main__":
    unittest.main()
