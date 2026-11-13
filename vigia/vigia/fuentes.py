"""De dónde se sabe que hay una versión nueva: el índice público de Docker Hub, un registro OCI cualquiera y la lista de
vulnerabilidades explotadas (KEV). Solo lectura, solo https, sin credenciales: lo único que sale es el nombre público
de una imagen. Una fuente que falla devuelve «desconocido», nunca una lista vacía.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

HUB = "https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
MAX_PAGINAS = 5
_MANIFIESTOS = ", ".join((
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))
_REPO = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}$")
_HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,199}(:\d{1,5})?$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class FuenteError(Exception):
    pass


class _SinCredencialAjena(urllib.request.HTTPRedirectHandler):
    """Al redirigir a otro servidor (un almacén de blobs), la autorización del registro no viaja."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nuevo = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nuevo is not None:
            if not newurl.startswith("https://"):
                raise FuenteError("redirección fuera de https")
            if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
                nuevo.headers.pop("Authorization", None)
                nuevo.unredirected_hdrs.pop("Authorization", None)
        return nuevo


def pedir(url, cabeceras=None, tope=20, maximo=8 << 20):
    """(estado, cabeceras en minúscula, cuerpo). Lanza FuenteError si no hubo respuesta."""
    if not url.startswith("https://"):
        raise FuenteError("solo https")
    req = urllib.request.Request(url, headers={"User-Agent": "vigia", "Accept": "application/json", **(cabeceras or {})})
    try:
        with urllib.request.build_opener(_SinCredencialAjena).open(req, timeout=tope) as r:
            cuerpo = r.read(maximo + 1)
            if len(cuerpo) > maximo:
                raise FuenteError("respuesta demasiado grande")
            return r.status, {k.lower(): v for k, v in r.headers.items()}, cuerpo
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(65536)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise FuenteError(type(e).__name__) from None


def _desconocido(motivo):
    return {"estado": "desconocido", "motivo": motivo, "etiquetas": [], "hay_mas": False}


def _json(cuerpo):
    try:
        return json.loads(cuerpo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FuenteError("respuesta ilegible") from None


def docker_hub(repo, pedir=pedir):
    """Hasta 100 etiquetas, las más recientes: nombre, fecha de publicación y huellas (la del índice y las de cada
    plataforma). `hay_mas` dice que el repositorio tiene más etiquetas que las consultadas."""
    if not _REPO.match(repo):
        return _desconocido("repositorio inválido")
    try:
        estado, _, cuerpo = pedir(HUB.format(repo=repo))
        if estado != 200:
            return _desconocido(f"el índice respondió {estado}")
        datos = _json(cuerpo)
        etiquetas = []
        for t in datos["results"]:
            if not isinstance(t.get("name"), str) or not _TAG.match(t["name"]):
                continue
            huellas = {t.get("digest")} | {i.get("digest") for i in t.get("images") or [] if isinstance(i, dict)}
            etiquetas.append({"nombre": t["name"], "publicada": str(t.get("last_updated") or "")[:20] or None,
                              "indice": t["digest"] if _DIGEST.match(str(t.get("digest"))) else None,
                              "digests": sorted(h for h in huellas if isinstance(h, str) and _DIGEST.match(h))})
        return {"estado": "ok", "motivo": "", "etiquetas": etiquetas, "hay_mas": bool(datos.get("next"))}
    except FuenteError as e:
        return _desconocido(str(e))
    except (KeyError, TypeError, AttributeError):
        return _desconocido("respuesta con otra forma")


def etiqueta_hub(repo, tag, pedir=pedir):
    """Fecha y huellas de una etiqueta puntual del índice público: {"publicada", "digests"} o None si no se pudo."""
    if not _REPO.match(repo) or not _TAG.match(tag):
        return None
    try:
        estado, _, cuerpo = pedir(f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}")
        if estado != 200:
            return None
        t = _json(cuerpo)
        huellas = {t.get("digest")} | {i.get("digest") for i in t.get("images") or [] if isinstance(i, dict)}
        return {"publicada": str(t.get("last_updated") or "")[:20] or None,
                "indice": t["digest"] if _DIGEST.match(str(t.get("digest"))) else None,
                "digests": sorted(h for h in huellas if isinstance(h, str) and _DIGEST.match(h))}
    except (FuenteError, TypeError, AttributeError):
        return None


def _con_token(url, repo, pedir, cabeceras=None):
    """GET a un registro OCI; si pide credencial, saca un token anónimo de lectura para ese repositorio y repite."""
    estado, cab, cuerpo = pedir(url, cabeceras)
    if estado != 401:
        return estado, cab, cuerpo, None
    reto = dict(re.findall(r'(\w+)="([^"]*)"', cab.get("www-authenticate", "")))
    if not reto.get("realm", "").startswith("https://"):
        raise FuenteError("el registro pide una credencial que no se puede obtener")
    consulta = {"scope": f"repository:{repo}:pull"}
    if reto.get("service"):
        consulta["service"] = reto["service"]
    e2, _, c2 = pedir(reto["realm"] + "?" + urllib.parse.urlencode(consulta))
    if e2 != 200:
        raise FuenteError("el registro no dio acceso anónimo")
    datos = _json(c2)
    token = datos.get("token") or datos.get("access_token")
    if not isinstance(token, str) or not token:
        raise FuenteError("el registro no dio acceso anónimo")
    auth = {"Authorization": "Bearer " + token}
    estado, cab, cuerpo = pedir(url, {**(cabeceras or {}), **auth})
    return estado, cab, cuerpo, auth


def registro_oci(registro, repo, pedir=pedir):
    """Las etiquetas de un repositorio en un registro OCI. No trae fechas ni huellas: se piden aparte, de la candidata."""
    if not _HOST.match(registro) or not _REPO.match(repo):
        return _desconocido("referencia inválida")
    try:
        url, nombres, paginas = f"https://{registro}/v2/{repo}/tags/list?n=1000", [], 0
        while url and paginas < MAX_PAGINAS:
            estado, cab, cuerpo, _ = _con_token(url, repo, pedir)
            if estado != 200:
                return _desconocido(f"el registro respondió {estado}")
            nombres += [t for t in (_json(cuerpo).get("tags") or []) if isinstance(t, str) and _TAG.match(t)]
            paginas += 1
            m = re.search(r'<([^>]+)>;\s*rel="next"', cab.get("link", ""))
            url = urllib.parse.urljoin(f"https://{registro}/", m.group(1)) if m else None
        return {"estado": "ok", "motivo": "", "hay_mas": bool(url),
                "etiquetas": [{"nombre": n, "publicada": None, "indice": None, "digests": []} for n in nombres]}
    except FuenteError as e:
        return _desconocido(str(e))
    except (TypeError, AttributeError):
        return _desconocido("respuesta con otra forma")


def detalle_oci(registro, repo, tag, pedir=pedir):
    """Fecha de creación y huella de una etiqueta de un registro OCI: {"publicada", "digests"} o None si no se pudo."""
    if not _HOST.match(registro) or not _REPO.match(repo) or not _TAG.match(tag):
        return None
    try:
        base = f"https://{registro}/v2/{repo}"
        estado, cab, cuerpo, auth = _con_token(f"{base}/manifests/{tag}", repo, pedir, {"Accept": _MANIFIESTOS})
        if estado != 200:
            return None
        indice = cab.get("docker-content-digest")
        huellas = {indice}
        manifiesto = _json(cuerpo)
        if manifiesto.get("manifests"):
            elegido = next((m for m in manifiesto["manifests"]
                            if (m.get("platform") or {}).get("os") == "linux"
                            and (m.get("platform") or {}).get("architecture") == "amd64"), None)
            if elegido is None or not _DIGEST.match(str(elegido.get("digest"))):
                return None
            huellas.add(elegido["digest"])
            estado, _, cuerpo = pedir(f"{base}/manifests/{elegido['digest']}", {"Accept": _MANIFIESTOS, **(auth or {})})
            if estado != 200:
                return None
            manifiesto = _json(cuerpo)
        config = str((manifiesto.get("config") or {}).get("digest"))
        if not _DIGEST.match(config):
            return None
        estado, _, cuerpo = pedir(f"{base}/blobs/{config}", auth)
        if estado != 200:
            return None
        creada = _json(cuerpo).get("created")
        return {"publicada": creada[:20] if isinstance(creada, str) else None,
                "indice": indice if isinstance(indice, str) and _DIGEST.match(indice) else None,
                "digests": sorted(h for h in huellas if isinstance(h, str) and _DIGEST.match(h))}
    except (FuenteError, TypeError, AttributeError):
        return None


def kev(pedir=pedir):
    """Los CVE con explotación conocida. Que un paquete figure no prueba que el caso sea explotable acá."""
    try:
        estado, _, cuerpo = pedir(KEV)
        if estado != 200:
            return {"estado": "desconocido", "motivo": f"la lista respondió {estado}", "cves": set()}
        cves = {v["cveID"] for v in _json(cuerpo)["vulnerabilities"] if isinstance(v.get("cveID"), str)}
        if not cves:
            return {"estado": "desconocido", "motivo": "lista vacía", "cves": set()}
        return {"estado": "ok", "motivo": "", "cves": cves}
    except FuenteError as e:
        return {"estado": "desconocido", "motivo": str(e), "cves": set()}
    except (KeyError, TypeError, AttributeError):
        return {"estado": "desconocido", "motivo": "respuesta con otra forma", "cves": set()}
