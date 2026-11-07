# Desarrollo compartido — de la idea a versión aceptada

Un solo job: de una idea o necesidad a una versión confirmada por quien la corre. Vale para el motor, los plugins y los
módulos de Odoo, y en los dos sentidos. Se repite ante cada cambio a algo que use más de una instalación.

Cada lado sigue este archivo sin cambiarlo. Las rutas propias de cada lado las da el `CONTEXT.md` de la raíz de su workspace.
Este archivo no las nombra.

- **Lado que corta:** su sesión escribe `motor-chat` y `comun`, y arma las entregas.
- **Lado que recibe:** toma las entregas y las guarda en sus repos, con su usuario.
- **El dueño:** dirige los dos lados y da los OK de este archivo.

## Dónde va cada cosa

| Qué | Dónde | Quién escribe |
|---|---|---|
| Motor | repo `motor-chat` | lado que corta |
| Lo que usan las dos empresas | repo `comun`, una carpeta por componente, con plugin y Odoo juntos | lado que corta |
| Lo que usa una sola empresa | su casa, con nombre por función | esa empresa |
| Lo de un cliente | la casa de ese cliente | la empresa que lo atiende |
| Pedidos, entregas y versiones | `~/motores-compartidos/` (reglas en su `LEEME.md`) | cada lado, lo suyo |

## Qué cruza

- Un archivo y nada más: la entrega sin historia. Ni submódulos, ni forks, ni espejos, ni una dirección que apunte a un repo.
- De lo que nace en el lado que recibe cruza una especificación, nunca código. El lado que corta lo programa de cero.
- Nada de lo que cruza nombra instalaciones, clientes, partners ni prospectos, ni trae datos reales o secretos.

## 1. Pedido — la idea queda escrita

Un solo job: de una idea a un pedido que el otro lado pueda hacer sin preguntar.

- **Inputs.** De esta corrida: la idea. De todas: `~/motores-compartidos/LEEME.md` y la sección «Qué cruza».
- **NO cargar:** repos, carpetas ni memoria del otro lado.
- **Process.**
  1. Con la tabla de arriba, ubicá dónde va. Si es de una sola empresa o de un cliente, termina acá: va a su casa.
  2. Escribí el pedido, `AAMMDD-tema.md`: qué hace falta, para qué, cómo se prueba y a quién avisar. Va a
     `~/motores-compartidos/pedidos/`. Si el otro lado todavía no puede recibirlo, queda en espera del lado que lo escribió,
     con «Cuándo», y junta ahí sus respuestas hasta que se deja en la carpeta.
  3. Si nace en el lado que recibe, el pedido es la especificación: qué hace, reglas, modelos y campos, casos inventados y
     lo que ya falló. Decí quién la escribió. Sin código, IDs ni ejemplos reales.
  4. Al dejarlo en la carpeta, avisá a una sesión del otro lado por el canal del `LEEME.md`.
- **Outputs.** El pedido, en esa carpeta o en espera.
- **Chequeo.** Automático: el lado que corta barre la carpeta y frena ante cualquier nombre.
- **Punto de commit.** Ninguno: la carpeta no es un repo.

## 2. Entrada — si entra en `comun`

Un solo job: del pedido a una decisión escrita sobre dónde se programa.

- **Inputs.** De esta corrida: el pedido. De todas: la tabla de arriba.
- **NO cargar:** código del lado que recibe.
- **Process.**
  1. Un arreglo a algo que ya está en `motor-chat` o `comun` sigue derecho a la etapa 3, con el freno de origen que ya tenga
     (paso 4).
  2. Algo nuevo entra en `comun` solo si cumple las tres: lo usan las dos empresas, anda sin nombrar a nadie y se prueba con
     datos inventados. Si falta una, queda en su casa.
  3. Un módulo propio de Odoo pide además mirar si alcanza con uno público y con la licencia a la vista.
  4. **Freno de origen.** Un componente que nació por una necesidad del lado que recibe se mantiene y se le entrega a ese
     lado, y frena ahí: no se ofrece a nadie más hasta que el dueño lo libere por escrito en el pedido.
- **Outputs.** Respuesta en el pedido: dónde va y por qué.
- **Chequeo.** Humano: el dueño da el OK de cada componente nuevo en `comun` y de cada liberación del freno de origen.
  Lo demás avanza solo.
- **Punto de commit.** Ninguno.

## 3. Desarrollo — el cambio, probado

Un solo job: del pedido a una rama con el cambio y sus pruebas. Lo hace la sesión del lado que corta.

- **Inputs.** De esta corrida: el pedido. De todas: `AGENTS.md` del repo y, si los tiene, `AGENTS.md` y `CONTRATO.md`
  del componente.
- **NO cargar:** código ni datos del lado que recibe.
- **Process.**
  1. Rama corta en el repo que toca, con la identidad de ese repo y un nombre distinto del tag que va a salir: si se llaman
     igual, publicar el tag es ambiguo.
  2. Si cambia lo que otro componente espera, decilo en el pedido antes de cortar.
  3. Pruebas solo con datos inventados. Un error que aparece únicamente con datos reales lo destila el lado que los tiene: un
     caso inventado en el pedido.
  4. Un módulo de Odoo que cambia datos lleva migración, y su vuelta atrás se prueba en una base aparte.
- **Outputs.** La rama, con pruebas en verde.
- **Chequeo.** Automático: `scripts/test.sh` del repo y el del componente, si lo tiene.
- **Punto de commit.** Con las pruebas en verde.

## 4. Validación — otra mirada

Un solo job: de la rama a una revisión independiente, con lo que tenga un caso concreto ya corregido.

- **Inputs.** De esta corrida: el pedido y la rama. De todas: las reglas del repo.
- **NO cargar:** las conclusiones de quien programó.
- **Process.**
  1. Mirá la memoria libre: una revisión anterior se cortó por eso.
  2. Escribí el pedido de revisión en un archivo, fuera del repo.
  3. Corré un Codex nuevo: `codex exec -s read-only --ephemeral -C <repo> -o <salida> "$(cat <pedido>)" < /dev/null`.
  4. Corregí lo que traiga un caso concreto. Lo demás queda anotado en el pedido.
  5. Una prueba de calidad la diseña y la juzga una sesión nueva, no quien programó.
- **Outputs.** Revisión guardada y correcciones en la rama.
- **Chequeo.** Automático: `scripts/test.sh`, de nuevo, después de corregir.
- **Punto de commit.** Con cada corrección.

## 5. Compuerta y tag — la versión queda fija

Un solo job: de la rama revisada a un tag publicado.

- **Inputs.** De esta corrida: la rama. De todas: `scripts/IDENTIFICADORES.md` del repo y, si el componente tiene pruebas
  propias, la sección de pruebas de su README: el etiquetado las corre y piden el entorno que dice ahí.
- **NO cargar:** la lista en claro: nunca entra en un repo que se entrega.
- **Process.**
  1. Uní la rama a `main` y guardá el cambio de `VERSION` en un commit propio: el etiquetado pide el árbol limpio.
  2. Etiquetá. `motor-chat`: `bash scripts/etiquetar.sh vX.Y.Z`, con su recibo. `comun`: `bash scripts/etiquetar.sh
     <componente> <versión>`.
  3. Publicá `main` y el tag: `git push origin main <tag>`. Si el `AGENTS.md` del componente pide autorización para
     publicar, antes va la del dueño, escrita en el pedido. Nunca a la fuerza ni saltando controles.
- **Outputs.** `main` y el tag, publicados.
- **Chequeo.** Automático: la compuerta y las pruebas corren en el etiquetado. Con un hallazgo no hay tag.
- **Punto de commit.** El tag.

## 6. Entrega — el archivo que cruza

Un solo job: del tag a una entrega en la carpeta compartida.

- **Inputs.** De esta corrida: el tag. De todas: la herramienta de entrega, en el `CONTEXT.md` de la raíz del lado que corta.
- **NO cargar:** nada del lado que recibe.
- **Process.**
  1. Armá la entrega con la herramienta. El motor pide además qué commits ya desplegados trae.
  2. Barré `~/motores-compartidos` con la misma herramienta.
  3. Anotá la versión en «Última cortada» de `versiones.md`.
  4. Anotá en el pedido, sin nombres, qué cambió, qué revisar y la evidencia que pida el contrato del componente, como
     contra qué motor se probó y con qué resultado: el `.json` de la entrega trae solo la huella.
  5. Avisá al lado que recibe.
- **Outputs.** `~/motores-compartidos/entregas/<componente>-<versión>/`, su `.tar` y su `.json`.
- **Chequeo.** Automático: la entrega y el barrido frenan ante un nombre o una copia de un repo. Una entrega no se edita: el
  arreglo sale en la versión siguiente.
- **Punto de commit.** Ninguno.

## 7. Aceptación — la confirma quien la corre

Un solo job: de la entrega a un sí o un no del lado que la corre. Una versión vale recién acá.

- **Inputs.** De esta corrida: la entrega, su `.json` y el pedido. De todas: `~/motores-compartidos/LEEME.md`.
- **NO cargar:** repos del otro lado.
- **Process.**
  1. Compará la huella del `.tar` con su `.json`.
  2. Guardá la versión en tus repos, con tu usuario. Ninguna dirección del lado que corta.
  3. Probá con tus plugins y tus datos, de tu lado. Solo vos podés hacer esa prueba.
  4. Desplegá con tu respaldo y tu vuelta atrás. Si algo falla, volvés atrás y avisás en el pedido.
  5. Respondé en el pedido: aceptada o rechazada, con el caso concreto. Un rechazo vuelve a la etapa 1.
- **Outputs.** La respuesta en el pedido.
- **Chequeo.** Humano o automático, lo que fijen las aprobaciones de ese lado para producción.
- **Punto de commit.** La versión guardada en tus repos.

## 8. Registro — qué corre cada lado

Un solo job: de la respuesta a la tabla común al día.

- **Inputs.** De esta corrida: la respuesta del pedido. De todas: `~/motores-compartidos/versiones.md`.
- **NO cargar:** nada más.
- **Process.**
  1. Cada lado escribe su columna: qué versión corre y desde cuándo.
  2. A las dos semanas de atraso, quien se atrasa escribe motivo y fecha en la tabla. Nunca se despliega algo incompatible por
     calendario.
- **Outputs.** La tabla al día y el pedido cerrado.
- **Chequeo.** Automático: el barrido también la lee.
- **Punto de commit.** Ninguno.
