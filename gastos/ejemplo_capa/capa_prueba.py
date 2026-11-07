"""Capa sintética: contratos estructurales sin importar ni modificar el consumidor."""
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from bot import config, plugins


def datos():
    valor = config.config_funcion("capa_prueba")
    if not isinstance(valor, dict):
        raise ValueError("Configurar capa_prueba antes de usar los proveedores")
    return valor


async def cuenta(marco):
    ident = datos()["cuentas"][marco.empresa][marco.destino]
    if type(ident) is not int or ident <= 0:
        raise ValueError("Cuenta analítica sin referencia nominal")
    filas = await marco.odoo("account.analytic.account", "read", [ident], fields=["name", "company_id", "active"])
    if len(filas) != 1 or not filas[0].get("active"):
        raise ValueError("Cuenta analítica inexistente o inactiva")
    compania = filas[0].get("company_id")
    if compania is not False and (not isinstance(compania, (list, tuple)) or compania[0] != marco.company_id):
        raise ValueError("Cuenta analítica de otra compañía")
    return ident, filas[0]["name"]


async def proponer(ctx, marco, lineas):
    ident, nombre = await cuenta(marco)
    return {"lineas": {x["n"]: {"distribucion": {ident: 100}, "grupo": "cuenta",
            "rotulo": nombre, "detalle": "Cuenta configurada por la empresa.", "texto": nombre,
            "estado": {"cuenta": ident}} for x in lineas}}


async def revalidar_imputacion(ctx, marco, lineas):
    ident, _ = await cuenta(marco)
    if any(x["imputacion"]["estado"]["cuenta"] != ident for x in lineas):
        return "Cambió la cuenta analítica configurada"
    return None


async def evaluar(ctx, marco, lineas):
    regla = datos()["topes"][marco.empresa]
    limite = Decimal(regla["importe"])
    if not limite.is_finite() or limite <= 0 or type(regla["bloquea"]) is not bool:
        raise ValueError("Tope de prueba inválido")
    excede = any(x["moneda"] == regla["moneda"] and Decimal(x["importe"]) > limite for x in lineas)
    observaciones = ["Hay comprobantes por encima del tope configurado."] if excede else []
    return {"observaciones": observaciones,
            "bloqueos": ["La política exige revisar el tope antes de cargar."] if excede and regla["bloquea"] else [],
            "estado": {"regla": hashlib.sha256(json.dumps(regla, sort_keys=True).encode()).hexdigest()}}


async def revalidar_politica(ctx, marco, lineas, resultado):
    if await evaluar(ctx, marco, lineas) != resultado:
        return "Cambió la política o su resultado"
    return None


PLUGIN = plugins.Plugin(nombre="capa_prueba", grupo="capa_prueba", funcion="capa_prueba", extensiones=tuple(
    extension for empresa in ("taller", "oficina") for extension in (
        plugins.Extension("gastos.imputador", 1, "imputador_" + empresa,
            SimpleNamespace(proponer=proponer, revalidar=revalidar_imputacion, nota="Se usa la cuenta configurada para empresa y destino.")),
        plugins.Extension("gastos.politica", 1, "politica_" + empresa,
            SimpleNamespace(evaluar=evaluar, revalidar=revalidar_politica)))))
