"""El estado que sobrevive entre corridas: las candidatas, con la fecha en que se detectó cada una. El plazo se cuenta desde
ahí y no se reinicia porque el servicio o el servidor se reinicien.

Estados: detectada → aplicando → verificada → cerrada, con esperando (un intento falló y se volvió atrás), escalada
(hace falta una persona) y descartada (la reemplazó otra candidata, o la pieza dejó de correr). Reservados: evaluada,
ensayada, programada. Una pieza que esta corrida no pudo medir no cierra ni descarta nada.

El diario guarda la intención antes de cada efecto y su resultado después: tras un corte se sabe qué quedó a medias.
"""
import json
import os
import sqlite3

ABIERTOS = ("detectada", "esperando", "escalada")      # el observador las sigue; «aplicando» es del ejecutor
_ESQUEMA = """
CREATE TABLE IF NOT EXISTS candidatas (
  id TEXT PRIMARY KEY, servidor TEXT NOT NULL, pieza TEXT NOT NULL, actual TEXT NOT NULL, propuesta TEXT NOT NULL,
  salto TEXT NOT NULL, estado TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT '', detectada INTEGER NOT NULL,
  actualizada INTEGER NOT NULL, datos TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS corridas (ts INTEGER PRIMARY KEY, resumen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diario (
  n INTEGER PRIMARY KEY AUTOINCREMENT, candidata TEXT NOT NULL, servidor TEXT NOT NULL, paso TEXT NOT NULL,
  datos TEXT NOT NULL, abierta INTEGER NOT NULL, resultado TEXT, detalle TEXT NOT NULL DEFAULT '', cerrada INTEGER);
CREATE TABLE IF NOT EXISTS estrenos (playbook TEXT PRIMARY KEY, ts INTEGER NOT NULL, candidata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ventanas (fecha TEXT PRIMARY KEY, servidor TEXT NOT NULL, aplicadas INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS incidentes (
  id TEXT PRIMARY KEY, clase TEXT NOT NULL, texto TEXT NOT NULL, abierto INTEGER NOT NULL, cerrado INTEGER,
  avisado INTEGER);
CREATE TABLE IF NOT EXISTS bloqueos (
  candidata TEXT PRIMARY KEY, compuerta TEXT NOT NULL, detalle TEXT NOT NULL, fechas TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS marcas (clave TEXT PRIMARY KEY, desde INTEGER NOT NULL);
"""
# Columnas que se sumaron después: un estado de una versión anterior se completa al abrirlo, sin perder nada.
_AGREGADAS = {"incidentes": (("tipo", "TEXT NOT NULL DEFAULT 'incidente'"), ("cierre_avisado", "INTEGER"))}


def abrir(directorio):
    os.makedirs(directorio, exist_ok=True)
    db = sqlite3.connect(os.path.join(directorio, "vigia.sqlite3"), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous = FULL")
    db.executescript(_ESQUEMA)
    for tabla, columnas in _AGREGADAS.items():
        hay = {f["name"] for f in db.execute(f"PRAGMA table_info({tabla})")}
        with db:
            for nombre, tipo in columnas:
                if nombre not in hay:
                    db.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
                    if nombre == "cierre_avisado":
                        # Lo que ya estaba cerrado antes de esta versión no se anuncia ahora como cerrado.
                        db.execute("UPDATE incidentes SET cierre_avisado = cerrado WHERE cerrado IS NOT NULL")
    return db


def registrar(db, servidor, piezas, ahora):
    """Cruza las piezas medidas en un servidor con lo guardado. Devuelve {"nuevas", "cerradas", "descartadas"} (ids).
    `piezas` es None si el servidor no se pudo medir: en ese caso no cambia nada."""
    cambios = {"nuevas": [], "cerradas": [], "descartadas": []}
    if piezas is None:
        return cambios
    with db:
        # Varias imágenes pueden tener el mismo nombre de pieza (por ejemplo, una propia rehecha en un servicio y no en
        # otro): se mira cada una, y una que no se pudo medir deja todo como está.
        medidas, sin_medir = {}, {p["pieza"] for p in piezas if p["estado"] == "desconocido"}
        for p in piezas:
            if p["estado"] != "desconocido":
                medidas.setdefault(p["pieza"], []).append(p)
        vigentes = {p["candidata"]["id"]: p["candidata"] for p in piezas if p.get("candidata")}
        for c in vigentes.values():
            fila = db.execute("SELECT estado FROM candidatas WHERE id = ?", (c["id"],)).fetchone()
            if fila is None:
                db.execute("INSERT INTO candidatas (id, servidor, pieza, actual, propuesta, salto, estado, detectada, "
                           "actualizada, datos) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (c["id"], servidor, c["pieza"], c["actual"], c["propuesta"], c["salto"], "detectada", ahora,
                            ahora, json.dumps(c, sort_keys=True)))
                cambios["nuevas"].append(c["id"])
            elif fila["estado"] in ABIERTOS or fila["estado"] == "aplicando":
                db.execute("UPDATE candidatas SET actual = ?, actualizada = ?, datos = ? WHERE id = ?",
                           (c["actual"], ahora, json.dumps(c, sort_keys=True), c["id"]))
            else:
                # Una candidata que se había cerrado o descartado vuelve a aparecer (por ejemplo, una vuelta atrás).
                db.execute("UPDATE candidatas SET estado = 'detectada', motivo = '', detectada = ?, actualizada = ?, "
                           "actual = ?, datos = ? WHERE id = ?", (ahora, ahora, c["actual"], json.dumps(c, sort_keys=True), c["id"]))
                cambios["nuevas"].append(c["id"])
        abiertas = db.execute("SELECT id, pieza, propuesta FROM candidatas WHERE servidor = ? AND estado IN (?,?,?)",
                              (servidor,) + ABIERTOS).fetchall()
        corriendo = {p["pieza"] for p in piezas}
        for fila in abiertas:
            if fila["id"] in vigentes:
                continue
            lista = medidas.get(fila["pieza"])
            if lista is None or fila["pieza"] in sin_medir:
                if fila["pieza"] in corriendo:
                    continue                                   # corre, pero hoy no se pudo medir: queda como está
                estado, motivo = "descartada", "la pieza ya no corre en el servidor"
            elif any(m.get("candidata") for m in lista):
                otra = next(m for m in lista if m.get("candidata"))
                estado, motivo = "descartada", f"la reemplazó {otra['candidata']['propuesta']}"
            elif all(m["actual"] == fila["propuesta"] or m["estado"] == "al día" for m in lista):
                estado, motivo = "cerrada", f"la pieza corre {lista[0]['actual']}"
            elif all(m.get("base") == fila["propuesta"] or m.get("base_al_dia") for m in lista):
                estado, motivo = "cerrada", f"la pieza corre sobre su base al día ({lista[0].get('base')})"
            else:
                continue
            db.execute("UPDATE candidatas SET estado = ?, motivo = ?, actualizada = ? WHERE id = ?",
                       (estado, motivo, ahora, fila["id"]))
            cambios["cerradas" if estado == "cerrada" else "descartadas"].append(fila["id"])
    return cambios


def detectada(db, identificador):
    fila = db.execute("SELECT detectada FROM candidatas WHERE id = ?", (identificador,)).fetchone()
    return fila["detectada"] if fila else None


def abiertas(db):
    return [dict(f, datos=json.loads(f["datos"])) for f in
            db.execute("SELECT * FROM candidatas WHERE estado IN (?,?,?) ORDER BY servidor, pieza", ABIERTOS)]


def candidata(db, identificador):
    f = db.execute("SELECT * FROM candidatas WHERE id = ?", (identificador,)).fetchone()
    return dict(f, datos=json.loads(f["datos"])) if f else None


def pasar(db, identificador, estado, motivo, ahora):
    with db:
        db.execute("UPDATE candidatas SET estado = ?, motivo = ?, actualizada = ? WHERE id = ?",
                   (estado, motivo, ahora, identificador))


def intencion(db, candidata_id, servidor, paso, datos, ahora):
    """Deja escrito lo que se va a hacer ANTES de hacerlo. Devuelve el número de la entrada."""
    with db:
        return db.execute("INSERT INTO diario (candidata, servidor, paso, datos, abierta) VALUES (?,?,?,?,?)",
                          (candidata_id, servidor, paso, json.dumps(datos, sort_keys=True), ahora)).lastrowid


def resultado(db, n, valor, detalle, ahora):
    with db:
        db.execute("UPDATE diario SET resultado = ?, detalle = ?, cerrada = ? WHERE n = ? AND resultado IS NULL",
                   (valor, detalle, ahora, n))


def a_medias(db):
    """Intenciones sin resultado: lo que un corte dejó sin saber si se hizo."""
    return [dict(f, datos=json.loads(f["datos"])) for f in
            db.execute("SELECT * FROM diario WHERE resultado IS NULL ORDER BY n")]


def diario(db, desde=0):
    return [dict(f, datos=json.loads(f["datos"])) for f in db.execute("SELECT * FROM diario WHERE n > ? ORDER BY n", (desde,))]


def estrenado(db, playbook):
    return db.execute("SELECT 1 FROM estrenos WHERE playbook = ?", (playbook,)).fetchone() is not None


def estrenar(db, playbook, candidata_id, ahora):
    with db:
        db.execute("INSERT OR IGNORE INTO estrenos (playbook, ts, candidata) VALUES (?,?,?)", (playbook, ahora, candidata_id))


def ventana(db, fecha):
    f = db.execute("SELECT servidor, aplicadas FROM ventanas WHERE fecha = ?", (fecha,)).fetchone()
    return dict(f) if f else None


def ultima_ventana(db, servidor):
    f = db.execute("SELECT MAX(fecha) AS fecha FROM ventanas WHERE servidor = ?", (servidor,)).fetchone()
    return f["fecha"] or ""


def tomar_ventana(db, fecha, servidor):
    with db:
        db.execute("INSERT OR IGNORE INTO ventanas (fecha, servidor) VALUES (?,?)", (fecha, servidor))


def contar_aplicada(db, fecha):
    with db:
        db.execute("UPDATE ventanas SET aplicadas = aplicadas + 1 WHERE fecha = ?", (fecha,))


def incidente(db, identificador, clase, texto, ahora):
    """Abre un incidente, una sola vez por identificador mientras siga abierto."""
    with db:
        f = db.execute("SELECT cerrado FROM incidentes WHERE id = ?", (identificador,)).fetchone()
        if f is None:
            db.execute("INSERT INTO incidentes (id, clase, texto, abierto) VALUES (?,?,?,?)", (identificador, clase, texto, ahora))
        elif f["cerrado"] is not None:
            db.execute("UPDATE incidentes SET clase = ?, texto = ?, abierto = ?, cerrado = NULL, avisado = NULL, "
                       "cierre_avisado = NULL WHERE id = ?",
                       (clase, texto, ahora, identificador))


def cerrar_incidente(db, identificador, ahora):
    with db:
        db.execute("UPDATE incidentes SET cerrado = ? WHERE id = ? AND cerrado IS NULL", (ahora, identificador))


def incidentes(db):
    return [dict(f) for f in db.execute(
        "SELECT * FROM incidentes WHERE cerrado IS NULL AND tipo = 'incidente' ORDER BY abierto")]


def abierto(db, identificador):
    return db.execute("SELECT 1 FROM incidentes WHERE id = ? AND cerrado IS NULL AND tipo = 'incidente'",
                      (identificador,)).fetchone() is not None


def cerrar_por_prefijo(db, prefijo, ahora, salvo=()):
    """Cierra los incidentes abiertos cuyo identificador empieza así, salvo los nombrados."""
    for f in db.execute("SELECT id FROM incidentes WHERE cerrado IS NULL AND tipo = 'incidente' AND id LIKE ?",
                        (prefijo.replace("%", "") + "%",)).fetchall():
        if f["id"] not in salvo:
            cerrar_incidente(db, f["id"], ahora)


def noticia(db, identificador, clase, texto, ahora):
    """Un aviso de una sola vez, sin incidente abierto (por ejemplo, una aplicación por urgencia): nada que cerrar."""
    with db:
        db.execute("INSERT OR IGNORE INTO incidentes (id, clase, texto, abierto, cerrado, tipo) VALUES (?,?,?,?,?,?)",
                   (identificador, clase, texto, ahora, ahora, "noticia"))


def por_avisar(db, ahora, cada_s=86400):
    """Los incidentes abiertos sin aviso, o avisados hace más de un día (un aviso por incidente y un recordatorio
    diario), y las noticias que todavía no salieron."""
    filas = [dict(f) for f in db.execute(
        "SELECT * FROM incidentes WHERE (tipo = 'incidente' AND cerrado IS NULL AND (avisado IS NULL OR avisado <= ?)) "
        "OR (tipo = 'noticia' AND avisado IS NULL) ORDER BY abierto", (ahora - cada_s,))]
    with db:
        db.executemany("UPDATE incidentes SET avisado = ? WHERE id = ?", [(ahora, f["id"]) for f in filas])
    return filas


def por_avisar_cierre(db, ahora):
    """Los incidentes que se avisaron y ya se cerraron: su cierre se avisa una vez."""
    filas = [dict(f) for f in db.execute(
        "SELECT * FROM incidentes WHERE tipo = 'incidente' AND cerrado IS NOT NULL AND avisado IS NOT NULL "
        "AND cierre_avisado IS NULL ORDER BY cerrado")]
    with db:
        db.executemany("UPDATE incidentes SET cierre_avisado = ? WHERE id = ?", [(ahora, f["id"]) for f in filas])
    return filas


def bloqueo(db, candidata_id, compuerta, detalle, fecha):
    """Anota que una candidata frenó en una compuerta que solo destraba una persona. Devuelve en cuántas fechas
    distintas frenó seguido en esa misma compuerta."""
    with db:
        f = db.execute("SELECT compuerta, fechas FROM bloqueos WHERE candidata = ?", (candidata_id,)).fetchone()
        fechas = json.loads(f["fechas"]) if f and f["compuerta"] == compuerta else []
        if fecha not in fechas:
            fechas = (fechas + [fecha])[-10:]
        db.execute("INSERT OR REPLACE INTO bloqueos (candidata, compuerta, detalle, fechas) VALUES (?,?,?,?)",
                   (candidata_id, compuerta, detalle, json.dumps(fechas)))
    return len(fechas)


def desbloquear(db, candidata_id):
    with db:
        db.execute("DELETE FROM bloqueos WHERE candidata = ?", (candidata_id,))


def desde(db, clave, activa, ahora):
    """Desde cuándo sigue pasando algo (por ejemplo, un servidor que no se puede medir). Con `activa` falso se borra y
    devuelve None. Sirve para avisar solo lo que dura, no lo que se arregla solo."""
    with db:
        if not activa:
            db.execute("DELETE FROM marcas WHERE clave = ?", (clave,))
            return None
        db.execute("INSERT OR IGNORE INTO marcas (clave, desde) VALUES (?,?)", (clave, ahora))
        return db.execute("SELECT desde FROM marcas WHERE clave = ?", (clave,)).fetchone()["desde"]


def anotar_corrida(db, ahora, resumen):
    with db:
        db.execute("INSERT OR REPLACE INTO corridas (ts, resumen) VALUES (?, ?)", (ahora, json.dumps(resumen, sort_keys=True)))


def ultima_corrida(db):
    fila = db.execute("SELECT ts, resumen FROM corridas ORDER BY ts DESC LIMIT 1").fetchone()
    return (fila["ts"], json.loads(fila["resumen"])) if fila else (None, None)
