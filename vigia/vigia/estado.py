"""El estado que sobrevive entre corridas: las candidatas, con la fecha en que se detectó cada una. El plazo se cuenta desde
ahí y no se reinicia porque el servicio o el servidor se reinicien.

Estados de esta versión: detectada → cerrada (la pieza ya corre esa versión o una posterior) o descartada (la reemplazó
otra candidata, o la pieza dejó de correr). Los que siguen (ensayada, programada, aplicando, verificada) llegan con el
ejecutor. Una pieza que esta corrida no pudo medir no cierra ni descarta nada.
"""
import json
import os
import sqlite3

ABIERTOS = ("detectada",)
_ESQUEMA = """
CREATE TABLE IF NOT EXISTS candidatas (
  id TEXT PRIMARY KEY, servidor TEXT NOT NULL, pieza TEXT NOT NULL, actual TEXT NOT NULL, propuesta TEXT NOT NULL,
  salto TEXT NOT NULL, estado TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT '', detectada INTEGER NOT NULL,
  actualizada INTEGER NOT NULL, datos TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS corridas (ts INTEGER PRIMARY KEY, resumen TEXT NOT NULL);
"""


def abrir(directorio):
    os.makedirs(directorio, exist_ok=True)
    db = sqlite3.connect(os.path.join(directorio, "vigia.sqlite3"), timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(_ESQUEMA)
    return db


def registrar(db, servidor, piezas, ahora):
    """Cruza las piezas medidas en un servidor con lo guardado. Devuelve {"nuevas", "cerradas", "descartadas"} (ids).
    `piezas` es None si el servidor no se pudo medir: en ese caso no cambia nada."""
    cambios = {"nuevas": [], "cerradas": [], "descartadas": []}
    if piezas is None:
        return cambios
    with db:
        medidas = {p["pieza"]: p for p in piezas if p["estado"] != "desconocido"}
        vigentes = {p["candidata"]["id"]: p["candidata"] for p in piezas if p.get("candidata")}
        for c in vigentes.values():
            fila = db.execute("SELECT estado FROM candidatas WHERE id = ?", (c["id"],)).fetchone()
            if fila is None:
                db.execute("INSERT INTO candidatas (id, servidor, pieza, actual, propuesta, salto, estado, detectada, "
                           "actualizada, datos) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (c["id"], servidor, c["pieza"], c["actual"], c["propuesta"], c["salto"], "detectada", ahora,
                            ahora, json.dumps(c, sort_keys=True)))
                cambios["nuevas"].append(c["id"])
            elif fila["estado"] in ABIERTOS:
                db.execute("UPDATE candidatas SET actual = ?, actualizada = ?, datos = ? WHERE id = ?",
                           (c["actual"], ahora, json.dumps(c, sort_keys=True), c["id"]))
            else:
                # Una candidata que se había cerrado o descartado vuelve a aparecer (por ejemplo, una vuelta atrás).
                db.execute("UPDATE candidatas SET estado = 'detectada', motivo = '', detectada = ?, actualizada = ?, "
                           "actual = ?, datos = ? WHERE id = ?", (ahora, ahora, c["actual"], json.dumps(c, sort_keys=True), c["id"]))
                cambios["nuevas"].append(c["id"])
        abiertas = db.execute("SELECT id, pieza, propuesta FROM candidatas WHERE servidor = ? AND estado = 'detectada'",
                              (servidor,)).fetchall()
        corriendo = {p["pieza"] for p in piezas}
        for fila in abiertas:
            if fila["id"] in vigentes:
                continue
            medida = medidas.get(fila["pieza"])
            if medida is None:
                if fila["pieza"] in corriendo:
                    continue                                   # corre, pero hoy no se pudo medir: queda como está
                estado, motivo = "descartada", "la pieza ya no corre en el servidor"
            elif medida.get("candidata"):
                estado, motivo = "descartada", f"la reemplazó {medida['candidata']['propuesta']}"
            elif medida["actual"] == fila["propuesta"] or medida["estado"] == "al día":
                estado, motivo = "cerrada", f"la pieza corre {medida['actual']}"
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
            db.execute("SELECT * FROM candidatas WHERE estado = 'detectada' ORDER BY servidor, pieza")]


def anotar_corrida(db, ahora, resumen):
    with db:
        db.execute("INSERT OR REPLACE INTO corridas (ts, resumen) VALUES (?, ?)", (ahora, json.dumps(resumen, sort_keys=True)))


def ultima_corrida(db):
    fila = db.execute("SELECT ts, resumen FROM corridas ORDER BY ts DESC LIMIT 1").fetchone()
    return (fila["ts"], json.loads(fila["resumen"])) if fila else (None, None)
