"""Pruebas del componente videos. Sin red, con datos inventados. Las de armado necesitan ffmpeg: sin él, se saltean."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
HERRAMIENTAS = AQUI.parent / "herramientas"
sys.path.insert(0, str(HERRAMIENTAS))
sys.path.insert(0, str(HERRAMIENTAS / "superficies"))

import editar  # noqa: E402
import entorno  # noqa: E402
import locutar  # noqa: E402
import terminal  # noqa: E402
import tomar  # noqa: E402

AJ = dict(editar.DEFECTO)


def _ffmpeg() -> str | None:
    try:
        return entorno.ffmpeg()
    except FileNotFoundError:
        return None


class Segmentos(unittest.TestCase):
    def marcas(self, tramos, escenas=None):
        return {"escenas": escenas or [{"id": "a", "t0": 0.0, "t1": 100.0}], "tramos": tramos}

    def test_trabajo_comprimido_con_un_cuadro_quieto_por_hito(self):
        segs = editar.segmentos(self.marcas([
            {"escena": "a", "accion": "terminal.prompt", "tipo": "trabajo", "t0": 10.0, "t1": 90.0,
             "hitos": [{"t": 30.0, "que": "usuario"}, {"t": 70.0, "que": "planning"}]}]), AJ)
        trabajo = [s for s in segs if not s.get("congelar") and s["s0"] >= 10]
        quietos = [s for s in segs if s.get("congelar")]
        self.assertEqual([q["s0"] for q in quietos], [30.0, 70.0])
        self.assertTrue(all(q["congelar"] == AJ["leer_hito"] for q in quietos))
        total = sum((s["s1"] - s["s0"]) / s["vel"] for s in trabajo)
        self.assertAlmostEqual(total, AJ["trabajo_max"], places=3)

    def test_trabajo_con_compresion_propia(self):
        segs = editar.segmentos(self.marcas([
            {"escena": "a", "accion": "x", "tipo": "trabajo", "t0": 0.0, "t1": 60.0, "comprimir_a": 10}]), AJ)
        self.assertAlmostEqual(sum((s["s1"] - s["s0"]) / s["vel"] for s in segs), 10.0, places=3)

    def test_un_trabajo_corto_no_se_frena(self):
        segs = editar.segmentos(self.marcas([
            {"escena": "a", "accion": "x", "tipo": "trabajo", "t0": 0.0, "t1": 2.0}]), AJ)
        self.assertEqual(segs[0]["vel"], 1.0)

    def test_lectura_con_su_propio_tiempo(self):
        segs = editar.segmentos(self.marcas([
            {"escena": "a", "accion": "x", "tipo": "lectura", "t0": 0.0, "t1": 20.0, "leer": 5}]), AJ)
        self.assertAlmostEqual(segs[0]["s1"], 5.0)

    def test_lo_manual_va_a_su_velocidad(self):
        segs = editar.segmentos(self.marcas([
            {"escena": "a", "accion": "escritorio.manual", "tipo": "manual", "t0": 0.0, "t1": 9.0}]), AJ)
        self.assertEqual(segs[0]["vel"], AJ["vel_manual"])

    def test_el_hueco_entre_pasos_se_acorta(self):
        segs = editar.segmentos(self.marcas([
            {"escena": "a", "accion": "x", "tipo": "pausa", "t0": 5.0, "t1": 6.0}]), AJ)
        self.assertAlmostEqual(segs[0]["s1"] - segs[0]["s0"], AJ["hueco_max"])


class Tapar(unittest.TestCase):
    GUION = {"tapar": [{"x": 0, "y": 0, "w": 100, "h": 40}],
             "escenas": []}

    def test_zonas_por_lapso_y_por_toma(self):
        escena = {"tapar": [{"x": 10, "y": 10, "w": 50, "h": 50, "desde": 20, "hasta": 30, "toma": 2}]}
        self.assertEqual(len(editar.zonas_de(self.GUION, escena, 2, 25, 26)), 2)
        self.assertEqual(len(editar.zonas_de(self.GUION, escena, 1, 25, 26)), 1)   # otra toma
        self.assertEqual(len(editar.zonas_de(self.GUION, escena, 2, 31, 40)), 1)   # fuera del lapso

    def test_filtro(self):
        f = editar.filtro_tapar([{"x": 11, "y": 7, "w": 101, "h": 41, "desde": 12, "hasta": 15}], 10.0, "[0:v]", "[t]")
        self.assertIn("crop=102:42:10:6", f)
        self.assertIn("between(t\\,2.000\\,5.000)", f)
        self.assertTrue(f.endswith("[t]"))
        self.assertEqual(editar.filtro_tapar([], 0, "[0:v]", "[t]"), "[0:v]null[t]")


class Diseno(unittest.TestCase):
    def test_encaje_respeta_la_proporcion(self):
        pw, ph, px, py = editar.encaje(1440, 810, editar.PC)
        self.assertEqual((pw, ph), (1536, 864))
        pw, ph, px, py = editar.encaje(1080, 2228, editar.PC)     # un monitor vertical: entra por el alto
        self.assertEqual(ph, 864)
        self.assertEqual(px, (1920 - pw) // 2)

    def test_nombres_de_video(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "video_261127_v1.mp4").write_bytes(b"")
            (d / "video_261127_v2_interno.mp4").write_bytes(b"")
            self.assertEqual(editar.nombre_video(d, False, "261127").name, "video_261127_v3.mp4")
            self.assertEqual(editar.nombre_video(d, True, "261127").name, "video_261127_v3_interno.mp4")


class Guion(unittest.TestCase):
    def test_grupos(self):
        g = {"escenas": [{"superficie": "terminal"}, {"superficie": "escritorio"}, {"superficie": "filmacion"}]}
        self.assertEqual(tomar.grupo(g), {"terminal", "escritorio"})
        with self.assertRaises(SystemExit):
            tomar.grupo({"escenas": [{"superficie": "web"}, {"superficie": "terminal"}]})
        self.assertEqual(tomar.grupo({"escenas": [{"superficie": "filmacion"}]}), set())

    def test_entorno_solo_acepta_lo_suyo(self):
        with self.assertRaises(SystemExit):
            entorno.aplicar({"entorno": {"PATH": "x"}})
        viejo = os.environ.get("VIDEOS_PRUEBA_X")
        entorno.aplicar({"entorno": {"VIDEOS_PRUEBA_X": "uno"}})
        self.assertEqual(os.environ["VIDEOS_PRUEBA_X"], "uno")
        if viejo is None:
            del os.environ["VIDEOS_PRUEBA_X"]

    def test_sin_raiz_frena(self):
        viejo = os.environ.pop("VIDEOS_RAIZ", None)
        try:
            with self.assertRaises(SystemExit):
                entorno.raiz()
        finally:
            if viejo is not None:
                os.environ["VIDEOS_RAIZ"] = viejo

    def test_locucion(self):
        g = {"placas": {"inicio": {"locucion": "hola"}, "fin": {}},
             "escenas": [{"id": "a", "texto": "uno"}, {"id": "b", "texto": "dos", "locucion": "otra cosa"},
                         {"id": "c"}]}
        self.assertEqual(locutar.a_locutar(g), [("placa_inicio", "hola"), ("a", "uno"), ("b", "otra cosa")])


class Panel(unittest.TestCase):
    def falso(self, tabs):
        def correr(*args, **_):
            if args[:2] == ("workspace", "list"):
                return {"workspaces": [{"workspace_id": "w1", "label": "uno"}, {"workspace_id": "w2", "label": "dos"}]}
            if args[:2] == ("tab", "list"):
                return {"tabs": tabs.get(args[3], [])}
            if args[:2] == ("pane", "list"):
                return {"panes": [{"pane_id": f"{args[3]}:p", "tab_id": t["tab_id"]} for t in tabs.get(args[3], [])]}
            raise AssertionError(args)
        return correr

    def test_resuelve_por_pestana(self):
        c = self.falso({"w1": [{"tab_id": "t1", "label": "Alta"}], "w2": [{"tab_id": "t2", "label": "Otra"}]})
        self.assertEqual(terminal.resolver({"pestana": "Alta"}, c), "w1:p")
        self.assertEqual(terminal.resolver({"panel": "x:p9"}, c), "x:p9")

    def test_pestana_ambigua_frena(self):
        c = self.falso({"w1": [{"tab_id": "t1", "label": "Alta"}], "w2": [{"tab_id": "t2", "label": "Alta"}]})
        with self.assertRaises(SystemExit):
            terminal.resolver({"pestana": "Alta"}, c)
        self.assertEqual(terminal.resolver({"pestana": "Alta", "espacio": "dos"}, c), "w2:p")


@unittest.skipUnless(_ffmpeg(), "sin ffmpeg")
class Armado(unittest.TestCase):
    """Una toma inventada (barras de colores) con trabajo, hitos, una filmación vertical, una zona tapada y voz."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="videos_prueba_"))
        ff = _ffmpeg()
        b = cls.dir / "binarios"
        (b / "voz").mkdir(parents=True)
        (b / "filmacion").mkdir()
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                        "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(b / "toma_1.mkv")], check=True)
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30",
                        "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(b / "filmacion" / "pc.mp4")],
                       check=True)
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                        str(b / "voz" / "pedido.wav")], check=True)
        marcas = {"escenas": [{"id": "pedido", "t0": 0.0, "t1": 8.0}, {"id": "resultado", "t0": 8.0, "t1": 12.0}],
                  "tramos": [
                      {"escena": "pedido", "accion": "terminal.prompt", "tipo": "escribir", "t0": 0.2, "t1": 1.2,
                       "letras": 15},
                      {"escena": "pedido", "accion": "terminal.prompt", "tipo": "trabajo", "t0": 1.3, "t1": 7.5,
                       "hitos": [{"t": 4.0, "que": "usuario creado"}]},
                      {"escena": "resultado", "accion": "escritorio.abrir", "tipo": "carga", "t0": 8.0, "t1": 9.0},
                      {"escena": "resultado", "accion": "pausa", "tipo": "pausa", "t0": 9.0, "t1": 11.0}]}
        (b / "toma_1.marcas.json").write_text(json.dumps(marcas), encoding="utf-8")
        guion = {"titulo": "Alta de prueba", "formato": "pc", "binarios": str(b), "datos_reales": True,
                 "placas": {"inicio": {"titulo": "Alta de prueba"}, "fin": {"titulo": "Fin"}},
                 "escenas": [
                     {"id": "pedido", "superficie": "terminal", "texto": "Le pedís el alta",
                      "tapar": [{"x": 0, "y": 0, "w": 160, "h": 60}]},   # el reloj
                     {"id": "resultado", "superficie": "escritorio", "texto": "Todo listo",
                      "recorte": {"x": 0, "y": 0, "w": 640, "h": 360}},
                     {"id": "pendrive", "superficie": "filmacion", "texto": "Se instala solo",
                      "archivo": "filmacion/pc.mp4",
                      "tramos": [{"desde": 0, "hasta": 2}, {"desde": 2, "hasta": 20, "vel": 9}]}]}
        cls.guion = cls.dir / "guion.yaml"
        import yaml
        cls.guion.write_text(yaml.safe_dump(guion, allow_unicode=True), encoding="utf-8")
        os.environ["VIDEOS_BASE"] = str(cls.dir)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("VIDEOS_BASE", None)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_difusion_e_interno(self):
        dif = editar.armar(self.guion, [1])
        interno = editar.armar(self.guion, [1], interno=True)
        self.assertTrue(interno.stem.endswith("_interno"))
        datos = json.loads(dif.with_suffix(".escenas.json").read_text(encoding="utf-8"))
        tramos = {e["escena"]: e for e in datos["escenas"]}
        self.assertEqual([e["escena"] for e in datos["escenas"]],
                         ["placa_inicio", "pedido", "resultado", "pendrive", "placa_fin"])
        # la voz (6 s) alarga la escena del pedido
        self.assertGreaterEqual(tramos["pedido"]["t1"] - tramos["pedido"]["t0"], 6 + AJ["voz_retardo"])
        # la filmación: 2 s a velocidad real + 18 s a ×9 = 4 s
        self.assertAlmostEqual(tramos["pendrive"]["t1"] - tramos["pendrive"]["t0"], 4.0, delta=0.15)
        self.assertAlmostEqual(editar.duracion(dif), datos["escenas"][-1]["t1"], delta=0.3)
        # en la de difusión, la zona tapada tiene mucho menos detalle que en la interna
        t = (tramos["pedido"]["t0"] + 0.5)
        detalle = [self._detalle(v, t) for v in (dif, interno)]
        self.assertLess(detalle[0], detalle[1] * 0.5)

    def test_datos_reales_sin_zonas_frena(self):
        import yaml
        g = yaml.safe_load(self.guion.read_text(encoding="utf-8"))
        for e in g["escenas"]:
            e.pop("tapar", None)
        otro = self.dir / "sin_zonas.yaml"
        otro.write_text(yaml.safe_dump(g, allow_unicode=True), encoding="utf-8")
        with self.assertRaises(SystemExit):
            editar.armar(otro, [1])

    def _detalle(self, video: Path, t: float) -> float:
        """Cuánto cambia la imagen de un píxel al de al lado sobre el reloj de la toma (la zona tapada)."""
        from PIL import Image
        import io
        import numpy as np
        r = subprocess.run([_ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
        img = np.asarray(Image.open(io.BytesIO(r.stdout)).convert("L"), dtype=float)
        pw, ph, px, py = editar.encaje(1280, 720, editar.PC)
        k = pw / 1280
        zona = img[int(py + 4 * k):int(py + 36 * k), int(px + 4 * k):int(px + 96 * k)]
        return float(np.abs(np.diff(zona, axis=1)).mean())


if __name__ == "__main__":
    unittest.main()
