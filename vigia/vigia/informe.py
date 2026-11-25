"""Lo que queda escrito de una corrida: la ficha completa (Markdown, para el registro) y el resumen corto para una persona.
Las dos dicen qué no se pudo saber; ninguna lo cuenta como «al día»."""
import os
from datetime import datetime, timezone

PASOS = {"patch": "parche", "minor": "versión menor", "mayor": "versión mayor"}


def _cuando(ts, formato="%Y-%m-%d %H:%M UTC"):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(formato)


def _si(valor):
    return "sin dato" if valor is None else ("sí" if valor else "no")


def _corta(pieza):
    return pieza.replace("docker.io/library/", "").replace("docker.io/", "")


def _vulnerabilidades(v):
    if not v or v.get("estado") != "ok":
        return "desconocido" + (f" ({v['motivo']})" if v and v.get("motivo") else "")
    texto = f"{v['total']} ({v['altas']} altas, {v['altas_con_arreglo']} con arreglo)"
    con, sin = v.get("kev_con_arreglo") or [], v.get("kev_sin_arreglo") or []
    return (texto + (f"; en KEV, con arreglo: {', '.join(con)}" if con else "")
            + (f"; en KEV, sin arreglo: {', '.join(sin)}" if sin else ""))


def ficha(corrida):
    r = [f"# Vigía · {_cuando(corrida['ts'])}", "", "Observar no aplica nada: lo aplica el ejecutor, en su ventana.", ""]
    for s in corrida["servidores"]:
        r += [f"## {s['nombre']}", ""]
        if s["estado"] != "ok":
            r += [f"**Desconocido:** {s['motivo']}. No se cerró ni se descartó nada de este servidor.", ""]
            continue
        sis = s["sistema"]
        r += [f"- Sistema: {sis['nombre'] or 'sin dato'} · kernel {sis['kernel_en_uso'] or 'sin dato'}",
              f"- Parches automáticos del sistema: {_si(sis['desatendidas'])} · reinicio pedido: {_si(sis['reinicio_pedido'])}"
              f" · kernel más nuevo instalado sin usar: {_si(sis['kernel_nuevo_instalado'])}"]
        if sis["pendientes"] is None:
            r += ["- Paquetes pendientes: desconocido"]
        else:
            r += [f"- Paquetes pendientes: {sis['pendientes']} ({sis['de_seguridad']} de seguridad)"]
            if sis["listas_al"]:
                r += [f"- Listas de paquetes al {_cuando(sis['listas_al'])}"]
            if sis["paquetes"]:
                r += ["", "| Paquete | De | A | Origen |", "|---|---|---|---|"]
                r += [f"| {p['nombre']} | {p['de']} | {p['a']} | {p['origen']} |" for p in sis["paquetes"]]
        for e in s["errores"]:
            r += [f"- No se pudo medir: {e}"]
        r += [""]
        if s["piezas"] is None:
            r += ["**Contenedores: desconocido.** No se cerró ni se descartó nada de este servidor.", ""]
            continue
        r += ["| Pieza | Corre | Estado | Propuesta | Paso | Publicada | Compuerta de versión | Plazo | Vulnerabilidades |",
              "|---|---|---|---|---|---|---|---|---|"]
        for p in s["piezas"]:
            c = p.get("candidata") or {}
            plazo = c.get("plazo") or {}
            r += ["| " + " | ".join([
                _corta(p["pieza"]), p["actual"] or "sin dato", p["estado"], c.get("propuesta", ""),
                PASOS.get(c.get("salto"), ""), (c.get("publicada") or "")[:10], c.get("compuerta_version", ""),
                (plazo.get("vence") or "")[:10], _vulnerabilidades(p.get("vulnerabilidades"))]) + " |"]
        notas = [(p, n) for p in s["piezas"] for n in p["notas"] + ([p["motivo"]] if p["motivo"] else [])
                 + ([(p.get("candidata") or {}).get("nota")] if (p.get("candidata") or {}).get("nota") else [])
                 + ([f"urgencia: {', '.join(p['candidata']['urgencia'])} con explotación conocida y arreglo; va por el "
                     "carril de urgencia"] if (p.get("candidata") or {}).get("urgencia") else [])
                 + [f"hay además un paso de {PASOS[o['salto']]}: {o['version']}" for o in p["otras"]]]
        if notas:
            r += ["", "### Notas", ""] + [f"- {_corta(p['pieza'])}: {n}" for p, n in notas]
        r += [""]
    r += ["## Fuentes", "", f"- Lista de vulnerabilidades explotadas (KEV): {corrida['kev']['estado']}"
          + (f" ({corrida['kev']['motivo']})" if corrida["kev"]["motivo"] else "")]
    for nombre, f in sorted(corrida["fuentes"].items()):
        r += [f"- {_corta(nombre)}: {f['estado']}" + (f" ({f['motivo']})" if f["motivo"] else "")]
    m = corrida.get("maquinas")
    if m:
        r += ["", "## Máquinas virtuales", ""]
        if m["estado"] != "ok":
            r += [f"**Desconocido:** {m['motivo']}."]
        else:
            r += [f"- {n}: {e}" for n, e in sorted(m["maquinas"].items())]
    if corrida.get("repos"):
        r += ["", "## Repositorios montados", "", "| Repositorio | Servidor | Rama | Local | Remoto | Estado |",
              "|---|---|---|---|---|---|"]
        r += [f"| {x['nombre']} | {x['servidor']} | {x['rama']} | {(x['local'] or 'sin dato')[:12]} | "
              f"{(x['remoto'] or 'sin dato')[:12]} | {x['estado']}" + (f" ({x['motivo']})" if x["motivo"] else "") + " |"
              for x in corrida["repos"]]
    if corrida.get("incidentes"):
        r += ["", "## Incidentes abiertos", ""] + [f"- {i['clase']}: {i['texto']}" for i in corrida["incidentes"]]
    cambios = corrida["cambios"]
    r += ["", "## Cambios desde la corrida anterior", "",
          f"- Candidatas nuevas: {len(cambios['nuevas'])} · cerradas: {len(cambios['cerradas'])} · "
          f"descartadas: {len(cambios['descartadas'])}", ""]
    return "\n".join(r)


def resumen(corrida):
    """Unas líneas para una persona: qué está atrasado, qué no se pudo saber y qué pide atención."""
    r = [f"Vigía de parches · {_cuando(corrida['ts'], '%d/%m %H:%M UTC')}"]
    for s in corrida["servidores"]:
        if s["estado"] != "ok":
            r += [f"{s['nombre']}: no se pudo medir ({s['motivo']})."]
            continue
        sis = s["sistema"]
        if sis["pendientes"] is None:
            linea = "sistema: no se pudo consultar los paquetes"
        elif sis["de_seguridad"]:
            linea = f"sistema: {sis['de_seguridad']} parches de seguridad pendientes"
        else:
            linea = f"sistema: sin parches de seguridad pendientes ({sis['pendientes']} paquetes menores)"
        if sis["reinicio_pedido"] or sis["kernel_nuevo_instalado"]:
            linea += "; pide reinicio"
        if sis["desatendidas"] is False:
            linea += "; los parches automáticos están apagados"
        r += [f"{s['nombre']} · {linea}."]
        if s["piezas"] is None:
            r += ["  No se pudo listar los contenedores."]
            continue
        por = {}
        for p in s["piezas"]:
            if not p.get("sistema"):
                por.setdefault(p["estado"], []).append(p)
        atrasadas = por.get("atrasada", [])
        r += [f"  Al día: {len(por.get('al día', []))} · atrasadas: {len(atrasadas)} · sin poder saber: "
              f"{len(por.get('desconocido', []))} · propias: {len(por.get('propia', [])) + len(por.get('base renovada', []))}"]
        for p in atrasadas:
            c = p["candidata"]
            extra = "".join(f"; además hay {PASOS[o['salto']]} {o['version']}" for o in p["otras"])
            r += [f"  - {_corta(p['pieza'])}: {c['actual']} → {c['propuesta']} ({PASOS[c['salto']]}){extra}"]
        for p in por.get("base renovada", []):
            r += [f"  - {_corta(p['pieza'])}: conviene reconstruirla, su imagen base se renovó"]
        for p in por.get("desconocido", []):
            r += [f"  - {_corta(p['pieza'])}: sin poder saber ({p['motivo']})"]
        for p in s["piezas"]:
            v = p.get("vulnerabilidades") or {}
            if (p.get("candidata") or {}).get("urgencia"):
                r += [f"  ! {_corta(p['pieza'])}: falla con explotación conocida y arreglo publicado; va por el carril de "
                      "urgencia"]
            elif v.get("kev_sin_arreglo"):
                r += [f"  ! {_corta(p['pieza'])}: falla con explotación conocida sin arreglo publicado"]
            elif v.get("kev_con_arreglo"):
                r += [f"  ! {_corta(p['pieza'])}: falla con explotación conocida y arreglo publicado; no da a internet: "
                      "va por su plazo"]
    m = corrida.get("maquinas")
    if m:
        r += [f"Máquinas virtuales: {len(m['maquinas'])} en el censo." if m["estado"] == "ok"
              else f"Máquinas virtuales: desconocido ({m['motivo']})."]
    distintos = [x for x in corrida.get("repos") or [] if x["estado"] != "al día"]
    if distintos:
        r += [f"Repositorios: {len(distintos)} distintos de su remoto o sin poder saber: "
              + ", ".join(x["nombre"] for x in distintos) + "."]
    if corrida.get("incidentes"):
        r += [f"Incidentes abiertos: {len(corrida['incidentes'])} (cada uno ya se avisó)."]
    r += ["Observar no aplica nada: lo aplica el ejecutor, en su ventana."]
    return "\n".join(r) + "\n"


def publicar(registro_dir, corrida):
    """Guarda la ficha en `<registro>/<año>/<mes>/` y rehace el índice. Devuelve la ruta de la ficha."""
    carpeta = os.path.join(registro_dir, _cuando(corrida["ts"], "%Y"), _cuando(corrida["ts"], "%m"))
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, _cuando(corrida["ts"], "%y%m%d-%H%M%S") + ".md")
    _escribir(ruta, ficha(corrida))
    fichas = []
    for raiz, _, archivos in os.walk(registro_dir):
        fichas += [os.path.relpath(os.path.join(raiz, a), registro_dir).replace(os.sep, "/")
                   for a in archivos if a.endswith(".md") and a != "indice.md"]
    _escribir(os.path.join(registro_dir, "indice.md"),
              "# Registro del vigía\n\n" + "".join(f"- [{f[8:-3]}]({f})\n" for f in sorted(fichas, reverse=True)))
    return ruta


def acta(a):
    r = [f"# Vigía · ejecutor · {_cuando(a['ts'])}", "",
         f"- Servidor de esta ventana: {a['servidor'] or 'ninguno'}",
         f"- Ventana: {a.get('ventana', 'ordinaria')} · con el dueño presente: {_si(a['presente'])}"]
    if a["motivo"]:
        r += [f"- **No siguió:** {a['motivo']}"]
    aplicadas = [p for p in a["pasos"] if p["resultado"] == "aplicada"]
    r += [f"- Aplicadas: {len(aplicadas)} de {len(a['pasos'])} candidatas", ""]
    for p in a["pasos"]:
        r += [f"## {_corta(p['pieza'])}: {p['de']} → {p['a']} · {p['resultado']}", ""]
        r += [f"- {'pasa' if ok else '**frena**'} · {nombre}: {detalle}" for nombre, ok, detalle in p.get("compuertas", [])]
        r += [""]
    return "\n".join(r)


def publicar_acta(registro_dir, a):
    carpeta = os.path.join(registro_dir, _cuando(a["ts"], "%Y"), _cuando(a["ts"], "%m"))
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, _cuando(a["ts"], "%y%m%d-%H%M%S") + "-ejecutor.md")
    _escribir(ruta, acta(a))
    return ruta


def _escribir(ruta, texto):
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    os.replace(temporal, ruta)
