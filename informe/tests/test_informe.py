"""Pruebas del armador. Solo datos inventados, sin red: el dibujo de los diagramas pasa en el navegador y no se prueba acá."""
import copy
import io
import json
import re
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from informe.__main__ import main
from informe.armado import MANIFIESTO, armar, componer
from informe.citas import CitaInvalida, ubicar
from informe.datos import ErrorDatos, validar
from informe.markdown import a_html, bloques, en_linea

AQUI = Path(__file__).resolve().parent.parent
EJEMPLO = AQUI / "ejemplo"
RAICES = {"casa": str(EJEMPLO / "fuentes")}


def ejemplo():
    return json.loads((EJEMPLO / "datos.json").read_text(encoding="utf-8"))


def hallazgos(d):
    return next(b for s in d["secciones"] for b in s["bloques"] if b["tipo"] == "hallazgos")["items"]


class Markdown(unittest.TestCase):
    def test_bloques_con_sus_lineas(self):
        texto = "# Uno\n\nprimera\nsegunda\n\n- a\n- b\n  sigue\n\n| x | y |\n|---|---|\n| 1 | 2 |\n"
        tipos = [(b["t"], b["a"], b["b"]) for b in bloques(texto)]
        self.assertEqual(tipos, [("h", 1, 1), ("p", 3, 4), ("lista", 6, 8), ("tabla", 10, 12)])

    def test_todo_el_texto_se_escapa(self):
        salida = a_html("<script>alert(1)</script>\n\n- <img src=x onerror=y>\n\n| <b>a</b> |\n|---|\n| <i>b</i> |")
        self.assertNotIn("<script>", salida)
        self.assertNotIn("<img", salida)
        self.assertNotIn("<b>", salida)
        self.assertIn("&lt;script&gt;", salida)

    def test_formato_en_linea(self):
        self.assertEqual(en_linea("**a** y `b<c` y *d*"), "<strong>a</strong> y <code>b&lt;c</code> y <em>d</em>")
        self.assertIn('href="https://ejemplo.invalid/a"', en_linea("[x](https://ejemplo.invalid/a)"))
        self.assertNotIn("href", en_linea("[x](javascript:alert(1))"))
        self.assertNotIn("href", en_linea("[x](otra/pagina.md)"))

    def test_un_enlace_no_rompe_el_atributo(self):
        salida = en_linea('[x](https://ejemplo.invalid/"onmouseover="y)')
        self.assertNotIn('"onmouseover="', salida)

    def test_codigo_dentro_del_destino_de_un_enlace_no_arma_un_atributo(self):
        salida = en_linea('[x](https://ejemplo.invalid/`"onmouseover="alert(1)//`)')
        self.assertNotIn("<a ", salida)
        self.assertNotIn('"onmouseover', salida)

    def test_un_comentario_no_se_lleva_el_texto_de_al_lado(self):
        self.assertEqual(a_html("<!-- interno -->Texto que debe verse"), "<p>Texto que debe verse</p>")
        self.assertEqual(a_html("antes <!-- interno --> después"), "<p>antes  después</p>")
        self.assertEqual(a_html("uno\n<!-- a\nb -->\ndos"), "<p>uno</p>\n<p>dos</p>")

    def test_un_comentario_dentro_de_un_bloque_de_codigo_se_conserva(self):
        self.assertIn("&lt;!-- queda --&gt;", a_html("```\n<!-- queda -->\n```"))

    def test_los_comentarios_no_mueven_las_lineas(self):
        salida = a_html("<!-- a\nb\nc -->\n\nseñalado", [(5, 5, "cita-1")])
        self.assertEqual(salida, '<p id="cita-1" class="resaltado">señalado</p>')

    def test_un_titulo_con_mucho_espacio_no_atasca(self):
        inicio = time.perf_counter()
        self.assertEqual(bloques("# a" + " " * 200000 + "z")[0]["t"], "h")
        en_linea("**" + "a " * 200000)
        self.assertLess(time.perf_counter() - inicio, 2)

    def test_titulos_con_y_sin_cierre(self):
        self.assertEqual(a_html("## Título ##\n\n# C# es un lenguaje"), "<h3>Título</h3>\n<h2>C# es un lenguaje</h2>")

    def test_dos_citas_del_mismo_bloque_tienen_cada_una_su_destino(self):
        salida = a_html("un párrafo\n\n| a |\n|---|\n| b |", [(1, 1, "cita-1"), (1, 1, "cita-2"), (5, 5, "cita-3"),
                                                              (5, 5, "cita-4")])
        for ancla in ("cita-1", "cita-2", "cita-3", "cita-4"):
            self.assertEqual(salida.count('id="%s"' % ancla), 1, ancla)
        self.assertIn('<td><span class="ancla" id="cita-4"></span>b</td>', salida)

    def test_lista_anidada_y_ordenada(self):
        salida = a_html("1. uno\n   - adentro\n2. dos")
        self.assertEqual(salida, "<ol><li>uno<ul><li>adentro</li></ul></li><li>dos</li></ol>")

    def test_resalta_solo_el_bloque_del_pasaje(self):
        salida = a_html("uno\n\ndos\n\n- tres\n- cuatro", [(5, 5, "cita-1")])
        self.assertEqual(salida.count("resaltado"), 1)
        self.assertIn('<li id="cita-1" class="resaltado">tres', salida)

    def test_un_pasaje_de_varios_bloques_lleva_una_sola_ancla(self):
        salida = a_html("uno\n\ndos", [(1, 3, "cita-1")])
        self.assertEqual(salida.count('id="cita-1"'), 1)
        self.assertEqual(salida.count("resaltado"), 2)

    def test_el_diagrama_de_un_documento_queda_para_el_visor(self):
        salida = a_html("```mermaid\nflowchart LR\n  a --> b\n```")
        self.assertIn('<pre class="mermaid">flowchart LR\n  a --&gt; b</pre>', salida)

    def test_un_comentario_no_se_muestra(self):
        self.assertEqual(a_html("<!-- nota\ninterna -->\nvisible"), "<p>visible</p>")

    def test_una_barra_dentro_de_codigo_no_corta_la_celda(self):
        salida = a_html("| a | b |\n|---|---|\n| `x|y` | z |")
        self.assertIn("<td><code>x|y</code></td><td>z</td>", salida)


class Citas(unittest.TestCase):
    FUENTE = "# T\n\nEl respaldo se prueba **una vez**\npor mes.\n\n> una nota que sigue\n> en otra línea\n"

    def test_ubica_aunque_cambien_saltos_y_formato(self):
        self.assertEqual(ubicar(self.FUENTE, "se prueba una vez por mes"), (3, 4, 1))

    def test_ubica_dentro_de_una_cita_partida(self):
        self.assertEqual(ubicar(self.FUENTE, "una nota que sigue en otra línea")[:2], (6, 7))

    def test_lo_que_no_esta_no_se_inventa(self):
        self.assertIsNone(ubicar(self.FUENTE, "se prueba dos veces por mes"))

    def test_una_cita_muy_corta_no_alcanza(self):
        with self.assertRaises(CitaInvalida):
            ubicar(self.FUENTE, "por mes")

    def test_cuenta_las_repeticiones(self):
        self.assertEqual(ubicar("la misma frase larga\n\nla misma frase larga", "la misma frase larga")[2], 2)


class Datos(unittest.TestCase):
    def test_el_ejemplo_cumple_el_contrato(self):
        d = ejemplo()
        self.assertIs(validar(d), d)

    def falla(self, cambio, espera):
        d = ejemplo()
        cambio(d)
        with self.assertRaises(ErrorDatos) as contexto:
            validar(d)
        self.assertIn(espera, str(contexto.exception))

    def test_bloque_desconocido(self):
        self.falla(lambda d: d["secciones"][0]["bloques"].append({"tipo": "grafico"}), "tipo de bloque desconocido")

    def test_clave_de_mas(self):
        self.falla(lambda d: d.update(extra=1), "clave desconocida: extra")

    def test_estado_invalido_en_la_matriz(self):
        def cambio(d):
            d["secciones"][2]["bloques"][0]["filas"][0]["celdas"]["sur"] = ["mal", "x"]
        self.falla(cambio, "estado en ok, exc, dif, na")

    def test_cita_sin_archivo(self):
        self.falla(lambda d: hallazgos(d)[0]["evidencia"][0].pop("archivo"), "van juntos")

    def test_evidencia_sin_cita_ni_fuente(self):
        def cambio(d):
            hallazgos(d)[0]["evidencia"][0] = {"parafrasis": "porque sí"}
        self.falla(cambio, "dice de dónde sale")

    def test_seccion_repetida(self):
        self.falla(lambda d: d["secciones"].append(copy.deepcopy(d["secciones"][0])), "está repetido")

    def test_tema_con_un_color_raro(self):
        self.falla(lambda d: d.update(tema={"acento": "red;}body{display:none"}), "tema.acento")

    def test_un_hallazgo_no_va_dentro_de_un_desplegable(self):
        def cambio(d):
            d["secciones"][1]["bloques"][2]["bloques"].append({"tipo": "hallazgos", "items": []})
        self.falla(cambio, "dentro de un desplegable no va")


class Armado(unittest.TestCase):
    def test_arma_el_indice_y_una_pagina_por_documento(self):
        archivos = componer(ejemplo(), RAICES)
        self.assertEqual(sorted(a for a in archivos if a.endswith(".html")),
                         ["doc/casa-estado-md.html", "doc/casa-reglas-md.html", "index.html"])
        indice = archivos["index.html"].decode("utf-8")
        self.assertIn('href="doc/casa-reglas-md.html#cita-1"', indice)
        self.assertIn("reglas.md, líneas 13 a 14", indice)
        documento = archivos["doc/casa-reglas-md.html"].decode("utf-8")
        self.assertIn('<p id="cita-1" class="resaltado">El respaldo se prueba', documento)
        self.assertIn('<tr id="cita-2" class="resaltado"><td>Carpeta</td>', documento)
        self.assertIn('href="../index.html"', documento)

    def test_una_cita_que_no_existe_frena_todo(self):
        d = ejemplo()
        hallazgos(d)[0]["evidencia"][0]["cita"] = "Una copia que jamás se restauró vale igual."
        with self.assertRaises(ErrorDatos) as contexto:
            componer(d, RAICES)
        self.assertIn("la cita no está en casa:reglas.md", str(contexto.exception))

    def test_no_se_sale_de_la_raiz(self):
        for ruta in ("casa:../fuera.md", "casa:/etc/passwd", "casa:a/../../b.md", "otra:reglas.md", "casa:\\reglas.md",
                     "casa:/reglas.md", "casa:./reglas.md", "casa:C:reglas.md", "casa:sub//reglas.md"):
            d = ejemplo()
            hallazgos(d)[0]["evidencia"][0]["archivo"] = ruta
            with self.assertRaises(ErrorDatos, msg=ruta):
                componer(d, RAICES)

    def test_cada_enlace_a_un_documento_tiene_destino(self):
        d = ejemplo()
        hallazgos(d)[0]["evidencia"].append(dict(hallazgos(d)[0]["evidencia"][0]))
        archivos = componer(d, RAICES)
        indice = archivos["index.html"].decode("utf-8")
        for pagina in sorted(a for a in archivos if a.startswith("doc/")):
            documento = archivos[pagina].decode("utf-8")
            for ancla in sorted(set(re.findall(r'href="%s#(cita-\d+)"' % re.escape(pagina), indice))):
                self.assertEqual(documento.count('id="%s"' % ancla), 1, "%s#%s" % (pagina, ancla))

    def test_una_cita_dentro_de_un_comentario_frena(self):
        d = ejemplo()
        hallazgos(d)[0]["evidencia"][0]["cita"] = "Documento inventado para las pruebas del componente."
        with self.assertRaises(ErrorDatos) as contexto:
            componer(d, RAICES)
        self.assertIn("no se muestra", str(contexto.exception))

    def test_los_datos_no_inyectan_html(self):
        d = ejemplo()
        d["titulo"] = "<script>x</script>"
        d["secciones"][0]["bloques"][0]["columnas"][0]["items"][0] = "<img src=x onerror=y>"
        d["secciones"][1]["bloques"][0]["mermaid"] = "flowchart LR\n  a[</pre><b>x] --> b"
        indice = componer(d, RAICES)["index.html"].decode("utf-8")
        self.assertNotIn("<script>x", indice)
        self.assertNotIn("<img", indice)
        self.assertNotIn("</pre><b>", indice)

    def test_sale_igual_dos_veces(self):
        self.assertEqual(componer(ejemplo(), RAICES), componer(ejemplo(), RAICES))

    def test_los_recursos_llevan_la_huella_en_el_nombre(self):
        archivos = componer(ejemplo(), RAICES)
        estilos = [a for a in archivos if a.startswith("estatico/estilo.")]
        self.assertEqual(len(estilos), 1)
        self.assertRegex(estilos[0], r"^estatico/estilo\.[0-9a-f]{10}\.css$")
        self.assertIn(estilos[0], archivos["index.html"].decode("utf-8"))

    def test_el_tema_cambia_solo_las_variables(self):
        d = ejemplo()
        d["tema"] = {"acento": "#123456", "fuente_titulos": "Georgia"}
        indice = componer(d, RAICES)["index.html"].decode("utf-8")
        self.assertIn("--acento:#123456", indice)
        self.assertIn("--f-titulos:Georgia,system-ui,sans-serif", indice)

    def test_la_libreria_de_diagramas_puede_ir_adentro(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "diagramas.js"
            local.write_text("/* copia local */", encoding="utf-8")
            archivos = componer(ejemplo(), RAICES, str(local))
        propias = [a for a in archivos if a.startswith("estatico/mermaid.")]
        self.assertEqual(len(propias), 1)
        self.assertIn('src="../%s"' % propias[0], archivos["doc/casa-estado-md.html"].decode("utf-8"))
        self.assertIn('src="%s"' % propias[0], archivos["index.html"].decode("utf-8"))
        # una página sin diagramas escritos como texto no pide la librería
        self.assertNotIn("mermaid", archivos["doc/casa-reglas-md.html"].decode("utf-8"))
        with self.assertRaises(ErrorDatos):
            componer(ejemplo(), RAICES, "http://ejemplo.invalid/diagramas.js")

    def test_escribe_y_vuelve_a_escribir_sin_dejar_restos(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "sitio"
            armar(ejemplo(), RAICES, salida)
            self.assertTrue((salida / "index.html").is_file())
            self.assertTrue((salida / MANIFIESTO).is_file())
            d = ejemplo()
            hallazgos(d).pop(0)
            for evidencia in hallazgos(d)[0]["evidencia"]:
                if evidencia.get("archivo") == "casa:reglas.md":
                    evidencia["archivo"], evidencia["cita"] = "casa:estado.md", "«Ejemplo Norte» ya tiene su ficha."
            armar(d, RAICES, salida)
            self.assertFalse((salida / "doc" / "casa-reglas-md.html").exists())
            self.assertTrue((salida / "doc" / "casa-estado-md.html").is_file())

    def test_no_pisa_una_carpeta_ajena(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "algo.txt").write_text("de otro", encoding="utf-8")
            with self.assertRaises(ErrorDatos):
                armar(ejemplo(), RAICES, tmp)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["algo.txt"])

    def test_un_registro_danado_no_borra_nada(self):
        for registro in ('{"archivos": ["index.html", null]}', '{"archivos": ["../afuera.txt"]}',
                         '{"archivos": "index.html"}', "no es json", '{"otra": 1}',
                         '{"archivos": ["C:/afuera.txt"]}', '{"archivos": ["doc\\\\..\\\\..\\\\afuera.txt"]}'):
            with tempfile.TemporaryDirectory() as tmp:
                salida = Path(tmp) / "sitio"
                armar(ejemplo(), RAICES, salida)
                (Path(tmp) / "afuera.txt").write_text("ajeno", encoding="utf-8")
                (salida / MANIFIESTO).write_text(registro, encoding="utf-8")
                with self.assertRaises(ErrorDatos):
                    armar(ejemplo(), RAICES, salida)
                self.assertTrue((salida / "index.html").is_file(), registro)
                self.assertTrue((Path(tmp) / "afuera.txt").is_file(), registro)

    def test_no_borra_ni_escribe_a_traves_de_un_enlace(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida, externa = Path(tmp) / "sitio", Path(tmp) / "externa"
            armar(ejemplo(), RAICES, salida)
            externa.mkdir()
            (externa / "casa-reglas-md.html").write_text("ajeno", encoding="utf-8")
            for hijo in (salida / "doc").iterdir():
                hijo.unlink()
            (salida / "doc").rmdir()
            try:
                (salida / "doc").symlink_to(externa, target_is_directory=True)
            except OSError:
                self.skipTest("este sistema no deja crear enlaces sin permisos")
            with self.assertRaises(ErrorDatos):
                armar(ejemplo(), RAICES, salida)
            self.assertEqual((externa / "casa-reglas-md.html").read_text(encoding="utf-8"), "ajeno")
            self.assertEqual(sorted(p.name for p in externa.iterdir()), ["casa-reglas-md.html"])

    def test_si_falla_no_escribe_nada(self):
        d = ejemplo()
        hallazgos(d)[1]["evidencia"][0]["cita"] = "esto no figura en ningún documento"
        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "sitio"
            with self.assertRaises(ErrorDatos):
                armar(d, RAICES, salida)
            self.assertFalse(salida.exists())


class Comando(unittest.TestCase):
    def correr(self, *argumentos):
        salida, errores = io.StringIO(), io.StringIO()
        with redirect_stdout(salida), redirect_stderr(errores):
            codigo = main(list(argumentos))
        return codigo, salida.getvalue(), errores.getvalue()

    def test_validar_y_armar(self):
        raiz = "casa=%s" % RAICES["casa"]
        codigo, salida, _ = self.correr("validar", str(EJEMPLO / "datos.json"), "--raiz", raiz)
        self.assertEqual((codigo, salida.startswith("VÁLIDO")), (0, True))
        with tempfile.TemporaryDirectory() as tmp:
            codigo, salida, _ = self.correr("armar", str(EJEMPLO / "datos.json"), "--raiz", raiz, "--salida",
                                            str(Path(tmp) / "s"))
            self.assertEqual(codigo, 0)
            self.assertIn("ARMADO", salida)

    def test_sin_la_raiz_dice_por_que_no_se_armo(self):
        codigo, _, errores = self.correr("validar", str(EJEMPLO / "datos.json"))
        self.assertEqual(codigo, 1)
        self.assertIn('NO SE ARMÓ', errores)
        self.assertIn('la raíz "casa" no fue declarada', errores)


if __name__ == "__main__":
    unittest.main()
