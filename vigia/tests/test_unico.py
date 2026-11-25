"""Lo que suma 0.5.0-dev.1 para que un solo vigía alcance: los tres pedidos del lado que recibe (contenedor creado desde un
ID, el proyecto de compose y el aviso del cierre), el carril de urgencia, el reinicio del servidor, los avisos de lo que
queda trabado, el latido para un monitor de afuera, el censo de máquinas virtuales y los repositorios montados."""
import json
import os
import tempfile
import time
import unittest

from vigia import analisis, censo, config, corrida, ejecutor, escaner, estado, fuentes, maquinas, repos

import datos
from datos import AHORA, H, H2, H3, Red, imagen_id, tag
from test_ejecutor import ACCIONES, Base, Corte, Servidor

VIEJA = "2026-12-01T00:00:00Z"
RECIENTE = "2027-01-14T00:00:00Z"            # madura el 21/01
DIA = 86400
PG = "docker.io/library/postgres"
KEV = "CVE-2099-0001"
REINICIO = dict(ACCIONES, reiniciar=["accion", "reiniciar"], verificar_servidor=["accion", "verificar-servidor"])
VULNERABLE = {"Results": [{"Vulnerabilities": [{"VulnerabilityID": KEV, "Severity": "CRITICAL", "FixedVersion": "16.16"}]}]}
LIMPIO = {"Results": [{"Vulnerabilities": []}]}


class Maquina(Servidor):
    """El servidor de mentira, con su arranque, el pedido de reinicio y proyectos de compose a elección."""

    def __init__(self, servicios, proyectos=None):
        super().__init__(servicios)
        self.arrancado = AHORA - 30 * DIA
        self.pide_reinicio = False
        self.reinicia = True
        self.no_vuelven = set()
        self.servidor_ok = True
        self.corta_reinicio = False
        self.etiquetas = dict(proyectos or {})       # contenedor -> (proyecto, servicio)

    def tomar(self, servidor, ahora, vigencia):
        t = super().tomar(servidor, ahora, vigencia)
        if t["estado"] == "ok":
            t["foto"]["sistema"]["arrancado"] = self.arrancado
            t["foto"]["sistema"]["reinicio_pedido"] = self.pide_reinicio
            for c in t["foto"]["contenedores"]:
                if c["nombre"] in self.etiquetas:
                    c["proyecto"], c["servicio"] = self.etiquetas[c["nombre"]]
        return t

    def correr(self, argv, tope):
        if argv[1] == "reiniciar":
            self.llamadas.append(argv[1:])
            if self.reinicia:
                self.arrancado += 600
                self.pide_reinicio = False
                self.detenidos |= self.no_vuelven
                if self.corta_reinicio:
                    raise Corte()
            return 0
        if argv[1] == "verificar-servidor":
            self.llamadas.append(argv[1:])
            return 0 if self.servidor_ok else 1
        if argv[1] in ("aplicar", "verificar", "volver") and "-" in argv[2] and argv[2] not in self.servicios:
            argv = argv[:2] + [argv[2].split("-", 1)[1]] + argv[3:]
        return super().correr(argv, tope)


class Unico(Base):
    def setUp(self):
        super().setUp()
        self.s = Maquina({"db": f"postgres:16.15@{H}"})
        self.escaneos = []

    def incidentes(self):
        return {i["id"]: i["clase"] for i in estado.incidentes(self.db())}

    def escanear(self, vulnerable_la_nueva=False):
        def escanear(comando, ids, cves, tope):
            self.escaneos.append(list(ids))
            return {i: escaner.resumir(VULNERABLE if (i.startswith("sha256:") or vulnerable_la_nueva) else LIMPIO, cves)
                    for i in ids}
        return escanear

    def aplicar(self, presente=False, cfg=None, s=None, escanear=None):
        s = s or self.s
        return ejecutor.aplicar(cfg or self.cfg, presente=presente, tomar=s.tomar, pedir=self.red, correr=s.correr,
                                reloj=self.reloj, dormir=lambda n: setattr(self, "ahora", self.ahora + n),
                                local=time.gmtime, escanear=escanear or (lambda *a: {}))


class Proyecto(Unico):
    """Pedido 2: dos proyectos de compose con un servicio del mismo nombre."""

    def setUp(self):
        super().setUp()
        self.s = Maquina({"uno-web": f"postgres:16.15@{H}", "dos-web": f"postgres:16.15@{H}"},
                         {"uno-web": ("uno", "web"), "dos-web": ("dos", "web")})

    def test_sin_proyecto_en_las_acciones_frena_por_ambiguo(self):
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["servicios"])
        self.assertEqual(self.s.llamadas, [])

    def test_con_proyecto_en_las_acciones_aplica_a_cada_uno(self):
        con = {k: [x.replace("{servicio}", "{proyecto}-{servicio}") for x in v] for k, v in ACCIONES.items()}
        acta = self.aplicar(presente=True, cfg=self.config(acciones={"principal": con}))
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(set(self.s.servicios.values()), {f"{PG}:16.16@{H2}"})
        self.assertEqual([e["datos"]["proyecto"] for e in estado.diario(self.db())], ["dos", "uno"])

    def test_un_nombre_repetido_en_otra_imagen_tambien_frena(self):
        self.s = Maquina({"uno-web": f"postgres:16.15@{H}", "dos-web": f"ejemplo/web:1.0.0@{H3}"},
                         {"uno-web": ("uno", "web"), "dos-web": ("dos", "web")})
        self.red.hub("ejemplo/web", [tag("1.0.0", VIEJA, H3)])
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["servicios"])

    def test_el_hueco_proyecto_se_valida(self):
        cfg = self.config(acciones={"principal": dict(ACCIONES, verificar=["a", "{proyecto}", "{servicio}"])})
        self.assertTrue(ejecutor.accion(cfg, "principal", "verificar", {"servicio": "web", "proyecto": "uno"},
                                        lambda a, t: 0))
        self.assertFalse(ejecutor.accion(cfg, "principal", "verificar", {"servicio": "web", "proyecto": "Uno Malo"},
                                         lambda a, t: 0))
        self.assertFalse(ejecutor.accion(cfg, "principal", "verificar", {"servicio": "web"}, lambda a, t: 0))
        with self.assertRaises(config.ConfigError):
            self.config(acciones={"principal": dict(ACCIONES, respaldo_vigente=["a", "{proyecto}"])})


class DesdeUnId(unittest.TestCase):
    """Pedido 1: un contenedor creado desde el ID de su imagen no es un repositorio público llamado «sha256»."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config.validar(datos.config(self.tmp.name))

    def piezas(self, ref, tags):
        foto = censo.limpiar(datos.foto([("bot", ref, 7)], {7: {"tags": tags, "digests": [], "creada": VIEJA}}),
                             AHORA, 900)
        pedidos = []

        def consultar(tipo, registro, repo):
            pedidos.append(repo)
            return {"estado": "desconocido", "motivo": "x", "etiquetas": [], "hay_mas": False}
        return analisis.piezas(self.cfg, "principal", foto, consultar, None, AHORA)[:-1], pedidos

    def test_con_etiqueta_propia_local_es_propia_y_no_sale_nada(self):
        (p,), pedidos = self.piezas(imagen_id(7), ["otra/cosa:1", "propio/bot:0.6.31"])
        self.assertEqual((p["pieza"], p["propia"], pedidos), ("docker.io/propio/bot", True, []))

    def test_sin_etiquetas_queda_sin_referencia_y_no_consulta(self):
        (p,), pedidos = self.piezas(imagen_id(7), [])
        self.assertEqual((p["pieza"], p["estado"], pedidos), ("(sin referencia)", "desconocido", []))

    def test_el_id_corto_tambien_es_un_id(self):
        (p,), pedidos = self.piezas(imagen_id(7)[7:19], [])
        self.assertEqual((p["pieza"], pedidos), ("(sin referencia)", []))

    def test_con_etiqueta_publica_usa_esa_y_su_huella_para_volver(self):
        foto = censo.limpiar(datos.foto([("db", imagen_id(7), 7)], {7: {
            "tags": ["postgres:16.15"], "digests": [f"postgres@{H}"], "creada": VIEJA}}), AHORA, 900)
        red = Red().hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        (p,) = analisis.piezas(self.cfg, "principal", foto, lambda t, r, repo: fuentes.docker_hub(repo, red), None,
                               AHORA)[:-1]
        self.assertEqual((p["pieza"], p["candidata"]["anterior"]), (PG, f"postgres:16.15@{H}"))


class Avisos(Unico):
    """Pedido 3: el cierre de un incidente avisado también se avisa, y su archivo abierto se retira."""

    def test_el_cierre_se_avisa_una_vez(self):
        cfg = self.config(aviso=["accion", "avisar"])
        db = self.db()
        estado.incidente(db, "pausa", "vigía en pausa", "texto", AHORA)
        (ruta,) = corrida.avisar(cfg, db, AHORA, lambda a, t: 0)
        self.assertTrue(os.path.exists(ruta))
        estado.cerrar_incidente(db, "pausa", AHORA + 60)
        llamadas = []
        (cierre,) = corrida.avisar(cfg, db, AHORA + 120, lambda a, t: llamadas.append(a) or 0)
        self.assertTrue(cierre.endswith("pausa.cerrado.txt"))
        self.assertFalse(os.path.exists(ruta))
        self.assertEqual(llamadas, [["accion", "avisar", cierre]])
        self.assertEqual(corrida.avisar(cfg, db, AHORA + 200, lambda a, t: 0), [])

    def test_lo_que_se_cerro_sin_avisar_no_se_anuncia(self):
        db = self.db()
        estado.incidente(db, "pausa", "vigía en pausa", "texto", AHORA)
        estado.cerrar_incidente(db, "pausa", AHORA + 60)
        self.assertEqual(corrida.avisar(self.cfg, db, AHORA + 120, lambda a, t: 0), [])

    def test_lo_cerrado_antes_de_esta_version_no_se_anuncia(self):
        import sqlite3
        os.makedirs(self.cfg["estado_dir"], exist_ok=True)
        viejo = sqlite3.connect(os.path.join(self.cfg["estado_dir"], "vigia.sqlite3"))
        viejo.executescript("CREATE TABLE incidentes (id TEXT PRIMARY KEY, clase TEXT NOT NULL, texto TEXT NOT NULL, "
                            "abierto INTEGER NOT NULL, cerrado INTEGER, avisado INTEGER);"
                            f"INSERT INTO incidentes VALUES ('x', 'no entró', 't', {AHORA}, {AHORA + 1}, {AHORA});")
        viejo.commit()
        viejo.close()
        self.assertEqual(corrida.avisar(self.cfg, self.db(), AHORA + 120, lambda a, t: 0), [])

    def test_una_noticia_sale_una_sola_vez_y_no_queda_abierta(self):
        db = self.db()
        estado.noticia(db, "urgencia-x-1", "aplicada por urgencia", "texto", AHORA)
        self.assertEqual(len(corrida.avisar(self.cfg, db, AHORA, lambda a, t: 0)), 1)
        self.assertEqual(corrida.avisar(self.cfg, db, AHORA + 2 * DIA, lambda a, t: 0), [])
        self.assertEqual(estado.incidentes(db), [])


class NadaTrabadoEnSilencio(Unico):
    def test_el_estreno_pendiente_se_avisa_y_se_cierra_al_estrenar(self):
        self.aplicar()
        self.assertEqual(self.incidentes(), {"estreno-imagen": "estreno pendiente"})
        self.aplicar(presente=True)
        self.assertEqual(self.incidentes(), {})

    def test_una_compuerta_de_persona_dos_ventanas_seguidas_se_avisa(self):
        self.red.hub("library/postgres", [tag("16.16", VIEJA), tag("16.15", VIEJA, H)])     # sin huella
        self.aplicar(presente=True)
        self.assertEqual(self.incidentes(), {})
        self.ahora = AHORA + 7 * DIA
        self.aplicar(presente=True)
        (clase,) = self.incidentes().values()
        self.assertEqual(clase, "trabada")
        self.red.hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.ahora = AHORA + 14 * DIA
        self.assertEqual(self.aplicar(presente=True)["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.incidentes(), {})

    def test_sin_respaldo_dos_ventanas_avisa_por_servidor_y_se_cierra_al_volver(self):
        self.s.respaldo = False
        self.aplicar(presente=True)
        self.ahora = AHORA + 7 * DIA
        self.aplicar(presente=True)
        self.assertEqual(self.incidentes(), {"respaldo:principal": "sin respaldo vigente"})
        self.s.respaldo = True
        self.ahora = AHORA + 14 * DIA
        self.aplicar(presente=True)
        self.assertEqual(self.incidentes(), {})

    def test_el_estreno_no_tapa_otra_compuerta_de_persona(self):
        self.red.hub("library/postgres", [tag("16.16", VIEJA), tag("16.15", VIEJA, H)])     # sin huella
        self.aplicar()
        self.ahora = AHORA + 7 * DIA
        self.aplicar()
        self.assertEqual(sorted(self.incidentes().values()), ["trabada"])

    def test_el_ejemplo_de_configuracion_es_valido(self):
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = config.cargar(os.path.join(raiz, "ejemplo", "vigia.json"))
        self.assertIn("reinicio", cfg["playbooks"])
        self.assertTrue(ejecutor._con_proyecto(cfg, "principal", "datos"))

    def test_lo_que_espera_la_maduracion_no_es_trabado(self):
        self.red.hub("library/postgres", [tag("16.16", RECIENTE, H2), tag("16.15", VIEJA, H)])
        for semana in range(2):
            self.ahora = AHORA + semana * 7 * DIA - (DIA if semana else 0)
            self.aplicar(presente=True)
        self.assertNotIn("trabada", self.incidentes().values())


class Urgencia(Unico):
    """Un KEV con arreglo en algo expuesto: no espera la maduración y entra en la ventana de urgencia, después de
    comprobar con el escáner que la versión propuesta ya no lo tiene."""

    def setUp(self):
        super().setUp()
        self.red.hub("library/postgres", [tag("16.16", RECIENTE, H2), tag("16.15", VIEJA, H)])
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": KEV}]}).encode())
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen", "expuesto": True}},
                               escaner={"comando": ["escaner"]}, ventana_urgente={"desde": "07:00", "hasta": "09:00"},
                               aviso=["accion", "avisar"])
        estado.estrenar(self.db(), "imagen", "antes", AHORA)
        self.ahora = AHORA + DIA                    # sábado 08:00: fuera de la ventana ordinaria

    def test_en_la_ventana_de_urgencia_aplica_sin_madurar_y_avisa(self):
        acta = self.aplicar(escanear=self.escanear())
        self.assertEqual((acta["ventana"], acta["pasos"][0]["resultado"]), ("urgencia", "aplicada"))
        self.assertEqual(self.s.servicios["db"], f"{PG}:16.16@{H2}")
        self.assertIn([f"{PG}:16.16@{H2}"], self.escaneos)            # se escaneó la versión propuesta
        avisos = [a for a in acta["avisos"] if "urgencia" in a]
        self.assertEqual(len(avisos), 1)
        with open(avisos[0], encoding="utf-8") as f:
            self.assertIn(KEV, f.read())

    def test_si_la_version_nueva_sigue_vulnerable_no_aplica(self):
        (paso,) = self.aplicar(escanear=self.escanear(vulnerable_la_nueva=True))["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["arreglo comprobado"])
        self.assertEqual(self.s.servicios["db"], f"postgres:16.15@{H}")

    def test_sin_kev_la_ventana_de_urgencia_no_toca_nada(self):
        acta = self.aplicar(escanear=lambda c, ids, cves, t: {i: escaner.resumir(LIMPIO, cves) for i in ids})
        self.assertEqual((acta["motivo"], acta["pasos"]), ("ventana de urgencia: no hay nada urgente", []))
        self.assertEqual(self.s.llamadas, [])

    def test_una_pieza_interna_no_va_por_urgencia(self):
        cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen"}}, escaner={"comando": ["escaner"]},
                          ventana_urgente={"desde": "07:00", "hasta": "09:00"})
        acta = self.aplicar(cfg=cfg, escanear=self.escanear())
        self.assertEqual(acta["motivo"], "ventana de urgencia: no hay nada urgente")

    def test_el_nivel_nunca_tampoco_se_saltea_por_urgencia(self):
        cfg = self.config(componentes={PG: {"nivel": "nunca", "playbook": "imagen", "expuesto": True}},
                          escaner={"comando": ["escaner"]}, ventana_urgente={"desde": "07:00", "hasta": "09:00"})
        (paso,) = self.aplicar(cfg=cfg, escanear=self.escanear())["pasos"]
        self.assertEqual(paso["compuertas"][0][:2], ("versión", False))

    def test_el_plazo_de_un_kev_con_arreglo_expuesto_es_de_un_dia(self):
        r = corrida.correr(self.cfg, self.ahora, tomar=self.s.tomar, pedir=self.red, escanear=self.escanear())
        db = next(p for p in r["servidores"][0]["piezas"] if p["pieza"] == PG)
        self.assertEqual((db["candidata"]["plazo"]["clase"], db["candidata"]["urgencia"]), ("kev_expuesto", [KEV]))
        self.assertIn("va por el carril de urgencia", r["resumen"])

    def test_un_kev_sin_arreglo_en_algo_expuesto_abre_incidente_y_se_cierra(self):
        sin = {"Results": [{"Vulnerabilities": [{"VulnerabilityID": KEV, "Severity": "CRITICAL"}]}]}
        corrida.correr(self.cfg, self.ahora, tomar=self.s.tomar, pedir=self.red,
                       escanear=lambda c, ids, cves, t: {i: escaner.resumir(sin, cves) for i in ids})
        self.assertEqual(list(self.incidentes().values()), ["falla explotada sin arreglo"])
        corrida.correr(self.cfg, self.ahora + 60, tomar=self.s.tomar, pedir=self.red,
                       escanear=lambda c, ids, cves, t: {i: escaner.resumir(LIMPIO, cves) for i in ids})
        self.assertEqual(self.incidentes(), {})


class Reinicio(Unico):
    def setUp(self):
        super().setUp()
        self.red.hub("library/postgres", [tag("16.15", VIEJA, H)])          # las imágenes, al día
        self.cfg = self.config(componentes={"sistema": {"nivel": "patch", "playbook": "reinicio"}},
                               playbooks=["imagen", "reinicio"], acciones={"principal": REINICIO})
        self.s.pide_reinicio = True

    def candidata_sistema(self):
        return dict(self.db().execute("SELECT * FROM candidatas WHERE pieza = 'sistema'").fetchone())

    def test_reinicia_espera_que_vuelva_todo_verifica_y_cierra(self):
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "aplicada")
        self.assertEqual(self.ordenes(), ["respaldo", "reiniciar", "verificar-servidor"])
        self.assertEqual(self.candidata_sistema()["estado"], "cerrada")
        self.assertTrue(estado.estrenado(self.db(), "reinicio"))

    def test_sin_el_tipo_habilitado_queda_en_observar(self):
        cfg = self.config(componentes={"sistema": {"nivel": "patch", "playbook": "reinicio"}},
                          acciones={"principal": REINICIO})
        (paso,) = self.aplicar(presente=True, cfg=cfg)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["tipo de acción"])

    def test_el_tipo_reinicio_no_vale_para_una_imagen(self):
        cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "reinicio"}}, playbooks=["reinicio"],
                          acciones={"principal": REINICIO})
        self.red.hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.s.pide_reinicio = False
        (paso,) = self.aplicar(presente=True, cfg=cfg)["pasos"]
        self.assertIn("no corresponde", dict((n, d) for n, _, d in paso["compuertas"])["tipo de acción"])

    def test_si_un_servicio_no_vuelve_escala_y_avisa(self):
        self.s.no_vuelven = {"db"}
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "el reinicio no volvió bien")
        self.assertEqual(list(self.incidentes().values()), ["reinicio incompleto"])
        self.assertEqual(self.candidata_sistema()["estado"], "escalada")

    def test_si_no_reinicia_queda_como_estaba(self):
        self.s.reinicia = False
        acta = self.aplicar(presente=True)
        self.assertEqual(acta["pasos"][0]["resultado"], "falló; quedó como estaba")
        self.assertEqual(self.candidata_sistema()["estado"], "esperando")

    def test_va_ultimo_y_no_despues_de_una_falla(self):
        self.red.hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.s.falla_verificar = {"db"}
        acta = self.aplicar(presente=True, cfg=self.config(
            componentes={"sistema": {"nivel": "patch", "playbook": "reinicio"}, PG: {"nivel": "patch", "playbook": "imagen"}},
            playbooks=["imagen", "reinicio"], acciones={"principal": REINICIO}))
        self.assertEqual([p["pieza"] for p in acta["pasos"]], [PG, "sistema"])
        self.assertEqual(acta["pasos"][1]["compuertas"][0][0], "pasada sin fallas")
        self.assertNotIn("reiniciar", self.ordenes())

    def test_si_el_reinicio_corta_al_vigia_la_pasada_siguiente_lo_resuelve(self):
        self.s.corta_reinicio = True
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.assertEqual(self.candidata_sistema()["estado"], "aplicando")
        self.ahora = AHORA + 3 * 3600             # el temporizador al arrancar, ya fuera de la ventana
        self.s.corta_reinicio = False
        acta = self.aplicar()
        self.assertEqual(acta["motivo"], "fuera de la ventana")
        self.assertEqual(self.candidata_sistema()["estado"], "cerrada")
        self.assertTrue(estado.estrenado(self.db(), "reinicio"))
        self.assertEqual([e["resultado"] for e in estado.diario(self.db())], ["hecho"])

    def test_sin_saber_cuando_arranco_no_reinicia(self):
        self.s.arrancado = None
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["arranque medible"])

    def test_el_candado_de_un_arranque_anterior_se_toma(self):
        d, ahora = os.path.join(self.tmp.name, "c"), int(time.time())
        corrida._candado(d, ahora, arranque=lambda: "arranque-1")
        with self.assertRaises(corrida.Ocupado):
            corrida._candado(d, ahora, arranque=lambda: "arranque-1")
        corrida._candado(d, ahora, arranque=lambda: "arranque-2")


class Observar(Unico):
    def correr(self, ahora=None, **k):
        k.setdefault("escanear", lambda *a: {})
        return corrida.correr(self.cfg, ahora or self.ahora, tomar=self.s.tomar, pedir=self.red, **k)

    def test_un_servidor_sin_medir_un_dia_se_avisa_y_se_cierra_al_volver(self):
        self.s.medible = False
        self.correr()
        self.correr(AHORA + 3600)
        self.assertEqual(self.incidentes(), {})                  # un tropiezo no se avisa
        self.correr(AHORA + DIA + 60)
        self.assertEqual(self.incidentes(), {"sin-medir-principal": "servidor sin medir"})
        self.s.medible = True
        self.correr(AHORA + DIA + 120)
        self.assertEqual(self.incidentes(), {})

    def test_la_pausa_de_mas_se_avisa(self):
        os.makedirs(self.cfg["estado_dir"], exist_ok=True)
        pausa = os.path.join(self.cfg["estado_dir"], "PAUSA")
        open(pausa, "w").close()
        os.utime(pausa, (AHORA - 8 * DIA, AHORA - 8 * DIA))
        self.correr()
        self.assertEqual(self.incidentes(), {"pausa": "vigía en pausa"})
        os.unlink(pausa)
        self.correr(AHORA + 60)
        self.assertEqual(self.incidentes(), {})

    def test_el_latido_y_la_salud(self):
        cfg = self.cfg
        self.assertEqual(corrida.salud(cfg, AHORA, 26)[0], 4)
        self.correr()
        self.assertEqual(corrida.salud(cfg, AHORA + 3600, 26), (0, "vigía: bien · incidentes abiertos: 0"))
        codigo, texto = corrida.salud(cfg, AHORA + 27 * 3600, 26)
        self.assertEqual(codigo, 4)
        self.assertIn("la última corrida fue hace 27 h", texto)

    def test_la_pieza_del_sistema_propone_el_reinicio_con_su_plazo(self):
        self.s.pide_reinicio = True
        r = self.correr()
        sis = next(p for p in r["servidores"][0]["piezas"] if p.get("sistema"))
        self.assertEqual((sis["estado"], sis["candidata"]["compuerta_version"], sis["candidata"]["plazo"]["clase"]),
                         ("reinicio pedido", "fuera: la pieza no admite aplicación automática", "alta_interna"))

    def test_parches_de_seguridad_que_no_entran(self):
        paquete = {"nombre": "libssl3", "de": "1", "a": "2", "origen": "estable-security", "seguridad": True}
        original = self.s.tomar

        def tomar(servidor, ahora, vigencia):
            t = original(servidor, ahora, vigencia)
            t["foto"]["sistema"]["paquetes"] = [paquete]
            return t
        self.s.tomar = tomar
        self.correr()
        self.assertEqual(self.incidentes(), {})
        self.correr(AHORA + 73 * 3600)
        self.assertEqual(self.incidentes(), {"sistema-seguridad-principal": "parches del sistema"})


class Maquinas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = os.path.join(self.tmp.name, "censo.json")
        self.m = config.validar(datos.config(self.tmp.name, maquinas={
            "archivo": self.ruta, "esperadas": {"vm-a": {"servidor": "principal"}, "vm-b": {}, "vm-c": {"apagada": True}}}
        ))["maquinas"]

    def censo(self, lista, tomada=AHORA):
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump({"tomada": tomada, "maquinas": lista}, f)
        return maquinas.leer(self.m, AHORA)

    def test_ausente_detenida_y_sin_vigia(self):
        c = self.censo([{"nombre": "vm-a", "estado": "corriendo"}, {"nombre": "vm-b", "estado": "apagada"},
                        {"nombre": "vm-c", "estado": "apagada"}, {"nombre": "vm-x", "estado": "corriendo"}])
        self.assertEqual(sorted(i for i, _, _ in maquinas.hallazgos(self.m, c)),
                         ["maquina-detenida-vm-b", "maquina-sin-vigia-vm-x"])
        c = self.censo([{"nombre": "vm-a", "estado": "corriendo"}])
        self.assertIn("maquina-ausente-vm-b", [i for i, _, _ in maquinas.hallazgos(self.m, c)])

    def test_un_censo_vencido_o_roto_es_desconocido(self):
        self.assertEqual(self.censo([], tomada=AHORA - 7200)["estado"], "desconocido")
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write('{"tomada": 1, "maquinas": [{"nombre": "con espacio", "estado": "x"}]}')
        self.assertEqual(maquinas.leer(self.m, AHORA)["estado"], "desconocido")
        os.unlink(self.ruta)
        c = maquinas.leer(self.m, AHORA)
        self.assertEqual([i for i, _, _ in maquinas.hallazgos(self.m, c)], ["maquinas-censo"])

    def test_la_configuracion_no_nombra_un_servidor_desconocido(self):
        with self.assertRaises(config.ConfigError):
            config.validar(datos.config(self.tmp.name, maquinas={"archivo": "x", "esperadas": {"vm": {"servidor": "otro"}}}))


class Repos(unittest.TestCase):
    REMOTO = "https://forja.ejemplo.test/oca/web.git"
    A, B = "a" * 40, "b" * 40

    def refs(self, rama, commit):
        def linea(t):
            return f"{len(t) + 4:04x}{t}".encode()
        cuerpo = linea("# service=git-upload-pack\n") + b"0000" + linea(f"{self.B} HEAD\0multi_ack\n") \
            + linea(f"{commit} refs/heads/{rama}\n") + linea(f"{self.B} refs/heads/otra\n") + b"0000"
        return Red({self.REMOTO + "/info/refs": (200, {}, cuerpo)})

    def cfg(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        return config.validar(datos.config(tmp, repos=[{"nombre": "addons-web", "servidor": "principal",
                                                         "local": ["leer-commit"], "remoto": self.REMOTO, "rama": "18.0"}]))

    def test_al_dia_distinto_y_desconocido(self):
        local = lambda a, t: (0, (self.A + "\n").encode())
        (r,) = repos.revisar(self.cfg(), self.refs("18.0", self.A), local)
        self.assertEqual(r["estado"], "al día")
        (r,) = repos.revisar(self.cfg(), self.refs("18.0", self.B), local)
        self.assertEqual(r["estado"], "distinto del remoto")
        (r,) = repos.revisar(self.cfg(), self.refs("17.0", self.A), local)
        self.assertEqual((r["estado"], r["motivo"]), ("desconocido", "no se pudo leer la rama remota"))
        (r,) = repos.revisar(self.cfg(), self.refs("18.0", self.A), lambda a, t: (1, b""))
        self.assertEqual(r["estado"], "desconocido")

    def test_solo_https(self):
        with self.assertRaises(config.ConfigError):
            config.validar(datos.config(tempfile.mkdtemp(), repos=[{"nombre": "x", "servidor": "principal",
                                                                     "local": ["a"], "remoto": "http://x.test/r",
                                                                     "rama": "18.0"}]))


if __name__ == "__main__":
    unittest.main()
