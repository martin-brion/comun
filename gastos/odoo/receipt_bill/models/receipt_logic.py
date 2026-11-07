"""Contrato puro del ingreso contable: no importa Odoo ni Recursos Humanos."""
import datetime
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

DATABASE_PARAMETER = "receipt_bill.database"
PAYMENTS = {"own_account", "company_account"}
ORIGIN_FIELDS = {
    "receipt_bill_lot", "receipt_bill_photo", "receipt_bill_payment",
    "receipt_bill_payload_hash", "receipt_bill_source_uid",
    "receipt_bill_original_total", "receipt_bill_original_analytic",
}
REVIEW_FIELDS = {
    "receipt_bill_direct_review_by_id", "receipt_bill_direct_review_at",
    "receipt_bill_direct_review_hash", "receipt_bill_contract",
}


class ReceiptContractError(ValueError):
    pass


def positive_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def decimal(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ReceiptContractError(f"{label}: importe inválido.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReceiptContractError(f"{label}: importe inválido.") from exc
    if not result.is_finite():
        raise ReceiptContractError(f"{label}: importe no finito.")
    return result


def database_allowed(actual, configured):
    return bool(isinstance(actual, str) and actual and actual == configured)


def origin(values):
    lot, photo = values.get("receipt_bill_lot"), values.get("receipt_bill_photo")
    if not isinstance(lot, str) or not re.fullmatch(r"L[0-9]{6}", lot):
        raise ReceiptContractError("El lote requiere el formato Lnnnnnn.")
    if not positive_id(photo) or photo > 30:
        raise ReceiptContractError("La foto debe estar entre 1 y 30.")
    if values.get("receipt_bill_payment") not in tuple(PAYMENTS):
        raise ReceiptContractError("El criterio de pago debe declararse explícitamente.")
    fingerprint = values.get("receipt_bill_payload_hash")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ReceiptContractError("La huella de origen requiere SHA256 hexadecimal minúscula.")
    return f"[{lot}/F{photo:02d}]"


def analytic(value):
    if not isinstance(value, dict) or not value:
        raise ReceiptContractError("Falta una distribución analítica explícita.")
    result = {}
    for key, percent in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*)*", key):
            raise ReceiptContractError("La distribución analítica requiere IDs de cuentas positivos.")
        if not 0 < decimal(percent, "Porcentaje analítico") <= 100:
            raise ReceiptContractError("El porcentaje analítico debe ser mayor a cero y no superar 100.")
        result[key] = float(decimal(percent, "Porcentaje analítico"))
    return result


def no_taxes(value):
    return value in (None, False, []) or value == [(6, 0, [])] or value == [[6, 0, []]]


def validate_create(values, uid):
    """Valida el ingreso explícito previo a defaults, devolviendo evidencia inmutable."""
    marker = origin(values)
    if REVIEW_FIELDS & values.keys():
        raise ReceiptContractError("La revisión sólo se registra con la acción contable explícita.")
    if (not positive_id(values.get("receipt_bill_source_uid", uid))
            or values.get("receipt_bill_source_uid", uid) != uid):
        raise ReceiptContractError("No se puede suplantar al usuario de origen.")
    if (values.get("move_type") != "in_invoice" or values.get("state", "draft") != "draft"
            or values.get("partner_id") or values.get("ref") or values.get("line_ids")
            or values.get("auto_post", "no") != "no"):
        raise ReceiptContractError("El ingreso crea sólo una factura de proveedor en borrador y sin identidad fiscal.")
    if not all(positive_id(values.get(key)) for key in ("company_id", "currency_id", "journal_id")):
        raise ReceiptContractError("Faltan compañía, moneda o diario explícitos.")
    date = values.get("invoice_date")
    try:
        datetime.date.fromisoformat(str(date))
    except (ValueError, TypeError) as exc:
        raise ReceiptContractError("Falta una fecha de comprobante válida.") from exc
    commands = values.get("invoice_line_ids")
    if (not isinstance(commands, (tuple, list)) or len(commands) != 1
            or not isinstance(commands[0], (tuple, list)) or len(commands[0]) != 3
            or commands[0][0] != 0 or commands[0][1] != 0 or not isinstance(commands[0][2], dict)):
        raise ReceiptContractError("El ingreso requiere una única línea nueva.")
    line = commands[0][2]
    if not positive_id(line.get("account_id")):
        raise ReceiptContractError("La cuenta contable debe declararse por ID.")
    if not isinstance(line.get("name"), str) or not line["name"].strip():
        raise ReceiptContractError("Falta la descripción del comprobante.")
    if (line.get("product_id") or line.get("display_type", "product") != "product"
            or not no_taxes(line.get("tax_ids")) or decimal(line.get("discount", 0), "Descuento") != 0
            or decimal(line.get("quantity"), "Cantidad") != 1):
        raise ReceiptContractError("La línea inicial requiere cantidad uno, sin producto, descuento ni impuestos.")
    total = decimal(line.get("price_unit"), "Total original")
    if total <= 0:
        raise ReceiptContractError("El total original debe ser positivo.")
    distribution = analytic(line.get("analytic_distribution"))
    if ("receipt_bill_original_total" in values
            and decimal(values["receipt_bill_original_total"], "Total de evidencia") != total):
        raise ReceiptContractError("El total de evidencia no coincide con la línea.")
    if "receipt_bill_original_analytic" in values and values["receipt_bill_original_analytic"] != distribution:
        raise ReceiptContractError("La analítica de evidencia no coincide con la línea.")
    return {"marker": marker, "total": float(total), "analytic": distribution}


def review_fingerprint(snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         default=str, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
