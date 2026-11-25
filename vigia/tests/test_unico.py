"""Lo que suma 0.5.0-dev.1 para que un solo vigía alcance: los tres pedidos del lado que recibe (contenedor creado desde un
ID, el proyecto de compose y el aviso del cierre), el carril de urgencia, el reinicio del servidor, los avisos de lo que
queda trabado, el latido para un monitor de afuera, el censo de máquinas virtuales y los repositorios montados."""
import json
import os
import re
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
        datos.kev(self.red)
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


class Turnos(Unico):
    def test_un_parche_maduro_no_espera_al_siguiente_recien_salido(self):
        self.red.hub("library/postgres", [tag("16.17", RECIENTE, H3), tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        acta = self.aplicar(presente=True)
        self.assertEqual((acta["pasos"][0]["a"], acta["pasos"][0]["resultado"]), ("16.16", "aplicada"))

    def test_la_candidata_que_reemplaza_a_otra_hereda_su_fecha(self):
        corrida.correr(self.cfg, AHORA, tomar=self.s.tomar, pedir=datos.kev(self.red))
        self.red.hub("library/postgres", [tag("16.17", VIEJA, H3), tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        corrida.correr(self.cfg, AHORA + 5 * DIA, tomar=self.s.tomar, pedir=self.red)
        self.assertEqual(self.candidata("16.17")["detectada"], AHORA)

    def test_un_servidor_que_no_se_mide_no_le_quita_la_ventana_a_los_demas(self):
        cfg = self.config(servidores=[{"nombre": "caido"}, {"nombre": "principal"}],
                          acciones={"caido": ACCIONES, "principal": ACCIONES})
        caido, sano = Maquina({"db": f"postgres:16.15@{H}"}), self.s
        caido.medible = False

        def tomar(servidor, ahora, vigencia):
            return (caido if servidor["nombre"] == "caido" else sano).tomar(servidor, ahora, vigencia)
        datos.kev(self.red)
        for semana in range(2):
            self.ahora = AHORA + semana * 7 * DIA
            acta = ejecutor.aplicar(cfg, presente=True, tomar=tomar, pedir=self.red, correr=sano.correr, reloj=self.reloj,
                                    dormir=lambda n: None, local=time.gmtime, escanear=lambda *a: {})
        self.assertEqual((acta["servidor"], acta["pasos"][0]["resultado"]), ("principal", "aplicada"))


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

    def test_el_candado_no_vence_por_tiempo_mientras_su_dueno_vive(self):
        d = os.path.join(self.tmp.name, "c")
        primero = corrida.Candado(d)
        os.utime(os.path.join(d, "corrida.candado"), (AHORA - 30 * DIA, AHORA - 30 * DIA))
        with self.assertRaises(corrida.Ocupado):
            corrida.Candado(d)
        primero.soltar()
        corrida.Candado(d).soltar()


class Observar(Unico):
    def correr(self, ahora=None, **k):
        k.setdefault("escanear", lambda *a: {})
        datos.kev(self.red)
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
        cfg = self.cfg = self.config(ventana=None)
        self.assertEqual(corrida.salud(cfg, AHORA, 26)[0], 4)
        self.correr()
        self.assertEqual(corrida.salud(cfg, AHORA + 3600, 26), (0, "vigía: bien · incidentes abiertos: 0"))
        codigo, texto = corrida.salud(cfg, AHORA + 27 * 3600, 26)
        self.assertEqual(codigo, 4)
        self.assertIn("la última corrida fue hace 27 h", texto)

    def test_la_salud_mira_tambien_al_ejecutor(self):
        self.correr()
        self.assertEqual(corrida.salud(self.cfg, AHORA + 60, 26),
                         (4, "vigía: el ejecutor no registró ninguna pasada · incidentes abiertos: 0"))
        self.aplicar()
        self.assertEqual(corrida.salud(self.cfg, self.ahora + 60, 26)[0], 0)
        codigo, texto = corrida.salud(self.cfg, self.ahora + 9 * DIA, 26)
        self.assertEqual(codigo, 4)
        self.assertIn("la última pasada del ejecutor", texto)

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

    def test_declarada_sin_servidor_que_corre_es_sin_vigia_salvo_a_proposito(self):
        c = self.censo([{"nombre": "vm-a", "estado": "corriendo"}, {"nombre": "vm-b", "estado": "corriendo"},
                        {"nombre": "vm-c", "estado": "apagada"}])
        self.assertEqual([i for i, _, _ in maquinas.hallazgos(self.m, c)], ["maquina-sin-vigia-vm-b"])
        m = config.validar(datos.config(self.tmp.name, maquinas={"archivo": self.ruta, "esperadas": {
            "vm-a": {"servidor": "principal"}, "vm-b": {"sin_vigia": True}, "vm-c": {"apagada": True}}}))["maquinas"]
        self.assertEqual(maquinas.hallazgos(m, c), [])

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


class Revision(Unico):
    """Un caso por cada hallazgo de la revisión independiente (Codex), con el número del hallazgo."""

    def correr(self, ahora=None, **k):
        k.setdefault("escanear", lambda *a: {})
        datos.kev(self.red)
        return corrida.correr(self.cfg, ahora or self.ahora, tomar=self.s.tomar, pedir=self.red, **k)

    # 1
    def test_un_salto_de_linea_no_pasa_por_un_hueco_ni_por_la_foto(self):
        cfg = self.config(acciones={"principal": dict(ACCIONES, verificar=["a", "{proyecto}", "{servicio}"])})
        self.assertFalse(ejecutor.accion(cfg, "principal", "verificar", {"servicio": "web", "proyecto": "pila\n"},
                                         lambda a, t: 0))
        cruda = datos.foto([("db", "postgres:16.15", 1)])
        cruda["contenedores"][0]["proyecto"] = "pila\n"
        with self.assertRaises(censo.FotoError):
            censo.limpiar(cruda, AHORA, 900)

    # 6
    def test_una_flotante_sin_version_conocida_no_se_aplica_sola(self):
        foto = censo.limpiar(datos.foto([("db", "postgres:latest", 1)], {1: {"digests": [f"postgres@{H}"]}}), AHORA, 900)
        red = Red().hub("library/postgres", [tag("latest", VIEJA, H2), tag("17.2", VIEJA, H2)])
        (p,) = analisis.piezas(self.cfg, "principal", foto, lambda t, r, repo: fuentes.docker_hub(repo, red), None,
                               AHORA)[:-1]
        self.assertTrue(p["candidata"]["compuerta_version"].startswith("fuera: no se sabe qué versión corre"))

    # 7
    def test_un_contenedor_sin_referencia_cuenta_para_la_ambiguedad(self):
        self.s = Maquina({"uno-web": f"postgres:16.15@{H}", "dos-web": "sha256:" + "c" * 64},
                         {"uno-web": ("uno", "web"), "dos-web": ("dos", "web")})
        (paso,) = self.aplicar(presente=True)["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["servicios"])

    # 8
    def test_un_escaneo_vacio_no_es_cero_y_no_se_recortan_los_kev(self):
        r = escaner.escanear(["escaner"], ["x"], set(), 60, correr=lambda a, t: (0, b"{}"))
        self.assertEqual(r["x"]["estado"], "desconocido")
        muchos = {"Results": [{"Vulnerabilities": [{"VulnerabilityID": f"CVE-2099-{i:04d}", "FixedVersion": "1"}
                                                   for i in range(60)]}]}
        r = escaner.resumir(muchos, {f"CVE-2099-{i:04d}" for i in range(60)})
        self.assertIn("CVE-2099-0059", r["kev"])

    # 10
    def test_en_un_registro_oci_la_maduracion_cuenta_desde_que_se_vio(self):
        from test_fuentes import registro
        self.cfg = self.config(componentes={"registro.ejemplo.test/equipo/forja": {"nivel": "patch"}})
        self.s = Maquina({"forja": f"registro.ejemplo.test/equipo/forja:15.0.9-rootless@{H}"})
        self.red = registro(["15.0.9-rootless", "15.0.11-rootless"])
        r = self.correr()
        forja = next(p for p in r["servidores"][0]["piezas"] if "forja" in p["pieza"])
        self.assertEqual(forja["candidata"]["compuerta_version"], "espera: madura el 2027-01-22")

    # 11 y 13
    def test_un_escaneo_desconocido_no_cierra_un_kev_avisado(self):
        cfg = self.cfg = self.config(componentes={PG: {"nivel": "patch", "expuesto": True}}, escaner={"comando": ["e"]})
        sin = {"Results": [{"Vulnerabilities": [{"VulnerabilityID": KEV, "Severity": "CRITICAL"}]}]}
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": KEV}]}).encode())
        self.correr(escanear=lambda c, ids, cves, t: {i: escaner.resumir(sin, cves) for i in ids})
        self.correr(AHORA + 60, escanear=lambda c, ids, cves, t: {i: {"estado": "desconocido", "motivo": "x"} for i in ids})
        self.assertEqual(list(self.incidentes().values()), ["falla explotada sin arreglo"])
        self.assertEqual(cfg["escaner"]["comando"], ["e"])

    def test_un_kev_con_arreglo_sin_imagen_nueva_avisa_pasado_el_plazo(self):
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "expuesto": True}}, escaner={"comando": ["e"]})
        self.red.hub("library/postgres", [tag("16.15", VIEJA, H)])
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": KEV}]}).encode())
        r = self.correr(escanear=self.escanear())
        self.assertIn("sin una imagen nueva que aplicar", r["resumen"])
        self.correr(AHORA + DIA + 60, escanear=self.escanear())
        self.assertEqual(list(self.incidentes().values()), ["falla explotada sin imagen nueva"])

    # 12
    def test_sin_poder_listar_los_contenedores_tambien_es_sin_medir(self):
        original = self.s.tomar

        def tomar(servidor, ahora, vigencia):
            t = original(servidor, ahora, vigencia)
            t["foto"]["contenedores"] = None
            return t
        self.s.tomar = tomar
        self.correr()
        self.correr(AHORA + DIA + 60)
        self.assertEqual(self.incidentes(), {"sin-medir-principal": "servidor sin medir"})

    # 16
    def test_un_aviso_que_no_salio_se_reintenta(self):
        cfg = self.config(aviso=["avisar"])
        db = self.db()
        estado.incidente(db, "pausa", "vigía en pausa", "texto", AHORA)
        self.assertEqual(len(corrida.avisar(cfg, db, AHORA, lambda a, t: 1)), 1)
        self.assertEqual(len(corrida.avisar(cfg, db, AHORA + 60, lambda a, t: 0)), 1)
        self.assertEqual(corrida.avisar(cfg, db, AHORA + 120, lambda a, t: 0), [])

    # 17
    def test_la_paginacion_no_lleva_a_otro_servidor(self):
        red = Red({"https://registro.ejemplo.test/v2/equipo/forja/tags/list": (
            200, {"link": '<https://ajeno.test/consulta>; rel="next"'}, b'{"tags": ["1.0.0"]}')})
        self.assertEqual(fuentes.registro_oci("registro.ejemplo.test", "equipo/forja", red)["estado"], "desconocido")
        self.assertFalse(any("ajeno" in u for u, _ in red.pedidos))

    # 18
    def test_un_nulo_en_una_lista_de_la_foto_la_rechaza(self):
        for cambio in (lambda c: c["sistema"].update(kernels=["6.12.1-amd64", None]),
                       lambda c: c["imagenes"].update({datos.imagen_id(1): {"digests": [None]}})):
            cruda = datos.foto([("db", "postgres:16.15", 1)])
            cambio(cruda)
            with self.assertRaises(censo.FotoError):
                censo.limpiar(cruda, AHORA, 900)

    # 22
    def test_parches_que_van_entrando_no_acusan_a_la_rutina(self):
        original = self.s.tomar
        dia = [0]

        def tomar(servidor, ahora, vigencia):
            t = original(servidor, ahora, vigencia)
            t["foto"]["sistema"]["paquetes"] = [{"nombre": f"lib{dia[0]}", "de": "1", "a": "2", "origen": "x-security",
                                                 "seguridad": True}]
            return t
        self.s.tomar = tomar
        for d in range(5):
            dia[0] = d
            self.correr(AHORA + d * DIA)
        self.assertEqual(self.incidentes(), {})

    # 23
    def test_una_respuesta_git_truncada_no_es_una_medicion(self):
        cuerpo = b"ffff" + b"a" * 40 + b" refs/heads/main\n"
        red = Red({"https://forja.ejemplo.test/r.git/info/refs": (200, {}, cuerpo)})
        self.assertIsNone(repos.punta("https://forja.ejemplo.test/r.git", "main", red))


class RecuperarTrasUnCorte(Unico):
    """Hallazgos 2 a 5: un corte entre dos pasos, lo nuevo que no verifica, lo roto que no se cierra solo y el bloqueo
    que sobrevive entre pasadas."""

    def setUp(self):
        super().setUp()
        from test_ejecutor import CON_DATOS
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "datos"}},
                               acciones={"principal": CON_DATOS}, playbooks=["datos"])
        self.s.con_datos = {"db"}

    def test_un_corte_entre_la_copia_y_aplicar_escala_y_bloquea_hasta_resolverlo(self):
        from unittest import mock
        original = estado.intencion

        def intencion(db, cid, servidor, paso, datos_, ahora):
            if paso == "aplicar":
                raise Corte()
            return original(db, cid, servidor, paso, datos_, ahora)
        with mock.patch.object(estado, "intencion", intencion):
            with self.assertRaises(Corte):
                self.aplicar(presente=True)
        self.assertEqual(estado.a_medias(self.db()), [])               # el diario no tiene nada abierto
        self.assertIn("db", self.s.detenidos)
        acta = self.aplicar(presente=True)
        self.assertIn("espera a una persona", acta["motivo"])
        (clave,) = self.incidentes()
        self.assertTrue(clave.startswith("ambiguo-"))
        with open(os.path.join(self.cfg["estado_dir"], "avisos", re.sub(r"[^A-Za-z0-9_-]", "-", clave) + ".txt"),
                  encoding="utf-8") as f:
            self.assertIn("marca m", f.read())
        self.ahora = AHORA + 7 * DIA
        self.assertIn("espera a una persona", self.aplicar(presente=True)["motivo"])      # el bloqueo sigue
        self.s.detenidos.clear()
        self.s.formato["db"] = self.s.servicios["db"]
        self.assertTrue(ejecutor.resuelto(self.cfg, clave, self.ahora))
        self.ahora = AHORA + 14 * DIA
        self.assertEqual(self.aplicar(presente=True)["pasos"][0]["resultado"], "aplicada")

    def test_lo_nuevo_que_corre_tras_un_corte_se_verifica(self):
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen"}})
        self.s.con_datos = set()
        self.s.corte = ("despues", "db")
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.corte = None
        self.s.falla_verificar = {"db"}
        self.aplicar(presente=True)
        self.assertEqual(self.candidata()["estado"], "escalada")
        self.assertEqual(list(self.incidentes().values()), ["a medias"])

    def test_lo_roto_no_se_cierra_porque_la_candidata_desaparece(self):
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen"}})
        self.s.con_datos = set()
        self.s.falla_verificar = {"db"}
        self.s.falla_volver = {"db"}
        self.aplicar(presente=True)
        self.assertEqual(list(self.incidentes().values()), ["vuelta atrás fallida"])
        self.s.detenidos.add("db")                                      # el contenedor quedó detenido: ya no figura
        corrida.correr(self.cfg, AHORA + 60, tomar=self.s.tomar, pedir=datos.kev(self.red), escanear=lambda *a: {})
        self.assertEqual(list(self.incidentes().values()), ["vuelta atrás fallida"])
        self.assertIn("espera a una persona", self.aplicar(presente=True)["motivo"])


class ReinicioRoto(Unico):
    def test_el_sistema_que_ya_no_pide_reinicio_no_cierra_un_reinicio_incompleto(self):
        self.red.hub("library/postgres", [tag("16.15", VIEJA, H)])
        self.cfg = self.config(componentes={"sistema": {"nivel": "patch", "playbook": "reinicio"}},
                               playbooks=["reinicio"], acciones={"principal": REINICIO})
        self.s.pide_reinicio = True
        self.s.no_vuelven = {"db"}
        self.aplicar(presente=True)
        corrida.correr(self.cfg, AHORA + 3600, tomar=self.s.tomar, pedir=datos.kev(self.red), escanear=lambda *a: {})
        self.assertEqual(list(self.incidentes().values()), ["reinicio incompleto"])


class SegundaRevision(Unico):
    """Un caso por cada punto de la segunda vuelta de la revisión independiente, con su número."""

    def correr(self, ahora=None, **k):
        k.setdefault("escanear", lambda *a: {})
        datos.kev(self.red)
        return corrida.correr(self.cfg, ahora or self.ahora, tomar=self.s.tomar, pedir=self.red, **k)

    # 1
    def test_un_corte_al_cerrar_el_intento_no_pierde_el_incidente_ni_el_bloqueo(self):
        from unittest import mock
        from test_ejecutor import CON_DATOS
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "datos"}},
                               acciones={"principal": CON_DATOS}, playbooks=["datos"])
        self.s.con_datos = {"db"}
        self.s.falla_verificar = {"db"}
        self.s.falla_restaurar = {"db"}

        def cerrar(*a):
            raise Corte()
        with mock.patch.object(estado, "cerrar_intento", cerrar):
            with self.assertRaises(Corte):
                self.aplicar(presente=True)
        self.assertEqual(list(self.incidentes().values()), ["vuelta atrás fallida"])
        self.assertEqual(estado.bloqueados(self.db()), {"principal"})
        self.assertIn("espera a una persona", self.aplicar(presente=True)["motivo"])

    # 2
    def test_el_observador_no_cierra_lo_que_la_recuperacion_escalo(self):
        self.s.corte = ("despues", "db")
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.corte = None
        self.s.falla_verificar = {"db"}
        self.aplicar(presente=True)
        self.correr(AHORA + 3600)
        self.assertEqual(list(self.incidentes().values()), ["a medias"])
        self.assertEqual(estado.bloqueados(self.db()), {"principal"})

    # 3
    def test_un_resultado_interior_ilegible_invalida_el_escaneo(self):
        for informe in ({"Results": [None]}, {"Results": [{"Vulnerabilities": "basura"}]},
                        {"Results": [{"Vulnerabilities": [{"Severity": "HIGH"}]}]}):
            r = escaner.escanear(["e"], ["x"], set(), 60, correr=lambda a, t, i=informe: (0, json.dumps(i).encode()))
            self.assertEqual(r["x"]["estado"], "desconocido", informe)

    # 4
    def test_la_urgencia_propone_la_mas_nueva_y_la_comprueba_aunque_haya_una_madura(self):
        self.red.hub("library/postgres", [tag("16.17", RECIENTE, H3), tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": KEV}]}).encode())
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen", "expuesto": True}},
                               escaner={"comando": ["e"]}, ventana_urgente={"desde": "07:00", "hasta": "09:00"})
        estado.estrenar(self.db(), "imagen", "antes", AHORA)
        self.ahora = AHORA + DIA
        acta = self.aplicar(escanear=self.escanear())
        self.assertEqual((acta["pasos"][0]["a"], acta["pasos"][0]["resultado"]), ("16.17", "aplicada"))
        self.assertIn([f"{PG}:16.17@{H3}"], self.escaneos)

    def test_una_madura_urgente_tambien_se_comprueba(self):
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": KEV}]}).encode())
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "playbook": "imagen", "expuesto": True}},
                               escaner={"comando": ["e"]})
        (paso,) = self.aplicar(presente=True, escanear=self.escanear(vulnerable_la_nueva=True))["pasos"]
        self.assertEqual([n for n, ok, _ in paso["compuertas"] if not ok], ["arreglo comprobado"])

    # 5
    def test_sin_la_lista_kev_no_se_cierra_un_kev_avisado(self):
        self.cfg = self.config(componentes={PG: {"nivel": "patch", "expuesto": True}}, escaner={"comando": ["e"]})
        sin = {"Results": [{"Vulnerabilities": [{"VulnerabilityID": KEV, "Severity": "CRITICAL"}]}]}
        escanear = lambda c, ids, cves, t: {i: escaner.resumir(sin, cves) for i in ids}
        self.red.respuestas[fuentes.KEV] = (200, {}, json.dumps({"vulnerabilities": [{"cveID": KEV}]}).encode())
        self.correr(escanear=escanear)
        self.red.respuestas[fuentes.KEV] = (503, {}, b"")
        self.correr(AHORA + 60, escanear=escanear)
        self.assertEqual(list(self.incidentes().values()), ["falla explotada sin arreglo"])

    # 6
    def test_un_servidor_bloqueado_no_le_quita_la_ventana_a_los_demas(self):
        cfg = self.config(servidores=[{"nombre": "bloqueado"}, {"nombre": "principal"}],
                          acciones={"bloqueado": ACCIONES, "principal": ACCIONES})
        estado.escalar(self.db(), "x", "bloqueado", "roto-x", "vuelta atrás fallida", "t", AHORA)
        datos.kev(self.red)
        for semana in range(2):
            self.ahora = AHORA + semana * 7 * DIA
            acta = ejecutor.aplicar(cfg, presente=True, tomar=self.s.tomar, pedir=self.red, correr=self.s.correr,
                                    reloj=self.reloj, dormir=lambda n: None, local=time.gmtime, escanear=lambda *a: {})
        self.assertEqual((acta["servidor"], acta["pasos"][0]["resultado"]), ("principal", "aplicada"))

    # 7
    def test_en_oci_una_madura_ya_vista_no_espera_a_la_nueva(self):
        reg, cfgd = "registro.ejemplo.test", "sha256:" + "e" * 64
        huellas = {"1.0.0": H, "1.0.1": H2, "1.0.2": H3}

        def registro(tags):
            r = {f"https://{reg}/v2/equipo/forja/tags/list": (200, {}, json.dumps({"tags": tags}).encode()),
                 f"https://{reg}/v2/equipo/forja/blobs/{cfgd}": (200, {}, b'{"created": "2026-01-01T00:00:00Z"}')}
            for t in tags:
                r[f"https://{reg}/v2/equipo/forja/manifests/{t}"] = (
                    200, {"docker-content-digest": huellas[t]}, json.dumps({"config": {"digest": cfgd}}).encode())
            return datos.kev(Red(r))
        self.cfg = self.config(componentes={f"{reg}/equipo/forja": {"nivel": "patch"}})
        self.s = Maquina({"forja": f"{reg}/equipo/forja:1.0.0@{H}"})
        self.red = registro(["1.0.0", "1.0.1"])
        self.correr(AHORA)
        self.red = registro(["1.0.0", "1.0.1", "1.0.2"])
        r = self.correr(AHORA + 10 * DIA)
        forja = next(p for p in r["servidores"][0]["piezas"] if "forja" in p["pieza"])
        self.assertEqual((forja["candidata"]["propuesta"], forja["candidata"]["compuerta_version"]), ("1.0.1", "pasaría"))

    def test_una_candidata_que_vuelve_hereda_la_fecha(self):
        self.red.hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.correr(AHORA)
        self.red.hub("library/postgres", [tag("16.17", VIEJA, H3), tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.correr(AHORA + 3 * DIA)
        self.red.hub("library/postgres", [tag("16.16", VIEJA, H2), tag("16.15", VIEJA, H)])
        self.correr(AHORA + 5 * DIA)
        self.assertEqual(self.candidata("16.16")["detectada"], AHORA)

    # 8
    def test_una_imagen_que_el_escaner_nunca_lee_se_avisa(self):
        self.s = Maquina({"db": f"postgres:16.15@{H}", "web": f"ejemplo/web:1.0.0@{H3}"})
        self.red.hub("ejemplo/web", [tag("1.0.0", VIEJA, H3)])
        self.cfg = self.config(escaner={"comando": ["e"]})

        def escanear(c, ids, cves, t):
            return {i: (escaner.resumir(LIMPIO, cves) if n == 0 else {"estado": "desconocido", "motivo": "x"})
                    for n, i in enumerate(ids)}
        self.correr(escanear=escanear)
        self.correr(AHORA + DIA + 60, escanear=escanear)
        self.assertIn("escaner-principal", self.incidentes())

    # 9
    def test_una_pasada_que_falla_no_deja_latido(self):
        from unittest import mock
        with mock.patch.object(corrida, "_correr", side_effect=RuntimeError("roto")):
            with self.assertRaises(RuntimeError):
                self.aplicar(presente=True)
        try:
            with open(os.path.join(self.cfg["estado_dir"], "latido.json"), encoding="utf-8") as f:
                self.assertNotIn("ejecutor", json.load(f))
        except FileNotFoundError:
            pass

    # 10
    def test_un_cierre_sin_avisar_no_se_pierde_si_el_incidente_vuelve(self):
        cfg = self.config(aviso=["avisar"])
        db = self.db()
        estado.incidente(db, "pausa", "vigía en pausa", "texto", AHORA)
        corrida.avisar(cfg, db, AHORA, lambda a, t: 0)
        estado.cerrar_incidente(db, "pausa", AHORA + 60)
        corrida.avisar(cfg, db, AHORA + 120, lambda a, t: 1)              # el cierre no salió
        estado.incidente(db, "pausa", "vigía en pausa", "texto", AHORA + 180)
        rutas = corrida.avisar(cfg, db, AHORA + 240, lambda a, t: 0)
        self.assertTrue(any("resuelto" in os.path.basename(r) for r in rutas))

    # 11
    def test_la_ficha_de_acceso_no_se_pide_a_un_servidor_no_aprobado(self):
        reg = "registro.ejemplo.test"
        red = Red({f"https://{reg}/v2/equipo/forja/tags/list": (
            401, {"www-authenticate": 'Bearer realm="https://ajeno.test/token",service="x"'}, b"")})
        self.assertEqual(fuentes.registro_oci(reg, "equipo/forja", red)["estado"], "desconocido")
        self.assertFalse(any("ajeno" in u for u, _ in red.pedidos))
        red.respuestas["https://ajeno.test/token"] = (200, {}, b'{"token": "t"}')
        fuentes.registro_oci(reg, "equipo/forja", red, ["ajeno.test"])
        self.assertTrue(any("ajeno" in u for u, _ in red.pedidos))

    # 12
    def test_resuelto_completa_una_resolucion_que_quedo_a_medias(self):
        db = self.db()
        estado.escalar(db, "x", "principal", "roto-x", "vuelta atrás fallida", "t", AHORA)
        estado.cerrar_incidente(db, "roto-x", AHORA + 1)                  # el corte cayó después de cerrar
        self.assertEqual(estado.bloqueados(db), {"principal"})
        self.assertTrue(ejecutor.resuelto(self.cfg, "roto-x", AHORA + 2))
        self.assertEqual(estado.bloqueados(self.db()), set())
        self.assertFalse(ejecutor.resuelto(self.cfg, "roto-x", AHORA + 3))

    # 13
    def test_la_alarma_de_parches_se_cierra_cuando_la_rutina_avanza(self):
        original = self.s.tomar
        paquete = ["liba"]

        def tomar(servidor, ahora, vigencia):
            t = original(servidor, ahora, vigencia)
            t["foto"]["sistema"]["paquetes"] = [{"nombre": paquete[0], "de": "1", "a": "2", "origen": "x-security",
                                                 "seguridad": True}]
            return t
        self.s.tomar = tomar
        self.correr(AHORA)
        self.correr(AHORA + 4 * DIA)
        self.assertIn("sistema-seguridad-principal", self.incidentes())
        paquete[0] = "libb"
        self.correr(AHORA + 5 * DIA)
        self.assertNotIn("sistema-seguridad-principal", self.incidentes())

    # 14
    def test_lo_aplicado_y_resuelto_tras_un_corte_cuenta_en_la_ventana(self):
        self.s.corte = ("despues", "db")
        with self.assertRaises(Corte):
            self.aplicar(presente=True)
        self.s.corte = None
        self.aplicar(presente=True)
        self.assertEqual(self.candidata()["estado"], "cerrada")
        self.assertEqual(estado.ventana(self.db(), "2027-01-15")["aplicadas"], 1)


class TerceraRevision(Unico):
    """Un caso por cada punto pendiente de la tercera vuelta, con su número."""

    def correr(self, ahora=None, **k):
        k.setdefault("escanear", lambda *a: {})
        datos.kev(self.red)
        return corrida.correr(self.cfg, ahora or self.ahora, tomar=self.s.tomar, pedir=self.red, **k)

    def registro(self, tags, sin_detalle=()):
        reg, cfgd = "registro.ejemplo.test", "sha256:" + "e" * 64
        huellas = {t: "sha256:" + f"{n + 1:064x}" for n, t in enumerate(["1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4"])}
        r = {f"https://{reg}/v2/equipo/forja/tags/list": (200, {}, json.dumps({"tags": tags}).encode()),
             f"https://{reg}/v2/equipo/forja/blobs/{cfgd}": (200, {}, b'{"created": "2026-01-01T00:00:00Z"}')}
        for t in tags:
            if t not in sin_detalle:
                r[f"https://{reg}/v2/equipo/forja/manifests/{t}"] = (
                    200, {"docker-content-digest": huellas[t]}, json.dumps({"config": {"digest": cfgd}}).encode())
        self.cfg = self.config(componentes={f"{reg}/equipo/forja": {"nivel": "patch"}})
        self.s = Maquina({"forja": f"{reg}/equipo/forja:1.0.0@{huellas['1.0.0']}"})
        self.red = datos.kev(Red(r))

    # 1
    def test_una_vuelta_atras_que_no_verifica_y_un_corte_igual_escalan(self):
        from unittest import mock
        original = self.s.correr
        self.s.correr = lambda argv, t: 1 if argv[1] == "verificar" else original(argv, t)

        def escalar(*a, **k):
            raise Corte()
        with mock.patch.object(estado, "escalar", escalar):
            with self.assertRaises(Corte):
                self.aplicar(presente=True)
        self.aplicar(presente=True)
        self.assertEqual(list(self.incidentes().values()), ["a medias"])
        self.assertEqual(estado.bloqueados(self.db()), {"principal"})

    # 2
    def test_vulnerabilidades_con_otra_forma_no_son_cero(self):
        for raro in ({}, False, 0, ""):
            r = escaner.escanear(["e"], ["x"], set(), 60, correr=lambda a, t, v=raro: (
                0, json.dumps({"Results": [{"Vulnerabilities": v}]}).encode()))
            self.assertEqual(r["x"]["estado"], "desconocido", raro)
        r = escaner.escanear(["e"], ["x"], set(), 60, correr=lambda a, t: (0, b'{"Results": [{"Vulnerabilities": null}]}'))
        self.assertEqual(r["x"]["estado"], "ok")

    # 3
    def test_en_oci_una_madura_vista_antes_no_espera_aunque_salgan_tres_mas(self):
        self.registro(["1.0.0", "1.0.1"])
        self.correr(AHORA)
        self.registro(["1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4"])
        r = self.correr(AHORA + 10 * DIA)
        forja = next(p for p in r["servidores"][0]["piezas"] if "forja" in p["pieza"])
        self.assertEqual((forja["candidata"]["propuesta"], forja["candidata"]["compuerta_version"]), ("1.0.1", "pasaría"))

    # 4
    def test_un_detalle_que_falla_por_dias_se_avisa(self):
        self.registro(["1.0.0", "1.0.1"], sin_detalle=("1.0.1",))
        self.correr(AHORA)
        self.correr(AHORA + 4 * DIA)
        self.assertIn("fuentes-principal", self.incidentes())

    # 5
    def test_escalar_no_pierde_un_cierre_pendiente(self):
        cfg = self.config(aviso=["avisar"])
        db = self.db()
        estado.escalar(db, "x", "principal", "roto-x", "vuelta atrás fallida", "t", AHORA)
        corrida.avisar(cfg, db, AHORA, lambda a, t: 0)
        estado.resolver(db, "roto-x", "x", AHORA + 60)
        corrida.avisar(cfg, db, AHORA + 120, lambda a, t: 1)
        estado.escalar(db, "x", "principal", "roto-x", "vuelta atrás fallida", "t", AHORA + 180)
        rutas = corrida.avisar(cfg, db, AHORA + 240, lambda a, t: 0)
        self.assertTrue(any("resuelto" in os.path.basename(r) for r in rutas))

    # 6
    def test_cada_peticion_a_un_registro_lleva_sus_destinos(self):
        llamadas = []

        def pedir(url, cabeceras=None, destinos=None, **_):
            llamadas.append((url, destinos))
            if "/token" in url:
                return 200, {}, b'{"token": "t"}'
            if (cabeceras or {}).get("Authorization"):
                return 200, {}, b'{"tags": ["1.0.0"]}'
            return 401, {"www-authenticate": 'Bearer realm="https://registro.ejemplo.test/token"'}, b""
        fuentes.registro_oci("registro.ejemplo.test", "equipo/forja", pedir)
        self.assertEqual(len(llamadas), 3)
        self.assertTrue(all(d == {"registro.ejemplo.test"} for _, d in llamadas))

    # 7
    def test_un_corte_al_cerrar_no_cuenta_dos_veces_ni_avisa_dos_veces(self):
        from unittest import mock
        original = estado.cerrar_intento
        veces = []

        def cerrar(*a):
            if not veces:
                veces.append(1)
                raise Corte()
            return original(*a)
        with mock.patch.object(estado, "cerrar_intento", cerrar):
            with self.assertRaises(Corte):
                self.aplicar(presente=True)
            self.aplicar(presente=True)
        self.assertEqual(estado.ventana(self.db(), "2027-01-15")["aplicadas"], 1)
        self.assertEqual(self.candidata()["estado"], "cerrada")


if __name__ == "__main__":
    unittest.main()
