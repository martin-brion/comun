"""Pruebas del atlas de una base de conocimiento. Solo las bases inventadas del ejemplo, sin red."""
import copy
import io
import json
import os
import posixpath
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from informe import atlas, forma, lector, resumenes
from informe.__main__ import main
from informe.armado import MANIFIESTO, componer as componer_informe, escribir
from informe.datos import ErrorDatos, validar

AQUI = Path(__file__).resolve().parent.parent
EJ = AQUI / "ejemplo" / "atlas"
CASA, MODELO, CADENA = EJ / "casa", EJ / "modelo", EJ / "cadena"


def forma_de(nombre):
    return json.loads((EJ / nombre).read_text(encoding="utf-8"))


def leer(nombre_forma, base, recorte=None, cambio=None):
    d = forma_de(nombre_forma)
    if cambio:
        cambio(d)
    return lector.leer(forma.validar(d), base, recorte)


def sitio(nombre_forma, base, recorte=None, cache=None):
    return atlas.componer(leer(nombre_forma, base, recorte), cache)[0]


def texto(archivos, ruta):
    return archivos[ruta].decode("utf-8")


def vinculos_internos(archivos, ruta):
    """Los destinos de los vínculos internos de una página, resueltos contra la raíz del sitio."""
    html = texto(archivos, ruta)
    carpeta = posixpath.dirname(ruta)
    for destino in re.findall(r'(?:href|src)="([^"#:]+)(?:#[^"]*)?"', html):
        yield posixpath.normpath(posixpath.join(carpeta, destino))


class Forma(unittest.TestCase):
    def falla(self, cambio, espera, nombre="forma-areas.json"):
        d = forma_de(nombre)
        cambio(d)
        with self.assertRaises(ErrorDatos) as c:
            forma.validar(d)
        self.assertIn(espera, str(c.exception))

    def test_las_formas_del_ejemplo_valen(self):
        self.assertEqual(forma.validar(forma_de("forma-areas.json"))["forma"], "areas")
        self.assertEqual(len(forma.validar(forma_de("forma-cadena.json"))["eslabones"]), 3)

    def test_una_clave_de_mas(self):
        self.falla(lambda d: d.update(extra=1), "clave desconocida: extra")
        self.falla(lambda d: d["areas"].update(otra=1), "clave desconocida: otra")

    def test_una_ruta_no_sale_de_la_base(self):
        for ruta in ("../afuera.md", "/LEEME.md", "C:LEEME.md", "a\\b.md", "a//b.md", "./LEEME.md", "a/../b.md"):
            self.falla(lambda d, r=ruta: d.update(orienta=r), "relativa a la base", )

    def test_versiones_sin_indice_no(self):
        self.falla(lambda d: d["eslabones"][2].pop("indice"), "van juntos", "forma-cadena.json")

    def test_eslabones_uno_dentro_de_otro_no(self):
        self.falla(lambda d: d["eslabones"][1].update(carpeta="fuentes/sub"), "una dentro de otra", "forma-cadena.json")

    def test_mas_de_cinco_eslabones_no(self):
        def cambio(d):
            d["eslabones"] = [{"carpeta": "c%d" % i, "titulo": "C%d" % i} for i in range(6)]
        self.falla(cambio, "de uno a cinco", "forma-cadena.json")

    def test_con_varias_salidas_hay_que_elegir(self):
        f = forma.validar(forma_de("forma-areas.json"))
        with self.assertRaises(ErrorDatos) as c:
            forma.recorte(f)
        self.assertIn("elegí una con --recorte", str(c.exception))
        with self.assertRaises(ErrorDatos):
            forma.recorte(f, "otra")
        self.assertEqual(forma.recorte(f, "invitado")[0], "invitado")

    def test_sin_salidas_hay_una_completa(self):
        d = forma_de("forma-areas.json")
        d.pop("salidas")
        self.assertEqual(forma.recorte(forma.validar(d)), ("completa", [["**", "borradores"]]))

    def test_ocultar(self):
        p = lambda r: forma._patron(r, "x")  # noqa: E731
        self.assertTrue(forma.oculto([p("areas/cobranzas")], "areas/cobranzas/reglas.md"))
        self.assertTrue(forma.oculto([p("areas/cobranzas")], "AREAS/Cobranzas"))
        self.assertFalse(forma.oculto([p("areas/cobranzas")], "areas/cobranzas-2/reglas.md"))
        self.assertTrue(forma.oculto([p("**/borradores")], "areas/altas/borradores/idea.md"))
        self.assertTrue(forma.oculto([p("**/borradores")], "borradores"))
        self.assertTrue(forma.oculto([p("*/privado")], "x/privado/a.md"))
        self.assertFalse(forma.oculto([p("*/privado")], "x/y/privado/a.md"))
        self.assertTrue(forma.oculto([], ".git/config"))
        with self.assertRaises(ErrorDatos):
            p("borr*")


class Lector(unittest.TestCase):
    def test_dos_areas_y_tres_procedimientos(self):
        a = leer("forma-areas.json", CASA, "completa")
        self.assertEqual([x["nombre"] for x in a["areas"]], ["Altas", "Cobranzas"])
        self.assertEqual([p["nombre"] for x in a["areas"] for p in x["procedimientos"]],
                         ["Alta de cliente", "Baja de cliente", "Reclamo de pago"])

    def test_los_pasos_lee_y_deja_salen_de_sus_secciones(self):
        alta, baja = leer("forma-areas.json", CASA, "completa")["areas"][0]["procedimientos"]
        self.assertIn("Pedir el nombre corto", alta["pasos"])
        self.assertIn("```mermaid", alta["pasos"])
        self.assertIn("La plantilla de ficha", alta["lee"])
        self.assertIn("fichas/<nombre>.md", alta["deja"])
        self.assertIsNone(baja["deja"])                    # la baja no dice qué deja: no se inventa
        self.assertEqual([o["ruta"] for o in alta["otros"]],
                         ["areas/altas/procedimientos/alta-de-cliente/plantilla-de-ficha.md"])

    def test_lo_oculto_y_lo_que_no_es_markdown_no_se_lee(self):
        a = leer("forma-areas.json", CASA, "completa")
        leidos = set(a["base"].docs)
        self.assertFalse(any("borradores" in r for r in leidos))
        self.assertFalse(any(r.endswith(".csv") for r in leidos))
        b = leer("forma-areas.json", CASA, "invitado")
        self.assertFalse(any("cobranzas" in r for r in b["base"].docs))

    def test_una_base_vacia_dice_sin_definir(self):
        a = leer("forma-areas.json", MODELO, "completa")
        self.assertEqual(a["orienta"]["estado"], "vacio")
        self.assertEqual(a["rutea"], {"ruta": "RUTEO.md", "estado": "falta"})
        self.assertEqual(a["funciones"]["estado"], "vacio")
        altas, cobranzas = a["areas"]
        self.assertEqual(altas["reglas"]["estado"], "vacio")
        self.assertEqual(cobranzas["reglas"], {"ruta": "areas/cobranzas/reglas.md", "estado": "falta"})
        self.assertTrue(all(p["documento"]["estado"] == "vacio" for x in a["areas"] for p in x["procedimientos"]))

    def test_una_base_sin_la_estructura_frena_diciendo_que_falta(self):
        with self.assertRaises(ErrorDatos) as c:
            leer("forma-cadena.json", CASA)
        mensaje = str(c.exception)
        for falta in ("fuentes/", "revision/", "notas/"):
            self.assertIn("falta la carpeta %s" % falta, mensaje)
        with self.assertRaises(ErrorDatos) as c:
            leer("forma-areas.json", CADENA, "completa")
        self.assertIn("falta la carpeta areas/ (areas.carpeta)", str(c.exception))

    def test_una_base_que_no_existe_frena(self):
        with self.assertRaises(ErrorDatos):
            leer("forma-areas.json", EJ / "no-existe", "completa")

    def test_sin_la_declaracion_frena(self):
        with self.assertRaises(ErrorDatos) as c:
            atlas.leer_forma(EJ / "no-existe.json")
        self.assertIn("falta la declaración de la forma", str(c.exception))

    def test_un_recorte_no_puede_ocultar_una_carpeta_declarada(self):
        def cambio(d):
            d["salidas"]["invitado"]["ocultar"] = ["areas"]
        with self.assertRaises(ErrorDatos) as c:
            leer("forma-areas.json", CASA, "invitado", cambio)
        self.assertIn('la salida "invitado" oculta areas/', str(c.exception))

    def test_un_enlace_dentro_de_la_base_frena(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "casa"
            shutil.copytree(CASA, base)
            try:
                (base / "areas" / "altas" / "atajo").symlink_to(Path(tmp), target_is_directory=True)
            except OSError:
                self.skipTest("este sistema no deja crear enlaces sin permisos")
            with self.assertRaises(ErrorDatos) as c:
                leer("forma-areas.json", base, "completa")
            self.assertIn("no se siguen enlaces", str(c.exception))

    def test_un_documento_que_no_es_utf8_frena_y_dice_cual(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "casa"
            shutil.copytree(CASA, base)
            (base / "areas" / "altas" / "raro.md").write_bytes(b"# T\n\n\xff\xfe")
            with self.assertRaises(ErrorDatos) as c:
                leer("forma-areas.json", base, "completa")
            self.assertIn("areas/altas/raro.md no es texto UTF-8", str(c.exception))


class Cadena(unittest.TestCase):
    def test_vigente_y_antecedentes(self):
        notas = leer("forma-cadena.json", CADENA)["eslabones"][2]
        piezas = {p["nombre"]: p for p in notas["piezas"]}
        self.assertEqual(sorted(piezas), ["Altas", "Horarios", "Respaldo"])
        self.assertEqual(piezas["Respaldo"]["vigente"], "notas/respaldo-v2.md")
        self.assertEqual(piezas["Respaldo"]["antecedentes"], ["notas/respaldo-v1.md"])
        self.assertIsNone(piezas["Horarios"]["vigente"])
        self.assertEqual(piezas["Horarios"]["antecedentes"], ["notas/horarios-v1.md"])

    def test_las_versiones_se_ordenan_por_numero(self):
        e = {"carpeta": "n", "versiones": "sufijo"}
        piezas = lector._piezas(e, ["n/a-v10.md", "n/a-v2.md", "n/a_v1.md", "n/b.md"])
        self.assertEqual(piezas[0]["versiones"], ["n/a_v1.md", "n/a-v2.md", "n/a-v10.md"])
        self.assertEqual(piezas[1]["versiones"], ["n/b.md"])

    def test_versiones_por_carpeta(self):
        e = {"carpeta": "n", "versiones": "carpeta"}
        piezas = lector._piezas(e, ["n/a/1.md", "n/a/2.md", "n/suelta.md"])
        self.assertEqual([(p["clave"], p["versiones"]) for p in piezas],
                         [("a/", ["n/a/1.md", "n/a/2.md"]), ("suelta", ["n/suelta.md"])])

    def test_las_flechas_salen_de_los_vinculos(self):
        a = leer("forma-cadena.json", CADENA)
        nombres = [[p["nombre"] for p in e["piezas"]] for e in a["eslabones"]]
        flechas = {(nombres[i][j], nombres[k][l]) for (i, j), (k, l) in a["flechas"]}
        self.assertEqual(flechas, {("Charla con el equipo", "Altas"), ("Encuesta de marzo", "Respaldo"),
                                   ("Altas", "Altas"), ("Respaldo", "Respaldo")})

    def copia(self, tmp, indice):
        base = Path(tmp) / "cadena"
        shutil.copytree(CADENA, base)
        (base / "notas" / "VIGENTES.md").write_text(indice, encoding="utf-8")
        return base

    def test_dos_vigentes_de_la_misma_pieza_frenan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self.copia(tmp, "- [a](respaldo-v1.md)\n- [b](respaldo-v2.md)\n")
            with self.assertRaises(ErrorDatos) as c:
                leer("forma-cadena.json", base)
            self.assertIn("dos versiones vigentes", str(c.exception))

    def test_un_vigente_que_no_existe_frena(self):
        with tempfile.TemporaryDirectory() as tmp:
            for indice, espera in (("- [a](respaldo-v9.md)\n", "no es un archivo de su eslabón"),
                                   ("- [a](../../afuera.md)\n", "fuera de la base"),
                                   ("- [a](../revision/respaldo.md)\n", "no es un archivo de su eslabón")):
                base = self.copia(tmp, indice)
                with self.assertRaises(ErrorDatos, msg=indice) as c:
                    leer("forma-cadena.json", base)
                self.assertIn(espera, str(c.exception))
                shutil.rmtree(base)

    def test_sin_el_indice_frena(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self.copia(tmp, "")
            (base / "notas" / "VIGENTES.md").unlink()
            with self.assertRaises(ErrorDatos) as c:
                leer("forma-cadena.json", base)
            self.assertIn("falta el índice notas/VIGENTES.md", str(c.exception))

    def test_un_vinculo_dentro_de_codigo_no_marca_vigente(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self.copia(tmp, "- [a](respaldo-v2.md)\n\n```\n[b](respaldo-v1.md)\n```\n")
            notas = leer("forma-cadena.json", base)["eslabones"][2]
            self.assertEqual(next(p for p in notas["piezas"] if p["nombre"] == "Respaldo")["vigente"],
                             "notas/respaldo-v2.md")


class Paginas(unittest.TestCase):
    def test_arma_una_pagina_por_nivel(self):
        archivos = sitio("forma-areas.json", CASA, "completa")
        paginas = sorted(r for r in archivos if r.endswith(".html"))
        self.assertIn("index.html", paginas)
        self.assertEqual([r for r in paginas if r.startswith("area/")], ["area/altas.html", "area/cobranzas.html"])
        self.assertEqual([r for r in paginas if r.startswith("proc/")],
                         ["proc/altas-alta-de-cliente.html", "proc/altas-baja-de-cliente.html",
                          "proc/cobranzas-reclamo-de-pago.html"])
        self.assertEqual(len([r for r in paginas if r.startswith("doc/")]), 10)

    def test_cada_vinculo_interno_tiene_destino(self):
        for nombre, base, recorte in (("forma-areas.json", CASA, "completa"), ("forma-areas.json", CASA, "invitado"),
                                      ("forma-areas.json", MODELO, "completa"), ("forma-cadena.json", CADENA, None)):
            archivos = sitio(nombre, base, recorte)
            for pagina in (r for r in archivos if r.endswith(".html")):
                for destino in vinculos_internos(archivos, pagina):
                    self.assertIn(destino, archivos, "%s → %s" % (pagina, destino))

    def test_se_entra_por_el_mapa_y_se_vuelve_por_el_camino(self):
        archivos = sitio("forma-areas.json", CASA, "completa")
        indice = texto(archivos, "index.html")
        self.assertRegex(indice, r'<a class="mp-vinculo" href="area/altas.html" aria-label="Altas">')
        self.assertIn('href="proc/altas-alta-de-cliente.html"', indice)
        area = texto(archivos, "area/altas.html")
        self.assertIn('href="../proc/altas-alta-de-cliente.html"', area)
        proc = texto(archivos, "proc/altas-alta-de-cliente.html")
        doc = "doc/areas-altas-procedimientos-alta-de-cliente-procedimiento.html"
        self.assertIn('href="../%s"' % doc, proc)
        camino = re.search(r'<nav class="camino".*?</nav>', texto(archivos, doc)).group(0)
        self.assertEqual(re.findall(r'href="([^"]+)"', camino),
                         ["../index.html", "../area/altas.html", "../proc/altas-alta-de-cliente.html"])
        self.assertIn('aria-current="page">Alta de un cliente<', camino)

    def test_el_procedimiento_muestra_sus_pasos_y_dice_lo_que_falta(self):
        archivos = sitio("forma-areas.json", CASA, "completa")
        alta = texto(archivos, "proc/altas-alta-de-cliente.html")
        self.assertIn("Pedir el nombre corto y comprobar que no exista.", alta)
        self.assertIn('<pre class="mermaid">', alta)
        self.assertIn("mermaid.min.js", alta)              # la librería, solo donde hay un diagrama
        self.assertNotIn("mermaid.min.js", texto(archivos, "area/altas.html"))
        baja = texto(archivos, "proc/altas-baja-de-cliente.html")
        self.assertEqual(baja.count("Sin definir"), 1)     # le falta «Qué deja»
        self.assertIn('<section id="deja"><h2>Qué deja</h2><p class="sin-definir">', baja)

    def test_una_base_modelo_muestra_cada_casillero_sin_definir(self):
        archivos = sitio("forma-areas.json", MODELO, "completa")
        indice = texto(archivos, "index.html")
        self.assertIn("Sin definir · RUTEO.md", indice)
        self.assertEqual(indice.count('<g class="mp-apagado">'), 5)   # reglas de base, ruteo, dos áreas, el asistente
        proc = texto(archivos, "proc/altas-alta-de-cliente.html")
        self.assertEqual(proc.count('<span class="pildora sin">Sin definir</span>'), 4)   # el archivo y sus tres partes
        self.assertIn("areas/altas/procedimientos/alta-de-cliente/procedimiento.md", proc)
        cobranzas = texto(archivos, "area/cobranzas.html")
        self.assertIn("<code>areas/cobranzas/reglas.md</code><span class=\"suave\"> · falta el archivo", cobranzas)
        self.assertFalse([r for r in archivos if r.startswith("doc/")])          # nada vacío lleva página

    def test_cadena_vigente_a_la_vista_y_antecedentes_plegados(self):
        archivos = sitio("forma-cadena.json", CADENA)
        notas = texto(archivos, "eslabon/notas.html")
        respaldo = re.search(r'<article class="pieza" id="pieza-3">.*?</article>', notas).group(0)
        self.assertIn('<span class="pildora vig">Vigente</span> <a href="../doc/notas-respaldo-v2.html">', respaldo)
        self.assertIn('<details class="antecedentes"><summary>Antecedentes (1)</summary>', respaldo)
        self.assertIn("Ninguna versión figura como vigente en el índice.", notas)
        viejo = texto(archivos, "doc/notas-respaldo-v1.html")
        self.assertIn('Versión reemplazada. La vigente es <a href="../doc/notas-respaldo-v2.html">', viejo)
        nuevo = texto(archivos, "doc/notas-respaldo-v2.html")
        self.assertIn("Antecedentes (1)", nuevo)
        indice = texto(archivos, "index.html")
        self.assertEqual(len(re.findall(r'<path class="mp-flecha"', indice)), 4)

    def test_el_recorte_de_invitado_no_lleva_lo_excluido(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "invitado"
            archivos = sitio("forma-areas.json", CASA, "invitado")
            escribir(archivos, salida)
            todo = sorted(p for p in salida.rglob("*") if p.is_file())
            self.assertIn(salida / MANIFIESTO, todo)
            self.assertTrue(any(p.parent.name == "estatico" for p in todo))
            for p in todo:
                contenido = p.read_bytes().decode("utf-8").casefold()
                nombre = p.relative_to(salida).as_posix().casefold()
                for marca in ("cobranzas", "marca-reservada", "marca-borrador", "marca-csv", "borradores"):
                    self.assertNotIn(marca, contenido, "%s en %s" % (marca, p))
                    self.assertNotIn(marca, nombre)
        completa = sitio("forma-areas.json", CASA, "completa")
        self.assertIn("area/cobranzas.html", completa)

    def test_sale_igual_dos_veces(self):
        for nombre, base, recorte in (("forma-areas.json", CASA, "completa"), ("forma-cadena.json", CADENA, None)):
            self.assertEqual(sitio(nombre, base, recorte), sitio(nombre, base, recorte))
        with tempfile.TemporaryDirectory() as tmp:
            for n in (1, 2):
                escribir(sitio("forma-areas.json", CASA, "completa"), Path(tmp) / str(n))
            uno = {p.relative_to(Path(tmp) / "1").as_posix(): p.read_bytes() for p in (Path(tmp) / "1").rglob("*")
                   if p.is_file()}
            dos = {p.relative_to(Path(tmp) / "2").as_posix(): p.read_bytes() for p in (Path(tmp) / "2").rglob("*")
                   if p.is_file()}
            self.assertEqual(uno, dos)

    def test_rearmar_otro_recorte_no_deja_restos(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "s"
            escribir(sitio("forma-areas.json", CASA, "completa"), salida)
            self.assertTrue((salida / "area" / "cobranzas.html").is_file())
            escribir(sitio("forma-areas.json", CASA, "invitado"), salida)
            self.assertFalse((salida / "area" / "cobranzas.html").exists())

    def test_lo_de_los_documentos_se_escapa(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "casa"
            shutil.copytree(CASA, base)
            (base / "areas" / "altas" / "reglas.md").write_text(
                '# <script>x</script>\n\nUn "><img src=x onerror=y> y más.\n', encoding="utf-8")
            archivos = sitio("forma-areas.json", base, "completa")
            for r in archivos:
                if r.endswith(".html"):
                    self.assertNotIn("<script>x", texto(archivos, r), r)
                    self.assertNotIn("<img", texto(archivos, r), r)

    def test_muchas_areas_no_rompen_el_mapa(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "casa"
            shutil.copytree(CASA, base)
            for i in range(20):
                (base / "areas" / ("extra-%02d" % i)).mkdir()
                (base / "areas" / ("extra-%02d" % i) / "reglas.md").write_text("# R\n\nTexto.\n", encoding="utf-8")
            archivos = sitio("forma-areas.json", base, "completa")
            indice = texto(archivos, "index.html")
            self.assertIn("y 8 áreas más", indice)
            self.assertEqual(indice.count('href="area/extra-'), 20 + 12)   # 12 en el dibujo y las 20 en la lista


class Resumenes(unittest.TestCase):
    def todo_resumido(self, atl, ruta_cache):
        nuevos = {r: {"huella": h, "resumen": "Resumen escrito por fuera de %s." % r}
                  for h, r in atlas.pendientes(atl, resumenes.cargar(ruta_cache, debe_existir=False))}
        archivo = Path(ruta_cache).with_name("nuevos.json")
        archivo.write_text(resumenes.como_texto(nuevos), encoding="utf-8")
        return resumenes.incorporar(archivo, atl["base"].raiz, ruta_cache)

    def test_sin_resumen_usa_el_primer_parrafo(self):
        archivos = sitio("forma-areas.json", CASA, "completa")
        self.assertIn("Un cliente se da de alta con su nombre corto", texto(archivos, "area/altas.html"))

    def test_cambiar_un_archivo_invalida_solo_su_resumen(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "casa"
            shutil.copytree(CASA, base)
            cache = Path(tmp) / "resumenes.json"
            atl = leer("forma-areas.json", base, "completa")
            todos = atlas.pendientes(atl, {})
            self.assertGreater(len(todos), 3)
            self.assertEqual(self.todo_resumido(atl, cache), (len(todos), 0))
            guardada = resumenes.cargar(cache)
            self.assertEqual(atlas.pendientes(leer("forma-areas.json", base, "completa"), guardada), [])
            archivos = atlas.componer(leer("forma-areas.json", base, "completa"), guardada)[0]
            self.assertIn("Resumen escrito por fuera de areas/altas/reglas.md.", texto(archivos, "area/altas.html"))
            # cambia un solo archivo
            reglas = base / "areas" / "altas" / "reglas.md"
            reglas.write_bytes(reglas.read_bytes() + b"\n- Una regla nueva.\n")
            atl = leer("forma-areas.json", base, "completa")
            self.assertEqual([r for _, r in atlas.pendientes(atl, guardada)], ["areas/altas/reglas.md"])
            archivos, sin_resumen = atlas.componer(atl, guardada)
            self.assertEqual(sin_resumen, ["areas/altas/reglas.md"])
            area = texto(archivos, "area/altas.html")
            self.assertNotIn("Resumen escrito por fuera de areas/altas/reglas.md.", area)
            self.assertIn("Un cliente se da de alta con su nombre corto", area)
            self.assertIn("Resumen escrito por fuera de areas/altas/procedimientos/alta-de-cliente/procedimiento.md.",
                          area)

    def test_incorporar_frena_si_la_huella_no_es_la_de_hoy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "resumenes.json"
            nuevos = Path(tmp) / "nuevos.json"
            nuevos.write_text(resumenes.como_texto({
                "LEEME.md": {"huella": "sha256:" + "0" * 64, "resumen": "Otro."},
                "RUTEO.md": {"huella": resumenes.huella((CASA / "RUTEO.md").read_bytes()), "resumen": "Bien."}}),
                encoding="utf-8")
            with self.assertRaises(ErrorDatos) as c:
                resumenes.incorporar(nuevos, CASA, cache)
            self.assertIn("LEEME.md cambió", str(c.exception))
            self.assertFalse(cache.exists())             # no escribió nada

    def test_incorporar_rechaza_lo_que_no_es_de_la_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache, nuevos = Path(tmp) / "r.json", Path(tmp) / "n.json"
            for ruta in ("../afuera.md", "no-existe.md"):
                nuevos.write_text(json.dumps({"formato": "resumenes-1", "resumenes": {
                    ruta: {"huella": "sha256:" + "0" * 64, "resumen": "x"}}}), encoding="utf-8")
                with self.assertRaises(ErrorDatos, msg=ruta):
                    resumenes.incorporar(nuevos, CASA, cache)
            self.assertFalse(cache.exists())

    def test_un_resumen_vacio_se_saltea(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache, nuevos = Path(tmp) / "r.json", Path(tmp) / "n.json"
            nuevos.write_text(resumenes.como_texto({
                "LEEME.md": {"huella": resumenes.huella((CASA / "LEEME.md").read_bytes()), "resumen": ""},
                "RUTEO.md": {"huella": resumenes.huella((CASA / "RUTEO.md").read_bytes()), "resumen": "Rutea."}}),
                encoding="utf-8")
            self.assertEqual(resumenes.incorporar(nuevos, CASA, cache), (1, 1))
            self.assertEqual(sorted(resumenes.cargar(cache)), ["RUTEO.md"])

    def test_una_cache_mal_escrita_frena(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "r.json"
            for malo in ('{"formato": "otro", "resumenes": {}}', "no es json",
                         '{"formato": "resumenes-1", "resumenes": {"LEEME.md": {"huella": "x", "resumen": "y"}}}'):
                cache.write_text(malo, encoding="utf-8")
                with self.assertRaises(ErrorDatos, msg=malo):
                    resumenes.cargar(cache)


class Comando(unittest.TestCase):
    def correr(self, *argumentos):
        salida, errores = io.StringIO(), io.StringIO()
        with redirect_stdout(salida), redirect_stderr(errores):
            codigo = main(["atlas"] + list(argumentos))
        return codigo, salida.getvalue(), errores.getvalue()

    def test_armar_pendientes_e_incorporar(self):
        f = str(EJ / "forma-areas.json")
        with tempfile.TemporaryDirectory() as tmp:
            cache = str(Path(tmp) / "resumenes.json")
            codigo, salida, _ = self.correr("armar", f, "--base", str(CASA), "--recorte", "invitado", "--salida",
                                            str(Path(tmp) / "s"))
            self.assertEqual(codigo, 0)
            self.assertIn("ARMADO", salida)
            codigo, salida, _ = self.correr("pendientes", f, "--base", str(CASA), "--recorte", "completa",
                                            "--resumenes", cache)
            self.assertEqual(codigo, 0)
            lineas = salida.splitlines()
            self.assertTrue(all(re.match(r"^sha256:[0-9a-f]{64} \S+\.md$", l) for l in lineas))
            self.assertIn("areas/altas/reglas.md", [l.split(" ", 1)[1] for l in lineas])
            codigo, salida, _ = self.correr("pendientes", f, "--base", str(CASA), "--recorte", "completa",
                                            "--resumenes", cache, "--json")
            plantilla = json.loads(salida)
            for entrada in plantilla["resumenes"].values():
                entrada["resumen"] = "Escrito por fuera."
            nuevos = Path(tmp) / "nuevos.json"
            nuevos.write_text(json.dumps(plantilla, ensure_ascii=False), encoding="utf-8")
            codigo, salida, _ = self.correr("incorporar", str(nuevos), "--base", str(CASA), "--resumenes", cache)
            self.assertEqual((codigo, salida.startswith("INCORPORADOS: %d" % len(lineas))), (0, True))
            codigo, salida, _ = self.correr("pendientes", f, "--base", str(CASA), "--recorte", "completa",
                                            "--resumenes", cache)
            self.assertEqual((codigo, salida), (0, ""))

    def test_una_base_sin_su_forma_dice_que_falta(self):
        codigo, _, errores = self.correr("validar", str(EJ / "forma-cadena.json"), "--base", str(CASA))
        self.assertEqual(codigo, 1)
        self.assertIn("NO ES VÁLIDA: la base no tiene la forma declarada: falta la carpeta fuentes/", errores)

    def test_con_varias_salidas_pide_el_recorte(self):
        codigo, _, errores = self.correr("validar", str(EJ / "forma-areas.json"), "--base", str(CASA))
        self.assertEqual(codigo, 1)
        self.assertIn("--recorte", errores)

    def test_el_informe_de_siempre_sigue_igual(self):
        salida, errores = io.StringIO(), io.StringIO()
        with redirect_stdout(salida), redirect_stderr(errores):
            codigo = main(["validar", str(AQUI / "ejemplo" / "datos.json"), "--raiz",
                           "casa=%s" % (AQUI / "ejemplo" / "fuentes")])
        self.assertEqual(codigo, 0)


class Revision(unittest.TestCase):
    """Los casos concretos de la revisión independiente, uno por defecto."""

    def test_un_nombre_que_windows_abre_igual_no_elude_el_recorte(self):
        def cambio(d):
            d["orienta"] = "areas/altas./reglas.md"
            d["ocultar"] = ["areas/altas"]
        with self.assertRaises(ErrorDatos) as c:
            leer("forma-areas.json", CASA, "completa", cambio)
        self.assertIn("relativa a la base", str(c.exception))

    @unittest.skipUnless(os.name == "nt", "solo Windows abre «altas.» como «altas»")
    def test_el_recorte_se_decide_con_el_nombre_del_disco(self):
        base = lector.Base(CASA, [forma._patron("areas/altas", "x")])
        self.assertIsNone(base.referencia("areas/altas./reglas.md"))
        with self.assertRaises(ErrorDatos):
            base.doc("areas/altas./reglas.md")

    def test_la_cache_no_se_escribe_a_traves_de_un_enlace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ajeno = Path(tmp) / "ajeno.txt"
            ajeno.write_text("ajeno", encoding="utf-8")
            cache = Path(tmp) / "resumenes.json"
            try:
                Path(str(cache) + ".nuevo").symlink_to(ajeno)
            except OSError:
                self.skipTest("este sistema no deja crear enlaces sin permisos")
            resumenes.guardar(cache, {})
            self.assertEqual(ajeno.read_text(encoding="utf-8"), "ajeno")
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["resumenes"], {})

    def test_un_archivo_declarado_que_no_es_markdown_no_se_lee(self):
        with self.assertRaises(ErrorDatos) as c:
            leer("forma-areas.json", CASA, "completa", lambda d: d.update(orienta="areas/altas/listado.csv"))
        self.assertIn("tiene que ser un archivo .md", str(c.exception))
        with self.assertRaises(ErrorDatos):
            lector.Base(CASA, []).doc("areas/altas/listado.csv")

    def base_en_carpetas(self, tmp, indice, archivos, vacias=()):
        base = Path(tmp) / "b"
        (base / "notas").mkdir(parents=True)
        (base / "LEEME.md").write_text("# B\n\nUna base.\n", encoding="utf-8")
        (base / "notas" / "VIGENTES.md").write_text(indice, encoding="utf-8")
        for ruta, contenido in archivos.items():
            (base / ruta).parent.mkdir(parents=True, exist_ok=True)
            (base / ruta).write_text(contenido, encoding="utf-8")
        for carpeta in vacias:
            (base / carpeta).mkdir(parents=True)
        d = {"titulo": "B", "forma": "cadena", "orienta": "LEEME.md",
             "eslabones": [{"carpeta": "notas", "titulo": "Notas", "versiones": "carpeta", "indice": "notas/VIGENTES.md"}]}
        return lector.leer(forma.validar(d), base)

    def test_un_archivo_suelto_y_una_carpeta_que_se_llaman_igual_son_dos_piezas(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.base_en_carpetas(tmp, "[suelta](altas.md)\n[carpeta](altas/v1.md)\n",
                                      {"notas/altas.md": "# Suelta\n\nTexto.\n", "notas/altas/v1.md": "# V1\n\nTexto.\n"})
            piezas = a["eslabones"][0]["piezas"]
            self.assertEqual([(p["vigente"], p["versiones"]) for p in piezas],
                             [("notas/altas.md", ["notas/altas.md"]), ("notas/altas/v1.md", ["notas/altas/v1.md"])])

    def test_una_carpeta_de_pieza_vacia_se_muestra_sin_definir(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.base_en_carpetas(tmp, "# Vigentes\n", {}, vacias=["notas/por-definir"])
            piezas = a["eslabones"][0]["piezas"]
            self.assertEqual([(p["nombre"], p["versiones"], p["vigente"]) for p in piezas], [("Por definir", [], None)])
            archivos = atlas.componer(a)[0]
            self.assertIn("Sin definir: la carpeta está vacía.", texto(archivos, "index.html"))
            self.assertIn("<code>notas/por-definir/</code>", texto(archivos, "eslabon/notas.html"))

    def test_un_vinculo_dentro_de_un_tramo_de_codigo_no_marca_vigente(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "cadena"
            shutil.copytree(CADENA, base)
            (base / "notas" / "VIGENTES.md").write_text(
                "# Vigentes\n\nEjemplo: `[x](respaldo-v1.md)`\n\n- [vigente](respaldo-v2.md)\n", encoding="utf-8")
            notas = leer("forma-cadena.json", base)["eslabones"][2]
            self.assertEqual(next(p for p in notas["piezas"] if p["nombre"] == "Respaldo")["vigente"],
                             "notas/respaldo-v2.md")

    def test_un_documento_de_mas_vuelve_a_su_nivel(self):
        archivos = sitio("forma-areas.json", CASA, "completa")
        for doc, espera in (("doc/areas-cobranzas-notas-del-equipo.html", ["../index.html", "../area/cobranzas.html"]),
                            ("doc/areas-altas-procedimientos-alta-de-cliente-plantilla-de-ficha.html",
                             ["../index.html", "../area/altas.html", "../proc/altas-alta-de-cliente.html"])):
            camino = re.search(r'<nav class="camino".*?</nav>', texto(archivos, doc)).group(0)
            self.assertEqual(re.findall(r'href="([^"]+)"', camino), espera, doc)


class VinculosDelMapa(unittest.TestCase):
    def datos(self):
        return json.loads((AQUI / "ejemplo" / "datos.json").read_text(encoding="utf-8"))

    def mapa(self, d):
        return next(b for s in d["secciones"] for b in s["bloques"] if b["tipo"] == "mapa")

    def test_un_vinculo_raro_no_vale(self):
        for enlace in ("../afuera.html", "https://ejemplo.invalid/x.html", "javascript:alert(1)", "doc/x.md",
                       "/index.html", "doc/../x.html"):
            d = self.datos()
            self.mapa(d)["columnas"][0]["items"][0]["enlace"] = enlace
            with self.assertRaises(ErrorDatos, msg=enlace):
                validar(d)

    def test_un_vinculo_a_una_pagina_que_no_se_arma_frena(self):
        raices = {"casa": str(AQUI / "ejemplo" / "fuentes")}
        d = self.datos()
        self.mapa(d)["columnas"][0]["items"][0]["enlace"] = "doc/casa-reglas-md.html"
        self.assertIn('href="doc/casa-reglas-md.html"', componer_informe(d, raices)["index.html"].decode("utf-8"))
        d = copy.deepcopy(d)
        self.mapa(d)["columnas"][0]["items"][0]["enlace"] = "doc/no-existe.html"
        with self.assertRaises(ErrorDatos) as c:
            componer_informe(d, raices)
        self.assertIn("lleva a una página que no se arma", str(c.exception))


if __name__ == "__main__":
    unittest.main()
