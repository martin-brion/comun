"""Vulnerabilidades de una imagen, con un escáner externo (Trivy) si la instancia lo declara. Sin escáner, con un escaneo
cortado o con una salida ilegible el resultado es «desconocido»: nunca «cero hallazgos»."""
import json
import subprocess
import time

ALTAS = ("HIGH", "CRITICAL")


def _correr(argv, tope):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=tope)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, b""


def resumir(informe, cves_kev):
    """De la salida JSON del escáner a cuentas: total, altas o críticas, con arreglo publicado, y las que figuran en KEV."""
    if not isinstance(informe, dict) or not isinstance(informe.get("Results", []), list):
        raise ValueError("informe con otra forma")
    vistos = {}
    for r in informe.get("Results") or []:
        for v in (r.get("Vulnerabilities") or []) if isinstance(r, dict) else []:
            if isinstance(v, dict) and isinstance(v.get("VulnerabilityID"), str):
                previo = vistos.get(v["VulnerabilityID"], {})
                vistos[v["VulnerabilityID"]] = {
                    "alta": previo.get("alta", False) or v.get("Severity") in ALTAS,
                    "arreglo": previo.get("arreglo", False) or bool(v.get("FixedVersion"))}
    kev = sorted(i for i in vistos if i in cves_kev)
    return {"estado": "ok", "motivo": "", "total": len(vistos), "altas": sum(1 for v in vistos.values() if v["alta"]),
            "altas_con_arreglo": sum(1 for v in vistos.values() if v["alta"] and v["arreglo"]), "kev": kev[:50],
            "kev_aplicable": False}


def escanear(comando, imagenes, cves_kev, tope_s, correr=_correr, reloj=time.monotonic):
    """{imagen_id: resumen}. `comando` es la lista de la configuración (por ejemplo, el escáner con su salida en JSON); se
    le agrega la imagen al final. El tope es de toda la pasada: lo que no entra queda «desconocido» y no se recupera."""
    salida = {}
    if not comando:
        return {i: {"estado": "desconocido", "motivo": "sin escáner configurado"} for i in imagenes}
    limite = reloj() + tope_s
    for imagen in imagenes:
        resto = limite - reloj()
        if resto <= 0:
            salida[imagen] = {"estado": "desconocido", "motivo": "se acabó el tiempo de la pasada"}
            continue
        rc, crudo = correr(list(comando) + [imagen], resto)
        if rc != 0:
            salida[imagen] = {"estado": "desconocido", "motivo": "el escáner no terminó"}
            continue
        try:
            salida[imagen] = resumir(json.loads(crudo.decode("utf-8")), cves_kev)
        except (ValueError, UnicodeDecodeError, AttributeError):
            salida[imagen] = {"estado": "desconocido", "motivo": "salida del escáner ilegible"}
    return salida
