"""Contrato con la carga de gastos por foto. No interpreta proveedor, número fiscal ni impuestos.

La carga nombra cada gasto con el marcador de su foto principal (`<texto> [Lnnnnnn/Fnn]`) y cada hoja con el
marcador de su lote (`[Lnnnnnn]`, una sola vez). Es lo único que este módulo lee del nombre: el criterio de pago
sale de los gastos de la hoja, no del texto.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

DATABASE_PARAMETER = "hr_expense_receipt_bill.database"
_EXPENSE = re.compile(r".+ \[(L[0-9]{6})/F([0-9]{2})\]\Z", re.DOTALL)
_SHEET = re.compile(r"\[(L[0-9]{6})\]")
PAYMENT_MODES = {"company_account", "own_account"}
MAX_PHOTOS = 30


class ReceiptContractError(ValueError):
    pass


def database_allowed(current, opted_in):
    """La base se habilita por su nombre exacto: una copia restaurada con otro nombre queda apagada."""
    return bool(current and current == opted_in)


def expense_marker(name):
    match = _EXPENSE.fullmatch(name or "")
    if not match or not 1 <= int(match[2]) <= MAX_PHOTOS:
        return None
    return f"[{match[1]}/F{match[2]}]"


def sheet_lot(name):
    lots = _SHEET.findall(name or "")
    return lots[0] if len(lots) == 1 else None


def validate_receipt(data, sheet_name, sheet_payment_modes):
    """Devuelve el marcador después de validar sólo datos ya confirmados por la carga."""
    marker = expense_marker(data.get("name"))
    lot = sheet_lot(sheet_name)
    if not marker or not lot or not marker.startswith(f"[{lot}/"):
        raise ReceiptContractError("El gasto y la hoja no pertenecen al mismo lote.")
    modes = set(sheet_payment_modes or ())
    if len(modes) != 1 or data.get("payment_mode") not in modes or not modes <= PAYMENT_MODES:
        raise ReceiptContractError("La hoja mezcla criterios de pago o el del gasto no coincide.")
    if data.get("quantity") != 1 or data.get("tax_ids"):
        raise ReceiptContractError("La carga requiere cantidad uno e impuestos aún sin clasificar.")
    amount = _amount(data.get("total_amount_currency"))
    if not amount.is_finite() or amount <= 0:
        raise ReceiptContractError("El importe del gasto debe ser positivo y finito.")
    if not all(data.get(field) for field in ("company_id", "employee_id", "currency_id", "date", "account_id")):
        raise ReceiptContractError("Falta compañía, empleado, moneda, fecha o cuenta del gasto.")
    return marker


def _amount(value):
    if isinstance(value, bool) or value is None:
        raise ReceiptContractError("El importe del gasto no es válido.")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReceiptContractError("El importe del gasto no es válido.") from exc


def invoice_values(data, sheet_name, sheet_payment_modes, journal_id):
    marker = validate_receipt(data, sheet_name, sheet_payment_modes)
    if not journal_id:
        raise ReceiptContractError("Falta el diario de compras explícito de la compañía.")
    return {
        "move_type": "in_invoice",
        "company_id": data["company_id"],
        "journal_id": journal_id,
        "currency_id": data["currency_id"],
        "invoice_date": data["date"],
        # Ni el empleado, ni el comercio, ni el texto leído de la foto son identidad o número fiscal.
        "partner_id": False,
        "ref": False,
        "invoice_origin": f"Gasto {marker}",
        "receipt_bill_expense_id": data["id"],
        "receipt_bill_marker": marker,
        "receipt_bill_payload_hash": receipt_fingerprint(data),
        "invoice_line_ids": [(0, 0, {
            "name": data["name"],
            "account_id": data["account_id"],
            "quantity": 1,
            "price_unit": float(_amount(data["total_amount_currency"])),
            "analytic_distribution": dict(data.get("analytic_distribution") or {}),
            # Sin product_id: que el producto no traiga impuestos por defecto a la factura.
            "tax_ids": [(5, 0, 0)],
        })],
    }


def receipt_fingerprint(data):
    """Detecta cambios del gasto entre un intento y el siguiente."""
    snapshot = {key: data.get(key) for key in (
        "id", "name", "company_id", "employee_id", "currency_id", "account_id", "payment_mode",
        "analytic_distribution")}
    snapshot["date"] = str(data.get("date") or "")
    snapshot["amount"] = str(_amount(data["total_amount_currency"]).normalize())
    return _digest(snapshot)


def review_fingerprint(snapshot):
    return _digest(snapshot)


def _digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
