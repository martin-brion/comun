"""Checks for the optional report; never produces accounting entries."""
from decimal import Decimal, InvalidOperation
import re


class ExpenseContractError(ValueError):
    pass


def positive_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def number(value):
    if isinstance(value, bool):
        raise ExpenseContractError("Importe inválido en el vínculo de rendición.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ExpenseContractError("Importe inválido en el vínculo de rendición.") from exc
    if not result.is_finite():
        raise ExpenseContractError("Importe no finito en el vínculo de rendición.")
    return result


def analytic(value):
    if not isinstance(value, dict) or not value:
        raise ExpenseContractError("Falta analítica explícita para la rendición.")
    result = {}
    for key, percent in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*)*", key):
            raise ExpenseContractError("Referencia analítica inválida.")
        result[key] = number(percent)
        if not 0 < result[key] <= 100:
            raise ExpenseContractError("Porcentaje analítico inválido.")
    return result


def validate(expense, bill):
    """Snapshots contain scalar IDs and only product invoice lines, no journal items."""
    if (bill.get("contract") != 2 or bill.get("state") != "posted"
            or bill.get("move_type") != "in_invoice"):
        raise ExpenseContractError("La rendición requiere la factura canónica contabilizada de contrato 2.")
    if not positive_id(expense.get("source_id")) or expense["source_id"] != bill.get("id"):
        raise ExpenseContractError("El origen de rendición no coincide con la factura.")
    if expense.get("invoice_id") != bill["id"]:
        raise ExpenseContractError("La factura de la rendición no puede reemplazarse ni quitarse.")
    for field in ("company_id", "currency_id"):
        if not positive_id(expense.get(field)) or expense[field] != bill.get(field):
            raise ExpenseContractError("La compañía o moneda no coincide con la factura.")
    if not expense.get("date") or str(expense["date"]) != str(bill.get("date")):
        raise ExpenseContractError("La fecha de la rendición no coincide con el comprobante.")
    if expense.get("payment") not in {"own_account", "company_account"} or expense["payment"] != bill.get("payment"):
        raise ExpenseContractError("El pago de la rendición difiere del pago original del comprobante.")
    original = number(bill.get("original_total"))
    if original <= 0 or original != number(bill.get("total")) or original != number(expense.get("total")):
        raise ExpenseContractError("El total fiscal difiere del original o de la rendición; requiere revisión explícita.")
    distribution = analytic(bill.get("original_analytic"))
    if analytic(expense.get("analytic")) != distribution:
        raise ExpenseContractError("La analítica de la rendición difiere del comprobante original.")
    lines = bill.get("lines")
    if not isinstance(lines, list) or not lines or any(analytic(line) != distribution for line in lines):
        raise ExpenseContractError("La analítica fiscal cambió; revisá la divergencia antes de rendir.")
    marker = f"[{bill.get('lot')}/F{bill.get('photo', 0):02d}]" if positive_id(bill.get("photo")) else ""
    name = expense.get("name", "")
    if (not re.fullmatch(r"\[L[0-9]{6}/F[0-9]{2}\]", marker) or not isinstance(name, str)
            or re.findall(r"\[L[0-9]{6}/F[0-9]{2}\]", name) != [marker]):
        raise ExpenseContractError("La rendición requiere el marcador exacto del comprobante.")
    return True
