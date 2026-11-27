"""Element X sobre la superficie Android: entrar a una sala, mandar, esperar al bot y abrir sus links.

Lo usa `tomar.py` (acciones `elementx.*` del guion: CONTRATO.md del componente). Para el escenario, una vez por cuenta:
  <comando que imprime la clave> | python elementx.py iniciar-sesion <usuario> <servidor>
La clave entra por la entrada estándar y no se escribe en ningún lado.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import android  # noqa: E402

PAQUETE = "io.element.android.x"
ENVIAR = ("Enviar", "Enviar mensaje", "Send", "Send message")
ESCRIBIENDO = ("escribiendo", "está escribiendo", "typing")
URL = re.compile(r"https?://\S+")
HORA = re.compile(r"\d{1,2}:\d{2}\W*(a|p)?\W*(\.?\s*m\.?)?", re.I)   # la hora de cada globo («2:58 a. m.»)
TOPE_BARRA = 231                                         # debajo de la barra de arriba empieza la línea de tiempo


class ElementX:
    def __init__(self, a: android.Android):
        self.a = a

    # --- lectura de la pantalla -----------------------------------------------------------------------------------

    def _mensajes(self, nodos: list[android.Nodo]) -> list[str]:
        """Los textos de la línea de tiempo (sin el compositor ni la barra de arriba), de arriba abajo."""
        compositor = self._compositor(nodos)
        tope = compositor.y1 if compositor else 2400
        textos = []
        for n in sorted(nodos, key=lambda n: (n.y1, n.x1)):
            if n.clase.endswith("EditText") or not n.texto.strip():
                continue
            if n.y2 > TOPE_BARRA + 10 and n.y1 < tope:   # un mensaje más alto que la pantalla empieza arriba de ella
                textos.append(n.texto.strip())
        return textos

    def _compositor(self, nodos: list[android.Nodo]) -> android.Nodo | None:
        campos = [n for n in nodos if n.clase.endswith("EditText") and n.y1 > 1200]
        return campos[-1] if campos else None

    def _escribiendo(self, nodos: list[android.Nodo]) -> bool:
        """El aviso «<bot> está escribiendo…»: un texto corto (un mensaje largo puede decir «escribiendo»)."""
        return any(len(t) <= 60 and any(e in t.lower() for e in ESCRIBIENDO)
                   for n in nodos for t in (n.texto, n.desc))

    # --- acciones del guion (argumento, marcas) -------------------------------------------------------------------

    def abrir_sala(self, nombre, m) -> None:
        t0 = m.ahora()
        self.a.abrir_app(PAQUETE)
        time.sleep(2)
        nodos = self.a.pantalla()
        no_mostrar = [n for n in nodos if n.texto.strip() == "No mostrar de nuevo"]
        if no_mostrar:  # el aviso de notificaciones (no hay distribuidor), tras un arranque en frío
            self.a.tocar_nodo(no_mostrar[0])
            self.a.tocar_nodo(self.a.esperar(texto="OK"))
            time.sleep(1)
            nodos = self.a.pantalla()
        for _ in range(4):  # ya hay una sala abierta (o varias apiladas, tras compartir): si no es esta, a la lista
            if not self._compositor(nodos):
                break
            if any(n.texto.strip() == str(nombre) and n.y2 <= TOPE_BARRA for n in nodos):
                m.tramo("accion", t0)
                return
            self.a.atras()
            time.sleep(1.5)
            nodos = self.a.pantalla()
        self.a.tocar_nodo(self.a.esperar(texto=str(nombre), espera=30))
        time.sleep(2)
        m.tramo("accion", t0)

    def mandar(self, texto, m) -> None:
        nodos = self.a.pantalla()
        compositor = self._compositor(nodos)
        if compositor is None:
            raise RuntimeError("no encuentro dónde escribir: ¿está abierta una sala?")
        self.a.tocar_nodo(compositor)
        time.sleep(0.4)
        t0 = m.ahora()
        self.a.escribir(str(texto))
        m.tramo("escribir", t0, letras=len(str(texto)))
        nodos = self.a.pantalla()  # fuera de los tramos: la edición lo deja como un hueco corto
        self._antes = self._mensajes(nodos)  # antes de mandar: una respuesta al instante (un comando) también cuenta
        botones = [n for n in nodos if n.desc.strip() in ENVIAR or "send" in n.rid.lower()]
        t1 = m.ahora()
        if botones:
            self.a.tocar_nodo(botones[-1])
        else:
            self.a.tecla("KEYCODE_ENTER")
        m.tramo("accion", t1)
        self._enviado = str(texto).strip()
        self._mandado = m.ahora()

    def esperar_bot(self, arg, m) -> None:
        """Espera la respuesta: un texto nuevo en la línea de tiempo (no el mandado ni una hora) y después `quieto`
        segundos sin cambios."""
        arg = arg or {}
        maximo, quieto = float(arg.get("max", 180)), float(arg.get("quieto", 6))
        t_mandado = getattr(self, "_mandado", m.ahora())
        antes = set(getattr(self, "_antes", None) or self._mensajes(self.a.pantalla()))
        enviado = getattr(self, "_enviado", "")
        fin = time.monotonic() + maximo
        t_asoma = None
        ultimo, t_ultimo_cambio = antes, m.ahora()
        while time.monotonic() < fin:
            nodos = self.a.pantalla()
            actual = self._mensajes(nodos)
            if actual != ultimo:
                ultimo, t_ultimo_cambio = actual, m.ahora()
                nuevos = [t for t in actual  # ni el nombre, ni «Hoy», ni «(editado)»: un mensaje de verdad
                          if t not in antes and t != enviado and not HORA.fullmatch(t) and len(t) >= 12]
                if t_asoma is None and nuevos:
                    t_asoma = t_ultimo_cambio
            if t_asoma is not None and not self._escribiendo(nodos) and m.ahora() - t_ultimo_cambio >= quieto:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"el bot no contestó en {maximo:.0f} s")
        m.tramo("espera", t_mandado, t_asoma)
        m.tramo("respuesta", t_asoma, t_ultimo_cambio)
        m.tramo("lectura", t_ultimo_cambio)
        self._antes, self._enviado = None, ""

    def tocar_link(self, arg, m) -> None:
        """Abre el último link visible (o el que contenga `contiene`) en el navegador del celular."""
        arg = arg or {}
        t0 = m.ahora()
        contiene = str(arg.get("contiene", ""))
        nodos = self.a.pantalla()
        candidatos = [(n, u) for n in sorted(nodos, key=lambda n: n.y1) for u in URL.findall(n.texto)
                      if contiene in u]
        if not candidatos:
            raise RuntimeError(f"no hay un link visible que contenga «{contiene}»")
        nodo, url = candidatos[-1]
        self.a.tocar_nodo(nodo)
        time.sleep(1.5)
        if PAQUETE in android.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", check=False):
            self.a.abrir_url_reservada(url.rstrip(").,»"))  # el toque cayó fuera del link: se abre igual
        time.sleep(4)
        m.tramo("accion", t0)

    def volver(self, arg, m) -> None:
        t0 = m.ahora()
        self.a.abrir_app(PAQUETE)
        time.sleep(1.5)
        m.tramo("accion", t0)

    def desplazar(self, sentido: str, m=None) -> None:
        if sentido == "arriba":
            self.a.deslizar(540, 900, 540, 1700)
        else:
            self.a.deslizar(540, 1700, 540, 900)


ENLACE = "https://mobile.element.io/element/?account_provider={servidor}"
CONTINUAR = ("Continuar", "Continue")


def _tocar_boton(a: android.Android, textos, espera: float = 30) -> None:
    """Toca el botón (no un título) con alguno de esos textos."""
    fin = time.monotonic() + espera
    while time.monotonic() < fin:
        for n in a.buscar():
            if n.texto.strip() in textos and n.clase.endswith(("Button", "View")) and n.y2 > 1000:
                a.tocar_nodo(n)
                return
        time.sleep(0.8)
    raise TimeoutError(f"no apareció el botón {textos}")


def _campo_web(a: android.Android, etiqueta: str, espera: float = 40) -> tuple[int, int]:
    """El campo de una página web (un WebView no lo muestra en el árbol mientras está vacío): queda debajo de su rótulo."""
    rotulo = a.esperar(texto=etiqueta, espera=espera)
    return rotulo.centro[0], rotulo.y2 + 70


def _cerrar_avisos_de_chrome(a: android.Android) -> None:
    """Chrome, la primera vez: bienvenida, cuenta de Google, notificaciones, ofrecer guardar la clave."""
    for _ in range(3):
        for n in a.buscar():
            if n.texto.strip() in ("Usar sin una cuenta", "Use without an account", "Ahora no", "Not now",
                                   "No, gracias", "No thanks", "Nunca", "Never", "Aceptar y continuar", "Accept & continue"):
                a.tocar_nodo(n)
                time.sleep(1.5)
                break
        else:
            return


def iniciar_sesion(usuario: str, servidor: str, clave: str, restablecer: bool = False) -> None:
    """Escenario: primer inicio de sesión de una cuenta en Element X con MAS (desde 261114). El enlace de mobile.element.io
    deja la app apuntada a `servidor`; ella abre la página de ingreso del servidor en el navegador de la app, ahí van
    usuario y clave, después un permiso con «Continuar», y vuelve sola. La clave no se imprime. Requiere Element X sin
    sesión. Una cuenta que ya tenía identidad digital (de antes de MAS) pide «Confirma tu identidad digital»: con
    `restablecer=True` se la restablece (se pierde su historial cifrado y el bot lo nota y avisa); sin él, se frena y se dice."""
    a = android.Android()
    # instalada desde un APK, la app no es la dueña del enlace y Chrome lo abriría él: se le da la preferencia (261115)
    android.shell(f"pm set-app-links-user-selection --user 0 --package {PAQUETE} true mobile.element.io", check=False)
    android.shell(f"am start -a android.intent.action.VIEW -d '{ENLACE.format(servidor=servidor)}'")
    # «Iniciar sesión en <servidor>»: abajo de la pantalla de bienvenida (el botón cambia mientras la app resuelve)
    boton = None
    fin = time.monotonic() + 45
    while boton is None and time.monotonic() < fin:
        time.sleep(1.5)
        boton = next((n for n in a.buscar() if servidor in n.texto and n.y1 > 1800), None)
    if boton is None:
        raise RuntimeError("no veo «Iniciar sesión en %s»: ¿la app ya tiene sesión? (borrala o usá otro celular)"
                           % servidor)
    a.tocar_nodo(boton)
    time.sleep(4)
    _cerrar_avisos_de_chrome(a)
    # el navegador del celular recuerda la sesión de MAS de un ingreso anterior: entonces no pide usuario y clave, sino
    # «¿Continuar a Element X?» con esa cuenta. Si es la misma, se sigue; si no, «Usar otra cuenta».
    fin = time.monotonic() + 40
    while time.monotonic() < fin:
        nodos = a.buscar()
        if a.buscar(texto="Usuario", nodos=nodos):
            break
        if a.buscar(contiene="Continuar a Element X", nodos=nodos):
            if any(usuario in n.texto and n.texto.strip().startswith("@") for n in nodos):
                _tocar_boton(a, CONTINUAR, espera=5)
                _esperar_regreso(a)
                _bienvenida(a, restablecer)
                print("sesión lista: Element X en la lista de chats")
                return
            _tocar_boton(a, ("Usar otra cuenta",), espera=5)
        time.sleep(1.5)
    x, y = _campo_web(a, "Usuario", espera=10)
    a.tocar(x, y)
    time.sleep(1)
    a.escribir(usuario)
    x, y = _campo_web(a, "Contraseña", espera=10)
    a.tocar(x, y)
    time.sleep(1)
    a.escribir_reservado(clave)
    time.sleep(0.5)
    android.shell("input keyevent KEYCODE_BACK")           # baja el teclado: tapaba el botón
    time.sleep(1)
    _tocar_boton(a, CONTINUAR, espera=10)
    # el permiso («Acceso a tu cuenta»): otro «Continuar»; si MAS frenó el ingreso (429), la página lo dice
    fin = time.monotonic() + 60
    while time.monotonic() < fin:
        time.sleep(2)
        nodos = a.buscar()
        if any(n.texto.strip() in ("Usuario", "Contraseña") for n in nodos) and any(
                "demasiad" in n.texto.lower() or "too many" in n.texto.lower() for n in nodos):
            raise RuntimeError("MAS frenó el ingreso (3 seguidos por IP, después uno cada 20 s): esperá y probá de nuevo")
        if any(n.rid.startswith(PAQUETE) for n in nodos):
            break
        if any(n.texto.strip() in CONTINUAR and n.y2 > 1000 for n in nodos):
            _tocar_boton(a, CONTINUAR, espera=5)
            break
    _cerrar_avisos_de_chrome(a)
    _esperar_regreso(a)
    _bienvenida(a, restablecer)
    print("sesión lista: Element X en la lista de chats")


def _esperar_regreso(a: android.Android) -> None:
    """Vuelve sola a Element X; si el navegador queda en «Acceso permitido», se trae la app al frente."""
    fin = time.monotonic() + 60
    while time.monotonic() < fin:
        nodos = a.buscar()
        if any(n.rid.startswith(PAQUETE) for n in nodos):
            break
        if PAQUETE not in android.shell("dumpsys window | grep -E 'mCurrentFocus'", check=False):
            a.abrir_app(PAQUETE)
        time.sleep(2)
    else:
        raise TimeoutError("Element X no volvió a primer plano después del ingreso")


def _bienvenida(a: android.Android, restablecer: bool) -> None:
    """Las pantallas que Element X muestra después del primer ingreso, hasta la lista de chats."""
    fin = time.monotonic() + 120
    while time.monotonic() < fin:
        nodos = a.buscar()
        if a.buscar(rid="home_screen-settings", nodos=nodos) and not a.buscar(contiene="Copia de seguridad de tus chats", nodos=nodos):
            return
        if a.buscar(texto="Confirma tu identidad digital", nodos=nodos) or a.buscar(
                texto="Continuar con el restablecimiento", nodos=nodos):
            if not restablecer:
                raise RuntimeError("Element X pide confirmar la identidad (la cuenta ya tenía una): iniciar_sesion con "
                                   "restablecer=True, o a mano con otro dispositivo")
            _restablecer_identidad(a)
        elif a.buscar(texto="No mostrar de nuevo", nodos=nodos):   # el aviso de notificaciones (no hay distribuidor)
            a.tocar_nodo(a.buscar(texto="No mostrar de nuevo", nodos=nodos)[0])
            a.tocar_nodo(a.esperar(texto="OK", espera=5))
        elif a.buscar(texto="Ahora no", nodos=nodos):               # notificaciones, datos de uso
            a.tocar_nodo(a.buscar(texto="Ahora no", nodos=nodos)[0])
        elif a.buscar(contiene="Copia de seguridad de tus chats", nodos=nodos):   # el cartel de clave de recuperación
            cerrar = a.buscar(desc="Cerrar", nodos=nodos) or a.buscar(texto="Cerrar", nodos=nodos)
            if cerrar:
                a.tocar_nodo(cerrar[0])
        time.sleep(2)
    raise TimeoutError("Element X no llegó a la lista de chats")


def _restablecer_identidad(a: android.Android) -> None:
    """«¿No puedes confirmar?» → restablecer → la página de MAS aprueba con «Terminar el reinicio» (sin pedir clave) → Atrás
    vuelve a la app, que termina sola. (Con «abrir la app» en vez de Atrás, el proceso queda a medias.)"""
    if a.buscar(texto="¿No puedes confirmar?"):    # si ya está en la pantalla siguiente, no hace falta
        a.tocar_nodo(a.buscar(texto="¿No puedes confirmar?")[0])
    a.tocar_nodo(a.esperar(texto="Continuar con el restablecimiento", espera=10))
    a.tocar_nodo(a.esperar(texto="Sí, restablecer ahora", espera=10))
    # la aprobación de MAS dura 10 minutos: si ya está dada, el reinicio termina sin pasar por la página
    fin = time.monotonic() + 45
    while time.monotonic() < fin:
        nodos = a.buscar()
        if any(n.texto.strip() == "Terminar el reinicio" for n in nodos):
            _tocar_boton(a, ("Terminar el reinicio",), espera=5)
            a.esperar(contiene="se reinició correctamente", espera=30)
            time.sleep(1)
            android.shell("input keyevent KEYCODE_BACK")
            time.sleep(6)
            return
        if a.buscar(rid="home_screen-settings", nodos=nodos) or any(
                t in n.texto for n in nodos for t in ("Activa las notificaciones", "Ayuda a mejorar", "No mostrar de nuevo")):
            return
        time.sleep(1.5)
    raise TimeoutError("no terminó el restablecimiento de la identidad")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "iniciar-sesion":
        clave = sys.stdin.readline().rstrip("\r\n")
        if not clave:
            sys.exit("la clave tiene que llegar por la entrada estándar")
        iniciar_sesion(sys.argv[2], sys.argv[3], clave, restablecer=False)
    else:
        print(__doc__)
        sys.exit(2)
