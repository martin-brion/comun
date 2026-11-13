"""De la foto y las fuentes a la lista de piezas: qué corre, qué versión hay y qué tan grande es el paso.

Esta versión observa: la compuerta de versión dice si una candidata «pasaría», pero nada se aplica. Lo que no se pudo
saber queda «desconocido»; nunca «al día».
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from . import config as _config
from . import versiones as V


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


def _pieza(cfg, servidor, ref, imagen, imagen_id, contenedores, consultar, detalle, ahora):
    comp = _config.componente(cfg, ref.pieza)
    locales = {d.rpartition("@")[2] for d in imagen.get("digests", [])} | ({ref.digest} if ref.digest else set())
    r = {"servidor": servidor, "pieza": ref.pieza, "contenedores": sorted(contenedores), "etiqueta": ref.tag,
         "fijada": bool(ref.digest), "actual": None, "estado": "desconocido", "motivo": "", "candidata": None,
         "otras": [], "notas": [], "nivel": comp["nivel"], "expuesto": comp["expuesto"], "propia": False,
         "creada": imagen.get("creada", "")[:19], "imagen_id": imagen_id}
    if es_propia(cfg, ref):
        r.update(propia=True, actual=ref.tag or None, estado="propia")
        base = V.referencia(comp.get("base") or imagen.get("etiquetas", {}).get("org.opencontainers.image.base.name") or "")
        if base is None or not base.tag:
            r["notas"].append("sin imagen base declarada: no se sabe si reconstruirla traería parches")
            return r
        fuente = _fuente(cfg, base, consultar)
        hecha = _fecha(imagen.get("creada"))
        publicada = next((_fecha(e["publicada"]) for e in fuente["etiquetas"] if e["nombre"] == base.tag), None)
        if publicada is None and fuente["estado"] == "ok" and detalle is not None:
            publicada = _fecha((detalle(base.registro, base.repo, base.tag) or {}).get("publicada"))
        if fuente["estado"] != "ok" or hecha is None or publicada is None:
            r["notas"].append(f"base {base.repo}:{base.tag}: no se pudo saber si cambió")
        elif publicada > hecha:
            r["notas"].append(f"la base {base.repo}:{base.tag} se renovó el {publicada:%Y-%m-%d}, después de armar esta "
                              "imagen: reconstruirla trae parches")
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
                                    hoy["publicada"], "", ahora)
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
            publicada = e["publicada"]
            if publicada is None and detalle is not None:
                publicada = (detalle(ref.registro, ref.repo, o.texto) or {}).get("publicada")
            r["candidata"] = _candidata(cfg, servidor, ref, comp, actual.texto, o.texto, tamano, publicada,
                                        actual.variante, ahora)
            break
    r["otras"] = [{"version": o.texto, "salto": t} for t, (o, _) in sorted(mejores.items(), key=lambda x: V.SALTOS.index(x[0]))]
    return r


def _candidata(cfg, servidor, ref, comp, actual, propuesta, tamano, publicada, variante, ahora):
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
            "nivel": comp["nivel"], "expuesto": comp["expuesto"], "compuerta_version": compuerta, "nota": ""}


def piezas(cfg, servidor, foto, consultar, detalle=None, ahora=0):
    """Una entrada por imagen distinta que corre en el servidor."""
    if foto["contenedores"] is None:
        return None
    por_imagen = {}
    for c in foto["contenedores"]:
        por_imagen.setdefault((c["imagen_ref"], c["imagen_id"]), []).append(c["nombre"])
    salida = []
    for (texto, imagen_id), nombres in sorted(por_imagen.items()):
        ref = V.referencia(texto)
        imagen = foto["imagenes"].get(imagen_id, {})
        if ref is None:
            # Un contenedor creado desde un ID de imagen: se usa la primera etiqueta local, si la hay.
            ref = next((x for x in map(V.referencia, imagen.get("tags", [])) if x), None)
        if ref is None:
            salida.append({"servidor": servidor, "pieza": "(sin referencia)", "contenedores": sorted(nombres),
                           "etiqueta": "", "fijada": False, "actual": None, "estado": "desconocido",
                           "motivo": "el contenedor no dice de qué imagen salió", "candidata": None, "otras": [],
                           "notas": [], "nivel": "nunca", "expuesto": False, "propia": False, "creada": "",
                           "imagen_id": imagen_id})
            continue
        salida.append(_pieza(cfg, servidor, ref, imagen, imagen_id, nombres, consultar, detalle, ahora))
    return salida


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
