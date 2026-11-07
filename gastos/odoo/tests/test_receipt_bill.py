"""Contrato y guardas de los borradores de proveedor, sin Odoo ni red. Datos sintéticos."""
from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "hr_expense_receipt_bill"
LATAM = ROOT / "hr_expense_receipt_bill_latam"
spec = importlib.util.spec_from_file_location("receipt_bill_logic", ADDON / "models" / "receipt_logic.py")
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)

SHEET = "Rendición Persona Prueba 20260923 Obra de prueba (proyecto #7) [L000123] · Propio"
MODES = ["own_account"]
DATA = {
    "id": 100, "name": "Comercio de prueba [L000123/F01]", "company_id": 3,
    "employee_id": 10, "currency_id": 46, "date": date(2026, 9, 23), "account_id": 44,
    "quantity": 1, "tax_ids": [], "payment_mode": "own_account", "total_amount_currency": 123.45,
    "analytic_distribution": {"521": 100},
}


class FakeModel:
    """Sólo aísla las guardas; no simula ORM, impuestos ni reglas de registros."""
    def __init__(self):
        self.env = types.SimpleNamespace(context={})

    def __iter__(self):
        return iter([self])

    def filtered(self, predicate):
        match = bool(getattr(self, predicate, False)) if isinstance(predicate, str) else predicate(self)
        return self if match else []

    def with_context(self, *args, **context):
        self.posting_context = dict(*args, **context)
        return self

    def _post(self, soft=True):
        self.base_post_called = True
        return "base_post"

    def create(self, vals_list):
        return vals_list

    def write(self, vals):
        return True

    # Ganchos base para probar el módulo LATAM encima.
    def _receipt_bill_extra_values(self):
        return {}

    def _receipt_bill_review_snapshot(self):
        return {"partner_id": 15}

    def _receipt_bill_review_missing(self):
        return ""

    def _receipt_bill_sensitive_fields(self):
        return {"partner_id"}


def fake_odoo():
    fields = types.SimpleNamespace(**{name: lambda *args, **kwargs: None for name in (
        "Boolean", "Many2many", "Many2one", "Char", "Datetime")})
    odoo = types.SimpleNamespace(
        _=lambda text: text,
        api=types.SimpleNamespace(model=lambda func: func, model_create_multi=lambda func: func,
                                  depends=lambda *args: lambda func: func,
                                  constrains=lambda *args: lambda func: func),
        fields=fields, models=types.SimpleNamespace(Model=FakeModel))

    class GuardError(Exception):
        pass
    errors = types.SimpleNamespace(AccessError=GuardError, UserError=GuardError, ValidationError=GuardError)
    return odoo, errors, GuardError


def load_module(addon, filename, package_name):
    odoo, errors, guard_error = fake_odoo()
    package = types.ModuleType(package_name)
    package.__path__ = [str(addon / "models")]
    spec = importlib.util.spec_from_file_location(f"{package_name}.{filename[:-3]}", addon / "models" / filename)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"odoo": odoo, "odoo.exceptions": errors, package_name: package}):
        spec.loader.exec_module(module)
    return module, guard_error


guards, GuardError = load_module(ADDON, "receipt_bill.py", "receipt_bill_models_test")
latam, _ = load_module(LATAM, "receipt_bill_latam.py", "receipt_bill_latam_models_test")


class ModelGuardTests(unittest.TestCase):
    def bill(self, reviewed=False, review_hash=False, current_hash="current"):
        bill = guards.AccountMove()
        bill.receipt_bill_expense_id = 123
        bill.receipt_bill_reviewed_by_id = reviewed
        bill.receipt_bill_review_hash = review_hash
        bill._receipt_bill_review_fingerprint = lambda: current_hash
        bill.base_post_called = False
        return bill

    def test_post_blocks_unreviewed_bill_before_calling_core(self):
        bill = self.bill()
        with self.assertRaises(GuardError):
            bill._post()
        self.assertFalse(bill.base_post_called)

    def test_post_blocks_changed_review_hash_before_calling_core(self):
        bill = self.bill(reviewed=2, review_hash="previous")
        with self.assertRaises(GuardError):
            bill._post()
        self.assertFalse(bill.base_post_called)

    def test_post_delegates_only_after_review_hash_matches(self):
        bill = self.bill(reviewed=2, review_hash="current")
        self.assertEqual(bill._post(), "base_post")
        self.assertTrue(bill.base_post_called)
        self.assertTrue(bill.posting_context["receipt_bill_posting"])

    def test_create_and_write_cannot_forge_review_metadata(self):
        for key in guards.REVIEW_FIELDS:
            with self.assertRaises(GuardError):
                guards.AccountMove().create([{key: "forged"}])
            with self.assertRaises(GuardError):
                guards.AccountMove().write({key: False})

    def test_origin_cannot_be_set_erased_or_introduced(self):
        for key in guards.ORIGIN_FIELDS:
            with self.assertRaises(GuardError):
                guards.AccountMove().create([{key: False}])
            with self.assertRaises(GuardError):
                self.bill().write({key: False})
            with self.assertRaises(GuardError):
                guards.AccountMove().write({key: 123})

    def test_context_cannot_inject_origin_or_review_defaults(self):
        for key in guards.ORIGIN_FIELDS | guards.REVIEW_FIELDS:
            model = guards.AccountMove()
            model.env.context = {"default_" + key: False}
            with self.assertRaises(GuardError):
                model.create([{}])
            with self.assertRaises(GuardError):
                model._receipt_bill_create({})

    def test_attachment_origin_cannot_be_created_or_changed_publicly(self):
        model = guards.IrAttachment()
        for value in (False, 123):
            with self.assertRaises(GuardError):
                model.create([{"receipt_bill_source_attachment_id": value}])
            with self.assertRaises(GuardError):
                model.write({"receipt_bill_source_attachment_id": value})
        model.env.context = {"default_receipt_bill_source_attachment_id": False}
        with self.assertRaises(GuardError):
            model.create([{}])

    def test_private_methods_use_the_module_prefix(self):
        # Otro módulo con un puente parecido no puede pisar estos métodos por nombre (pasó en laboratorio).
        import re
        for path in (ADDON / "models" / "receipt_bill.py", LATAM / "models" / "receipt_bill_latam.py"):
            names = set(re.findall(r"def (_receipt\w*)", path.read_text(encoding="utf-8")))
            self.assertTrue(names)
            self.assertFalse([n for n in names if not n.startswith("_receipt_bill_")], path.name)

    def test_base_module_names_no_country_or_localization(self):
        source = (ADDON / "models" / "receipt_bill.py").read_text(encoding="utf-8")
        source += (ADDON / "models" / "receipt_logic.py").read_text(encoding="utf-8")
        for word in ("l10n_", "country_id", '"UY"', '"AR"', "Matrix"):
            self.assertNotIn(word, source)


class LatamTests(unittest.TestCase):
    def move(self, uses_documents, number="F10R/2026/10/0001", doc_type=7):
        move = latam.AccountMove()
        move.l10n_latam_use_documents = uses_documents
        move.l10n_latam_document_number = number
        move.l10n_latam_document_type_id = types.SimpleNamespace(id=doc_type, __bool__=lambda self: bool(doc_type))
        return move

    def test_pending_bill_starts_without_document_type(self):
        self.assertEqual(latam.AccountMove()._receipt_bill_extra_values(), {"l10n_latam_document_type_id": False})

    def test_document_fields_invalidate_the_review(self):
        self.assertTrue(latam.LATAM_FIELDS <= latam.AccountMove()._receipt_bill_sensitive_fields())

    def test_manual_sequence_is_not_a_fiscal_number(self):
        reviewed = self.move(False, number=False)._receipt_bill_review_snapshot()
        posted = self.move(False)._receipt_bill_review_snapshot()
        self.assertEqual(logic.review_fingerprint(reviewed), logic.review_fingerprint(posted))

    def test_fiscal_number_and_journal_mode_stay_protected(self):
        base = self.move(True, number="A123")._receipt_bill_review_snapshot()
        for other in (self.move(True, number="A124"), self.move(False, number="A123")):
            self.assertNotEqual(logic.review_fingerprint(base),
                                logic.review_fingerprint(other._receipt_bill_review_snapshot()))

    def test_review_requires_document_when_journal_uses_documents(self):
        self.assertTrue(self.move(True, number=False)._receipt_bill_review_missing())
        self.assertEqual(self.move(False, number=False)._receipt_bill_review_missing(), "")


class LogicTests(unittest.TestCase):
    def test_database_requires_exact_opt_in(self):
        self.assertTrue(logic.database_allowed("ensayo_1", "ensayo_1"))
        for opted in (None, "", "ensayo_2", "ENSAYO_1"):
            self.assertFalse(logic.database_allowed("ensayo_1", opted))
        self.assertFalse(logic.database_allowed("", ""))

    def test_marker_uses_full_name_and_photo_bounds(self):
        self.assertEqual(logic.expense_marker(DATA["name"]), "[L000123/F01]")
        for name in ("[L000123/F01]", "Comercio [L000123/F00]", "Comercio [L000123/F31]",
                     "Comercio [L123/F01]", "Comercio [L000123/F01] extra", "Comercio"):
            self.assertIsNone(logic.expense_marker(name))

    def test_sheet_needs_exactly_one_lot_and_tolerates_any_label(self):
        self.assertEqual(logic.sheet_lot(SHEET), "L000123")
        self.assertEqual(logic.sheet_lot("Report X [L000123] - own"), "L000123")
        for name in ("Sin lote", "Dos [L000123] [L000124]", "[L12345]"):
            self.assertIsNone(logic.sheet_lot(name))

    def test_other_lot_is_rejected(self):
        with self.assertRaises(logic.ReceiptContractError):
            logic.invoice_values(DATA, SHEET.replace("L000123", "L000124"), MODES, 5)

    def test_payment_mode_comes_from_the_sheet_expenses(self):
        logic.invoice_values({**DATA, "payment_mode": "company_account"}, SHEET, ["company_account"], 5)
        for modes in (["company_account"], ["own_account", "company_account"], [], ["otro"]):
            with self.assertRaises(logic.ReceiptContractError):
                logic.invoice_values(DATA, SHEET, modes, 5)

    def test_preclassified_taxes_and_quantity_are_not_silently_changed(self):
        for change in ({"tax_ids": [22]}, {"quantity": 2}):
            with self.assertRaises(logic.ReceiptContractError):
                logic.invoice_values({**DATA, **change}, SHEET, MODES, 5)

    def test_invalid_amounts_are_rejected(self):
        for amount in (0, -1, "NaN", "Infinity", "comercio", True, None):
            with self.assertRaises(logic.ReceiptContractError):
                logic.invoice_values({**DATA, "total_amount_currency": amount}, SHEET, MODES, 5)

    def test_missing_required_data_and_journal_stop_before_creating_invoice(self):
        for field in ("company_id", "employee_id", "currency_id", "date", "account_id"):
            with self.assertRaises(logic.ReceiptContractError):
                logic.invoice_values({**DATA, field: False}, SHEET, MODES, 5)
        with self.assertRaises(logic.ReceiptContractError):
            logic.invoice_values(DATA, SHEET, MODES, False)

    def test_pending_bill_has_no_fiscal_identity_or_inferred_tax(self):
        vals = logic.invoice_values({**DATA, "vendor_id": 900, "referencia": "A1234567"}, SHEET, MODES, 5)
        self.assertFalse(vals["partner_id"])
        self.assertFalse(vals["ref"])
        line = vals["invoice_line_ids"][0][2]
        self.assertNotIn("product_id", line)
        self.assertEqual(line["tax_ids"], [(5, 0, 0)])
        self.assertFalse([key for key in vals if key.startswith("l10n_")])

    def test_original_currency_date_total_and_analytic_are_preserved(self):
        vals = logic.invoice_values(DATA, SHEET, MODES, 5)
        self.assertEqual(vals["currency_id"], DATA["currency_id"])
        self.assertEqual(vals["invoice_date"], DATA["date"])
        self.assertEqual(vals["invoice_line_ids"][0][2]["price_unit"], 123.45)
        self.assertEqual(vals["invoice_line_ids"][0][2]["analytic_distribution"], {"521": 100})
        self.assertEqual(vals["receipt_bill_expense_id"], DATA["id"])
        self.assertEqual(vals["receipt_bill_marker"], "[L000123/F01]")

    def test_preparation_does_not_mutate_the_expense_payload(self):
        original = deepcopy(DATA)
        vals = logic.invoice_values(DATA, SHEET, MODES, 5)
        vals["invoice_line_ids"][0][2]["analytic_distribution"]["521"] = 0
        self.assertEqual(DATA, original)

    def test_payload_fingerprint_is_stable_and_detects_changes(self):
        self.assertEqual(logic.receipt_fingerprint(DATA),
                         logic.receipt_fingerprint({**DATA, "total_amount_currency": "123.450"}))
        original = logic.receipt_fingerprint(DATA)
        for change in ({"total_amount_currency": 123.46}, {"date": date(2026, 9, 24)},
                       {"currency_id": 2}, {"analytic_distribution": {"522": 100}}, {"employee_id": 11}):
            self.assertNotEqual(original, logic.receipt_fingerprint({**DATA, **change}))

    def test_views_are_well_formed(self):
        ET.parse(ADDON / "views" / "receipt_bill_views.xml")


if __name__ == "__main__":
    unittest.main()
