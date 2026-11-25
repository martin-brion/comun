"""De la foto y las fuentes a la lista de piezas: qué corre, qué versión hay y qué tan grande es el paso.

Esta versión observa: la compuerta de versión dice si una candidata «pasaría», pero nada se aplica. Lo que no se pudo
saber queda «desconocido»; nunca «al día».
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from . import config as _config
from . import versiones as V

BASE_NOMBRE = "org.opencontainers.image.base.name"
BASE_HUELLA = "org.opencontainers.image.base.digest"
VERSION = "org.opencontainers.image.version"
SISTEMA = "sistema"                 # el nombre de la pieza del sistema del servidor, y de su componente en la configuración
_HUELLA = re.compile(r"^sha256:[0-9a-f]{64}$")


def _fecha(texto):
    try:
        return datetime.fromisoformat(str(texto).rstrip("Z")[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def identificador(servidor, pieza, variante, propuesta):
    return hashlib.sha256(f"{servidor}|{pieza}|{variante}|{propuesta}".encode()).hexdigest()[:12]


def es_propia(cfg, ref):
    nombres = (ref.pieza, ref.repo, ref.repo[len("library/"):] if ref.repo.startswith("library/") else ref.repo)
    return any(n.startswith(p) for n in nombres for p in cfg["propias"])


def _fuente(cfg, ref, consultar):
    """Las etiquetas publicadas de la pieza, solo por una fuente aprobada en la configuración."""
    if ref.registro == "docker.io":
        if not cfg["fuentes"]["docker_hub"]:
            return {"estado": "desconocido", "motivo": "fuente apagada en la configuración", "etiquetas": [], "hay_mas": False}
        return consultar("docker_hub", ref.registro, ref.repo)
    if ref.registro in cfg["fuentes"]["registros"]:
        return consultar("registro_oci", ref.registro, ref.repo)
    return {"estado": "desconocido", "motivo": "registro sin fuente aprobada", "etiquetas": [], "hay_mas": False}


def _resolver(tag, locales, etiquetas):
    """La versión que corre de verdad. Con una etiqueta flotante («18», «1.30-alpine») y la huella local, busca la etiqueta
    más específica que apunta a esa misma imagen."""
    nombres = [e["nombre"] for e in etiquetas]
    propia = V.etiqueta(tag)
    if propia is not None and not V.flotante(tag, nombres):
        return propia, False
    mejores = []
    for e in etiquetas:
        o = V.etiqueta(e["nombre"])
        if o is None or o.previa or not (set(e["digests"]) & locales) or V.flotante(e["nombre"], nombres):
            continue
        if propia is None or (o.prefijo == propia.prefijo and o.variante == propia.variante
                              and o.numeros[:len(propia.numeros)] == propia.numeros):
            mejores.append(o)
    if mejores:
        variante = propia.variante if propia is not None else ""
        return max(mejores, key=lambda o: (o.variante == variante, len(o.numeros), o.clave)), True
    return None, True


def _pieza(cfg, servidor, ref, texto, imagen, imagen_id, contenedores, consultar, detalle, ahora):
    comp = _config.componente(cfg, ref.pieza)
    servicios = sorted({f"{c['proyecto']}/{c['servicio']}" for c in contenedores if c["proyecto"] and c["servicio"]})
    contenedores = [c["nombre"] for c in contenedores]
    # La referencia exacta para volver atrás: la que tiene el servicio hoy, con su huella.
    anterior = texto if ref.digest else next((f"{texto}@{d.rpartition('@')[2]}" for d in imagen.get("digests", [])), None)
    locales = {d.rpartition("@")[2] for d in imagen.get("digests", [])} | ({ref.digest} if ref.digest else set())
    r = {"servidor": servidor, "pieza": ref.pieza, "contenedores": sorted(contenedores), "etiqueta": ref.tag,
         "fijada": bool(ref.digest), "actual": None, "estado": "desconocido", "motivo": "", "candidata": None,
         "otras": [], "notas": [], "nivel": comp["nivel"], "expuesto": comp["expuesto"], "propia": False,
         "creada": imagen.get("creada", "")[:19], "imagen_id": imagen_id, "servicios": servicios,
         "playbook": comp.get("playbook")}
    if es_propia(cfg, ref):
        r.update(propia=True, actual=ref.tag or None, estado="propia")
        rotulos = imagen.get("etiquetas", {})
        # El rótulo dice sobre qué base se armó; la configuración, cuál aprobó el dueño. Si los dos la nombran, tienen que
        # coincidir: la foto es de otra máquina y no puede elegir de qué repositorio se arma una imagen.
        declarada = V.referencia(comp.get("base") or "")
        rotulada = V.referencia(rotulos.get(BASE_NOMBRE) or "")
        rotulada = rotulada if rotulada is not None and rotulada.tag else None
        declarada = declarada if declarada is not None and declarada.tag else None
        if declarada and rotulada and (rotulada.registro, rotulada.repo, rotulada.tag) != (
                declarada.registro, declarada.repo, declarada.tag):
            r["notas"].append(f"sus rótulos nombran otra base ({rotulada.repo}:{rotulada.tag}) que la declarada "
                              f"({declarada.repo}:{declarada.tag}): no se propone rehacerla")
            return r
        base = rotulada or declarada
        if base is None:
            r["notas"].append("sin imagen base declarada: no se sabe si reconstruirla traería parches")
            return r
        if es_propia(cfg, base):
            # Regla 4: un nombre propio no sale, tampoco como base de otra imagen.
            r["notas"].append("su base es otra imagen propia: no se consulta afuera ni se rehace sola")
            return r
        fuente = _fuente(cfg, base, consultar)
        hecha = _fecha(imagen.get("creada"))
        entrada = next((e for e in fuente["etiquetas"] if e["nombre"] == base.tag), None)
        publicada_texto = entrada["publicada"] if entrada else None
        indice = entrada.get("indice") if entrada else None
        if (publicada_texto is None or indice is None) and fuente["estado"] == "ok" and detalle is not None:
            d = detalle(base.registro, base.repo, base.tag) or {}
            publicada_texto, indice = publicada_texto or d.get("publicada"), indice or d.get("indice")
        publicada = _fecha(publicada_texto)
        hoy = rotulos.get(BASE_HUELLA, "")
        if _HUELLA.match(hoy):
            # Armada con la huella de su base en los rótulos: se sabe exactamente cuál tiene y se puede rehacer.
            r["base"] = "base " + hoy[7:19]
            if fuente["estado"] != "ok" or not _HUELLA.match(str(indice or "")):
                # Desconocido, no «propia»: con la fuente caída no se puede decir que su base está al día.
                r.update(estado="desconocido", motivo=f"base {base.repo}:{base.tag}: no se pudo saber si cambió")
            elif indice == hoy:
                r["base_al_dia"] = True
            else:
                nombre = f"{base.registro}/{base.repo}:{base.tag}"
                r["estado"] = "base renovada"
                r["notas"].append(f"la base {base.repo}:{base.tag} tiene una versión nueva: rehacerla trae parches")
                c = _candidata(cfg, servidor, ref, comp, "base " + hoy[7:19], "base " + indice[7:19], "patch",
                               publicada_texto, "base", ahora, indice, f"{nombre}@{hoy}")
                c["referencia"] = f"{nombre}@{indice}"
                c["nota"] = f"rehacer {ref.tag or 'latest'} igual, sobre la base nueva"
                # La versión que corre: al rehacerla y al volver atrás se mide también, no solo la base.
                c["version"] = rotulos.get(VERSION) or None
                r["candidata"] = c
            return r
        if fuente["estado"] != "ok" or hecha is None or publicada is None:
            r["notas"].append(f"base {base.repo}:{base.tag}: no se pudo saber si cambió")
        elif publicada > hecha:
            r["notas"].append(f"la base {base.repo}:{base.tag} se renovó el {publicada:%Y-%m-%d}, después de armar esta "
                              "imagen: reconstruirla trae parches")
            r["notas"].append("sin la huella de su base en los rótulos: no se puede rehacer sola")
            r["estado"] = "base renovada"
        return r
    fuente = _fuente(cfg, ref, consultar)
    if fuente["estado"] != "ok":
        r["motivo"] = fuente["motivo"]
        return r
    etiquetas = fuente["etiquetas"]
    nombres = [e["nombre"] for e in etiquetas]
    if fuente["hay_mas"]:
        r["notas"].append("el repositorio tiene más etiquetas que las consultadas")
    if not ref.digest:
        r["notas"].append("sin fijar por huella")
    actual, era_flotante = _resolver(ref.tag, locales, etiquetas)
    if era_flotante:
        r["notas"].append(f"la etiqueta «{ref.tag or 'latest'}» es flotante: cambia de imagen sin cambiar de nombre")
    if actual is None:
        # No se sabe qué versión es; sí se puede saber si la etiqueta ya apunta a otra imagen.
        hoy = next((e for e in etiquetas if e["nombre"] == (ref.tag or "latest")), None)
        if hoy is None or not hoy["digests"] or not locales:
            r["motivo"] = "no se pudo identificar la versión que corre"
            return r
        if set(hoy["digests"]) & locales:
            r.update(estado="al día", actual=ref.tag or "latest")
            return r
        # La propuesta es la etiqueta fija que hoy comparte imagen con la flotante, si existe: así se puede fijar.
        fijas = [o for e in etiquetas for o in [V.etiqueta(e["nombre"])]
                 if o is not None and not o.previa and set(e["digests"]) & set(hoy["digests"])
                 and not V.flotante(e["nombre"], nombres)]
        variante = getattr(V.etiqueta(ref.tag), "variante", "")
        propuesta = (max(fijas, key=lambda o: (o.variante == variante, len(o.numeros), o.clave)).texto
                     if fijas else (ref.tag or "latest"))
        r.update(estado="atrasada", actual=ref.tag or "latest")
        r["candidata"] = _candidata(cfg, servidor, ref, comp, ref.tag or "latest", propuesta, "patch",
                                    hoy["publicada"], "", ahora, hoy.get("indice"), anterior)
        r["candidata"]["nota"] = "la misma etiqueta apunta hoy a una imagen más nueva; no se sabe cuál corre"
        return r
    r["actual"] = actual.texto
    mejores = {}
    for e in etiquetas:
        o = V.etiqueta(e["nombre"])
        if o is None or o.previa or not V.misma_familia(actual, o) or o.clave <= actual.clave:
            continue
        if V.flotante(e["nombre"], nombres):
            continue
        tamano = V.salto(actual, o, comp.get("linea"))
        if tamano not in mejores or o.clave > mejores[tamano][0].clave:
            mejores[tamano] = (o, e)
    if not mejores:
        r["estado"] = "al día"
        return r
    r["estado"] = "atrasada"
    # La candidata es el paso más chico disponible; los más grandes se informan aparte.
    for tamano in V.SALTOS:
        if tamano in mejores:
            o, e = mejores.pop(tamano)
            publicada, indice = e["publicada"], e.get("indice")
            if (publicada is None or indice is None) and detalle is not None:
                d = detalle(ref.registro, ref.repo, o.texto) or {}
                publicada, indice = publicada or d.get("publicada"), indice or d.get("indice")
            r["candidata"] = _candidata(cfg, servidor, ref, comp, actual.texto, o.texto, tamano, publicada,
                                        actual.variante, ahora, indice, anterior)
            break
    r["otras"] = [{"version": o.texto, "salto": t} for t, (o, _) in sorted(mejores.items(), key=lambda x: V.SALTOS.index(x[0]))]
    return r


def _candidata(cfg, servidor, ref, comp, actual, propuesta, tamano, publicada, variante, ahora, indice=None, anterior=None):
    hecha = _fecha(publicada)
    madura = hecha + timedelta(days=cfg["maduracion_dias"]) if hecha else None
    if tamano == "mayor":
        compuerta = "fuera: salto mayor"
    elif comp["nivel"] == "nunca":
        compuerta = "fuera: la pieza no admite aplicación automática"
    elif not V.permite(comp["nivel"], tamano):
        compuerta = f"fuera: salto {tamano} sobre un nivel {comp['nivel']}"
    elif madura is None:
        compuerta = "espera: sin fecha de publicación"
    elif madura > datetime.fromtimestamp(ahora, timezone.utc):
        compuerta = f"espera: madura el {madura:%Y-%m-%d}"
    else:
        compuerta = "pasaría"
    return {"id": identificador(servidor, ref.pieza, variante, propuesta), "servidor": servidor, "pieza": ref.pieza,
            "actual": actual, "propuesta": propuesta, "salto": tamano, "publicada": _iso(hecha), "madura": _iso(madura),
            "nivel": comp["nivel"], "expuesto": comp["expuesto"], "compuerta_version": compuerta, "nota": "",
            "indice": indice, "referencia": f"{ref.pieza}:{propuesta}@{indice}" if indice else None, "anterior": anterior}


def piezas(cfg, servidor, foto, consultar, detalle=None, ahora=0):
    """Una entrada por imagen distinta que corre en el servidor."""
    if foto["contenedores"] is None:
        return None
    por_imagen = {}
    for c in foto["contenedores"]:
        por_imagen.setdefault((c["imagen_ref"], c["imagen_id"]), []).append(c)
    salida = []
    for (texto, imagen_id), usan in sorted(por_imagen.items()):
        imagen = foto["imagenes"].get(imagen_id, {})
        ref = None if es_id(texto, imagen_id) else V.referencia(texto)
        if ref is None:
            # Un contenedor creado desde un ID de imagen («sha256:…» o su forma corta): nunca se consulta como si fuera un
            # repositorio público. Se usa una etiqueta local de esa imagen, una propia antes que cualquier otra; sin
            # ninguna, queda «sin referencia».
            locales = [t for t in imagen.get("tags", []) if V.referencia(t) and not es_id(t, imagen_id)]
            elegida = next((t for t in locales if es_propia(cfg, V.referencia(t))), locales[0] if locales else None)
            if elegida:
                texto, ref = elegida, V.referencia(elegida)
        if ref is None:
            salida.append({"servidor": servidor, "pieza": "(sin referencia)", "contenedores": sorted(c["nombre"] for c in usan),
                           "etiqueta": "", "fijada": False, "actual": None, "estado": "desconocido",
                           "motivo": "el contenedor no dice de qué imagen salió", "candidata": None, "otras": [],
                           "notas": [], "nivel": "nunca", "expuesto": False, "propia": False, "creada": "",
                           "imagen_id": imagen_id, "servicios": [], "playbook": None})
            continue
        salida.append(_pieza(cfg, servidor, ref, texto, imagen, imagen_id, usan, consultar, detalle, ahora))
    salida.append(pieza_sistema(cfg, servidor, foto))
    return salida


def es_id(texto, imagen_id):
    """¿La referencia del contenedor es el ID de su imagen y no un nombre?"""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", texto or ""):
        return True
    return bool(re.fullmatch(r"[0-9a-f]{12,64}", texto or "")) and str(imagen_id or "")[7:].startswith(texto)


def pieza_sistema(cfg, servidor, foto):
    """El sistema del servidor como una pieza más. Los paquetes los instala la rutina desatendida de la distribución; lo
    que queda para el vigía es el reinicio que esa rutina pide (un kernel o una biblioteca nuevos). Su candidata no tiene
    maduración: lo instalado ya está instalado."""
    s = foto["sistema"]
    comp = _config.componente(cfg, SISTEMA)
    kernels = s["kernels"] or []
    en_uso = s["kernel_en_uso"]
    nuevo = bool(en_uso and kernels and en_uso in kernels and en_uso != _mayor(kernels))
    r = {"servidor": servidor, "pieza": SISTEMA, "contenedores": [], "etiqueta": "", "fijada": True, "actual": en_uso,
         "estado": "al día", "motivo": "", "candidata": None, "otras": [], "notas": [], "nivel": comp["nivel"],
         "expuesto": comp["expuesto"], "propia": False, "creada": "", "imagen_id": None, "servicios": [],
         "playbook": comp.get("playbook"), "sistema": True, "arrancado": s.get("arrancado")}
    if en_uso is None:
        r.update(estado="desconocido", motivo="no se pudo leer el kernel en uso")
        return r
    if not (s["reinicio_pedido"] or nuevo):
        return r
    propuesta = _mayor(kernels) if nuevo else f"{en_uso} reiniciado"
    r["estado"] = "reinicio pedido"
    if comp["nivel"] == "nunca":
        compuerta = "fuera: la pieza no admite aplicación automática"
    else:
        compuerta = "pasaría"
    r["candidata"] = {
        "id": identificador(servidor, SISTEMA, "", propuesta), "servidor": servidor, "pieza": SISTEMA, "actual": en_uso,
        "propuesta": propuesta, "salto": "patch", "publicada": None, "madura": None, "nivel": comp["nivel"],
        "expuesto": comp["expuesto"], "compuerta_version": compuerta,
        "nota": "kernel nuevo instalado sin usar" if nuevo else "el sistema pide reinicio", "indice": None,
        "referencia": None, "anterior": None}
    return r


def sistema(foto):
    """Resumen del sistema: paquetes pendientes (los de seguridad aparte) y si pide reinicio."""
    s = foto["sistema"]
    paquetes = s["paquetes"]
    kernels = s["kernels"] or []
    en_uso = s["kernel_en_uso"]
    return {"nombre": s["nombre"], "kernel_en_uso": en_uso,
            "kernel_nuevo_instalado": bool(en_uso and kernels and en_uso in kernels and en_uso != _mayor(kernels)),
            "reinicio_pedido": s["reinicio_pedido"], "desatendidas": s["desatendidas"],
            "pendientes": None if paquetes is None else len(paquetes),
            "de_seguridad": None if paquetes is None else sum(1 for p in paquetes if p["seguridad"]),
            "paquetes": paquetes, "listas_al": s["listas_al"]}


def _mayor(kernels):
    return max(kernels, key=lambda k: [int(n) for n in re.findall(r"\d+", k)])


def plazo(cfg, candidata, vulnerabilidades, detectada):
    """Hasta cuándo debería estar aplicada, contando desde que se detectó. Un KEV solo acorta el plazo si su
    aplicabilidad está verificada; si no, cuenta como una vulnerabilidad alta y se marca para revisar."""
    v = vulnerabilidades or {}
    horas = cfg["plazos_h"]["ordinario"]
    clase = "ordinario"
    if v.get("altas", 0) or v.get("kev"):
        clase = "alta_expuesta" if candidata["expuesto"] else "alta_interna"
        horas = cfg["plazos_h"][clase]
    if v.get("kev_aplicable") and candidata["expuesto"]:
        clase, horas = "kev_expuesto", cfg["plazos_h"]["kev_expuesto"]
    return {"clase": clase, "vence": _iso(datetime.fromtimestamp(detectada, timezone.utc) + timedelta(hours=horas))}
