# comun · orientación

Componentes que usan dos instalaciones. Una carpeta por componente, entera: plugin, módulos de Odoo, pruebas y documentos.
El motor no vive acá.

- **Cómo se trabaja:** `proceso/desarrollo.md`. Es el único camino, de la idea a la versión aceptada.
- **Quién escribe:** solo la sesión del lado que corta, con la identidad de este repo. Sin submódulos, forks ni espejos.
- **Qué entra:** lo que usan las dos empresas, anda sin nombrar a nadie y se prueba con datos inventados.
- **Compuerta:** `bash scripts/test.sh` revisa todo el repo por hashes (`scripts/IDENTIFICADORES.md`). Ningún nombre de
  instalación, cliente, partner o prospecto, ni dato real. La lista en claro no entra acá.
- **Datos de prueba:** solo inventados. Un CUIT de prueba lleva a propósito un dígito verificador inválido: una prueba lo exige.
- **Versiones:** tag `<componente>-<versión>`, con `scripts/etiquetar.sh`. Cada componente lleva su `VERSION`.
- **Git:** `add` con rutas explícitas. Nunca `--force` ni `--no-verify`. Los bytes no se tocan: `.gitattributes` lo fija.
- **Cada carpeta manda:** antes de tocar un componente, leé su `AGENTS.md`, si lo tiene.
- Este repo no recibe copias de reglas de otras casas: lo que entra acá viaja.
