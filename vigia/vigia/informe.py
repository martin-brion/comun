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
    return texto + (f"; en KEV sin aplicabilidad verificada: {', '.join(v['kev'])}" if v["kev"] else "")


def ficha(corrida):
    r = [f"# Vigía · {_cuando(corrida['ts'])}", "", "Esta versión observa: no se aplicó nada.", ""]
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
                 + [f"hay además un paso de {PASOS[o['salto']]}: {o['version']}" for o in p["otras"]]]
        if notas:
            r += ["", "### Notas", ""] + [f"- {_corta(p['pieza'])}: {n}" for p, n in notas]
        r += [""]
    r += ["## Fuentes", "", f"- Lista de vulnerabilidades explotadas (KEV): {corrida['kev']['estado']}"
          + (f" ({corrida['kev']['motivo']})" if corrida["kev"]["motivo"] else "")]
    for nombre, f in sorted(corrida["fuentes"].items()):
        r += [f"- {_corta(nombre)}: {f['estado']}" + (f" ({f['motivo']})" if f["motivo"] else "")]
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
        urgentes = [p for p in s["piezas"] if (p.get("vulnerabilidades") or {}).get("kev")]
        for p in urgentes:
            r += [f"  ! {_corta(p['pieza'])}: tiene vulnerabilidades con explotación conocida; falta verificar si aplican"]
    r += ["No se aplicó nada: esta versión solo mira."]
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


def _escribir(ruta, texto):
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    os.replace(temporal, ruta)
