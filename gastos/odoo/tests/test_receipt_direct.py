"""Pruebas puras y guardas previas al ORM; no acreditan una instalación de Odoo."""
import ast
import copy
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1] / "receipt_bill"
spec = importlib.util.spec_from_file_location("direct_receipt_logic", ROOT / "models" / "receipt_logic.py")
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


def values():
    return {
        "company_id": 3, "journal_id": 11, "currency_id": 2, "invoice_date": "2026-10-22",
        "move_type": "in_invoice", "receipt_bill_lot": "L000123", "receipt_bill_photo": 1,
        "receipt_bill_payment": "own_account", "receipt_bill_payload_hash": "a" * 64,
        "invoice_line_ids": [(0, 0, {"name": "Traslado [L000123/F01]", "account_id": 41,
                                   "quantity": 1, "price_unit": 100, "tax_ids": [(6, 0, [])],
                                   "analytic_distribution": {"81": 100}})],
    }


class ContractTests(unittest.TestCase):
    def reject(self, mutate):
        item = values()
        mutate(item)
        with self.assertRaises(logic.ReceiptContractError):
            logic.validate_create(item, 17)

    def test_valid_receipt_copies_initial_evidence(self):
        item = values()
        result = logic.validate_create(item, 17)
        self.assertEqual(result, {"marker": "[L000123/F01]", "total": 100.0, "analytic": {"81": 100}})
        item["invoice_line_ids"][0][2]["analytic_distribution"]["81"] = 50
        self.assertEqual(result["analytic"], {"81": 100})

    def test_both_payment_modes(self):
        for mode in logic.PAYMENTS:
            item = values()
            item["receipt_bill_payment"] = mode
            logic.validate_create(item, 17)

    def test_database_exact_match_only(self):
        self.assertTrue(logic.database_allowed("casa_prueba", "casa_prueba"))
        for actual, configured in [("", ""), ("casa_prueba", "otra"), ("casa_prueba", None)]:
            self.assertFalse(logic.database_allowed(actual, configured))

    def test_lot_format(self):
        for lot in [None, "L123", "[L000123]", "L000123\n", "l000123", "L１２３４５６"]:
            self.reject(lambda item: item.update(receipt_bill_lot=lot))

    def test_photo_range(self):
        for photo in [0, -1, 31, True, "1", 1.0]:
            self.reject(lambda item: item.update(receipt_bill_photo=photo))
        for photo in [1, 30]:
            item = values()
            item["receipt_bill_photo"] = photo
            logic.validate_create(item, 17)

    def test_payment_cannot_come_from_label(self):
        for mode in [None, "employee", "Factura", ""]:
            self.reject(lambda item: item.update(receipt_bill_payment=mode))

    def test_hash_is_sha256(self):
        for fingerprint in ["", "A" * 64, "a" * 63, "z" * 64, None]:
            self.reject(lambda item: item.update(receipt_bill_payload_hash=fingerprint))

    def test_source_cannot_be_spoofed(self):
        for uid in [18, False, True, "17", 17.0]:
            self.reject(lambda item: item.update(receipt_bill_source_uid=uid))

    def test_explicit_nominal_source_accepted(self):
        item = values()
        item["receipt_bill_source_uid"] = 17
        logic.validate_create(item, 17)

    def test_review_fields_never_accepted(self):
        for field in logic.REVIEW_FIELDS:
            self.reject(lambda item: item.update({field: False}))

    def test_only_vendor_draft(self):
        for key, value in [("state", "posted"), ("move_type", "entry"), ("auto_post", "at_date")]:
            self.reject(lambda item: item.update({key: value}))

    def test_no_fiscal_identity_in_initial_payload(self):
        for key, value in [("partner_id", 71), ("ref", "0001-123"), ("line_ids", [(0, 0, {})])]:
            self.reject(lambda item: item.update({key: value}))

    def test_explicit_company_currency_journal(self):
        for key in ["company_id", "currency_id", "journal_id"]:
            for value in [None, 0, True, "2"]:
                self.reject(lambda item: item.update({key: value}))

    def test_valid_iso_date(self):
        for date in [None, "", "2026-02-30", "22/10/2026", "2026-10-22T12:00:00"]:
            self.reject(lambda item: item.update(invoice_date=date))

    def test_one_new_invoice_line(self):
        for commands in [[], [(4, 1, 0)], [(0, 0, {}), (0, 0, {})], [(1, 1, {})], "bad"]:
            self.reject(lambda item: item.update(invoice_line_ids=commands))

    def test_account_id_explicit(self):
        for value in [0, None, True, "41"]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(account_id=value))

    def test_description_required(self):
        for value in [None, "", "   ", 1]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(name=value))

    def test_no_product_defaults(self):
        self.reject(lambda item: item["invoice_line_ids"][0][2].update(product_id=501))

    def test_no_initial_taxes(self):
        for taxes in [[41], [(6, 0, [41])], [(4, 41, 0)]]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(tax_ids=taxes))

    def test_no_initial_discount(self):
        self.reject(lambda item: item["invoice_line_ids"][0][2].update(discount=10))

    def test_unit_quantity(self):
        for quantity in [None, True, 0, 2, float("nan")]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(quantity=quantity))

    def test_positive_finite_total(self):
        for total in [0, -1, True, float("nan"), float("inf"), "bad"]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(price_unit=total))

    def test_analytic_required(self):
        for distribution in [None, {}, [], False]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(analytic_distribution=distribution))

    def test_analytic_ids_positive(self):
        for key in ["0", "-1", "name", 41, "1,0", "1,", " 41"]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(analytic_distribution={key: 100}))

    def test_analytic_percentage_finite_positive(self):
        for percent in [0, -1, 101, True, float("nan"), float("inf")]:
            self.reject(lambda item: item["invoice_line_ids"][0][2].update(analytic_distribution={"41": percent}))

    def test_multiple_analytic_plans_do_not_use_global_sum(self):
        item = values()
        item["invoice_line_ids"][0][2]["analytic_distribution"] = {"41": 100, "42": 100}
        self.assertEqual(logic.validate_create(item, 17)["analytic"], {"41": 100, "42": 100})

    def test_combined_analytic_key(self):
        self.assertEqual(logic.analytic({"41,42": 100}), {"41,42": 100})

    def test_original_total_cannot_be_spoofed(self):
        self.reject(lambda item: item.update(receipt_bill_original_total=200))

    def test_original_analytic_cannot_be_spoofed(self):
        self.reject(lambda item: item.update(receipt_bill_original_analytic={"42": 100}))

    def test_evidence_matching_input_accepted(self):
        item = values()
        item.update(receipt_bill_original_total=100, receipt_bill_original_analytic={"81": 100})
        logic.validate_create(item, 17)

    def test_review_fingerprint_stable_key_order(self):
        self.assertEqual(logic.review_fingerprint({"b": 2, "a": 1}), logic.review_fingerprint({"a": 1, "b": 2}))

    def test_fingerprint_changes_for_each_evidence_component(self):
        snapshot = {"origin": {"lot": "L000123", "photo": 1, "source_uid": 17},
                    "total": 100, "analytic": {"41": 100}, "receipts": [{"checksum": "aaa"}]}
        original = logic.review_fingerprint(snapshot)
        for key in snapshot:
            modified = copy.deepcopy(snapshot)
            modified[key] = "changed"
            self.assertNotEqual(original, logic.review_fingerprint(modified))

    def test_fingerprint_refuses_nonfinite_data(self):
        with self.assertRaises(ValueError):
            logic.review_fingerprint({"total": float("nan")})


class AccessError(Exception):
    pass


def orm_module():
    """Carga clases reales con superficies mínimas; las pruebas no simulan una base."""
    odoo = types.ModuleType("odoo")
    odoo._ = lambda value: value
    odoo.api = types.SimpleNamespace(model=lambda func: func, model_create_multi=lambda func: func,
                                     constrains=lambda *args: lambda func: func,
                                     depends=lambda *args: lambda func: func)
    class Fields:
        def __getattr__(self, name):
            return lambda *args, **kw: None
    odoo.fields = Fields()
    odoo.models = types.SimpleNamespace(Model=object)
    exceptions = types.ModuleType("odoo.exceptions")
    exceptions.AccessError, exceptions.UserError, exceptions.ValidationError = AccessError, ValueError, ValueError
    package = types.ModuleType("direct_receipt_models")
    package.__path__ = [str(ROOT / "models")]
    modules = {"odoo": odoo, "odoo.exceptions": exceptions, "direct_receipt_models": package,
               "direct_receipt_models.receipt_logic": logic}
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("direct_receipt_models.receipt_bill", ROOT / "models" / "receipt_bill.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


ORM = orm_module()


def latam_module():
    odoo = types.ModuleType("odoo")
    odoo._ = lambda value: value
    odoo.api = types.SimpleNamespace(model=lambda func: func, depends=lambda *args: lambda func: func)
    odoo.models = types.SimpleNamespace(Model=ORM.AccountMove)
    with patch.dict(sys.modules, {"odoo": odoo}):
        spec = importlib.util.spec_from_file_location(
            "direct_receipt_latam", ROOT.parent / "receipt_bill_latam" / "models" / "receipt_bill.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


LATAM = latam_module()


class LatamTests(unittest.TestCase):
    def document(self):
        model = LATAM.AccountMove()
        model.l10n_latam_use_documents = True
        model.l10n_latam_document_type_id = types.SimpleNamespace(id=11)
        model.l10n_latam_document_number = "0001-00001234"
        return model

    def test_fiscal_identity_starts_empty(self):
        model = self.document()
        self.assertEqual(model._receipt_bill_direct_extra_values(), {
            "l10n_latam_document_type_id": False, "l10n_latam_document_number": False})

    def test_document_fields_invalidate_review(self):
        model = self.document()
        self.assertTrue({"l10n_latam_document_type_id", "l10n_latam_document_number"}
                        <= model._receipt_bill_direct_sensitive_fields())

    def test_base_missing_evidence_preserved(self):
        model = self.document()
        with patch.object(ORM.AccountMove, "_receipt_bill_direct_review_missing", return_value="Falta evidencia"):
            self.assertEqual(model._receipt_bill_direct_review_missing(), "Falta evidencia")

    def test_document_type_and_number_required_when_used(self):
        for missing in ["type", "number"]:
            model = self.document()
            if missing == "type":
                model.l10n_latam_document_type_id = False
            else:
                model.l10n_latam_document_number = False
            with patch.object(ORM.AccountMove, "_receipt_bill_direct_review_missing", return_value=""):
                self.assertIn("LATAM", model._receipt_bill_direct_review_missing())

    def test_document_number_protected_by_snapshot(self):
        model = self.document()
        with patch.object(ORM.AccountMove, "_receipt_bill_direct_review_snapshot", return_value={}):
            self.assertEqual(model._receipt_bill_direct_review_snapshot()["latam_document_number"], "0001-00001234")

    def test_nonfiscal_sequence_does_not_change_review_fingerprint(self):
        model = self.document()
        model.l10n_latam_use_documents = False
        with patch.object(ORM.AccountMove, "_receipt_bill_direct_review_snapshot", return_value={}):
            before = model._receipt_bill_direct_review_snapshot()
            model.l10n_latam_document_number = "SECUENCIA/2026/00001"
            after = model._receipt_bill_direct_review_snapshot()
        self.assertEqual(before, after)


class EarlyGuardTests(unittest.TestCase):
    def configured(self):
        user = types.SimpleNamespace(has_group=lambda group: True)
        journal = types.SimpleNamespace(id=11, type="purchase")
        company = types.SimpleNamespace(receipt_bill_enabled=True, receipt_bill_user_ids=[user],
                                        receipt_bill_journal_id=journal)
        company.sudo = lambda: company
        journal.company_id = company
        params = types.SimpleNamespace(get_param=lambda key: "casa_prueba")
        params.sudo = lambda: params
        class Env:
            def __getitem__(self, key):
                if key != "ir.config_parameter":
                    raise AssertionError(key)
                return params
        model = ORM.AccountMove()
        model._fields = {}
        model.env = Env()
        model.env.companies = [company]
        model.env.cr = types.SimpleNamespace(dbname="casa_prueba")
        model.env.user = user
        model.has_access = lambda action: True
        return model, company, user, journal, params

    def test_contract_distinguishes_ordinary_invoices(self):
        direct, ordinary = types.SimpleNamespace(receipt_bill_lot="L000123"), types.SimpleNamespace(receipt_bill_lot=False)
        ORM.AccountMove._compute_receipt_bill_contract([direct, ordinary])
        self.assertEqual((direct.receipt_bill_contract, ordinary.receipt_bill_contract), (2, 0))

    def test_nominal_enabled_company_returns_configured_journal(self):
        model, company, user, journal, params = self.configured()
        self.assertIs(model._receipt_bill_direct_allowed(company), journal)

    def test_restored_database_is_not_enabled(self):
        model, company, user, journal, params = self.configured()
        model.env.cr.dbname = "casa_copia"
        with self.assertRaises(AccessError):
            model._receipt_bill_direct_allowed(company)

    def test_company_toggle_required(self):
        model, company, user, journal, params = self.configured()
        company.receipt_bill_enabled = False
        with self.assertRaises(AccessError):
            model._receipt_bill_direct_allowed(company)

    def test_selected_nominal_user_required(self):
        model, company, user, journal, params = self.configured()
        company.receipt_bill_user_ids = []
        with self.assertRaises(AccessError):
            model._receipt_bill_direct_allowed(company)

    def test_company_must_be_allowed_in_session(self):
        model, company, user, journal, params = self.configured()
        model.env.companies = []
        with self.assertRaises(AccessError):
            model._receipt_bill_direct_allowed(company)

    def test_hr_only_user_cannot_create_invoice(self):
        model, company, user, journal, params = self.configured()
        user.has_group = lambda group: False
        with self.assertRaises(AccessError):
            model._receipt_bill_direct_allowed(company)

    def test_real_create_and_read_acl_required(self):
        for denied in ["create", "read"]:
            model, company, user, journal, params = self.configured()
            model.has_access = lambda action: action != denied
            with self.assertRaises(AccessError):
                model._receipt_bill_direct_allowed(company)

    def test_journal_must_be_purchase_and_same_company(self):
        for alteration in ["type", "company"]:
            model, company, user, journal, params = self.configured()
            if alteration == "type":
                journal.type = "sale"
            else:
                journal.company_id = None
            with self.assertRaises(ValueError):
                model._receipt_bill_direct_allowed(company)

    def test_post_without_review_is_blocked_before_parent(self):
        class Bills(list):
            def _receipt_bill_direct_lock(self):
                pass
        model = ORM.AccountMove()
        model.filtered = lambda name: Bills([types.SimpleNamespace(receipt_bill_direct_review_by_id=False)])
        with self.assertRaisesRegex(ValueError, "revisión fiscal"):
            model._post()

    def test_post_with_stale_fingerprint_is_blocked(self):
        class Bills(list):
            def _receipt_bill_direct_lock(self):
                pass
        bill = types.SimpleNamespace(receipt_bill_direct_review_by_id=17,
                                     receipt_bill_direct_review_hash="old",
                                     _receipt_bill_direct_review_missing=lambda: "",
                                     _receipt_bill_direct_review_fingerprint=lambda: "changed")
        model = ORM.AccountMove()
        model.filtered = lambda name: Bills([bill])
        with self.assertRaisesRegex(ValueError, "revisión fiscal"):
            model._post()

    def test_create_rejects_context_origin_default(self):
        model = ORM.AccountMove()
        for name in logic.ORIGIN_FIELDS | logic.REVIEW_FIELDS:
            model.env = types.SimpleNamespace(context={"default_" + name: False}, uid=17)
            with self.assertRaises(AccessError):
                model.create([values()])

    def test_create_rejects_review_metadata_even_for_nonreceipt(self):
        model = ORM.AccountMove()
        model.env = types.SimpleNamespace(context={}, uid=17)
        with self.assertRaises(AccessError):
            model.create([{"receipt_bill_direct_review_hash": False}])

    def test_public_write_rejects_all_origin_and_review_fields(self):
        model = ORM.AccountMove()
        for name in logic.ORIGIN_FIELDS | logic.REVIEW_FIELDS:
            with self.assertRaises(AccessError):
                model.write({name: False})

    def test_review_requires_accounting_role(self):
        model = ORM.AccountMove()
        model.env = types.SimpleNamespace(user=types.SimpleNamespace(has_group=lambda group: False))
        with self.assertRaises(AccessError):
            model.action_receipt_bill_direct_review()

    def test_legacy_creator_fails_closed(self):
        model = ORM.AccountMove()
        model._fields = {"receipt_bill_expense_id": None}
        with self.assertRaises(AccessError):
            model._receipt_bill_direct_allowed(None)

    def test_original_document_cannot_be_deleted(self):
        model = ORM.AccountMove()
        model.filtered = lambda name: [True]
        with self.assertRaises(AccessError):
            model.unlink()

    def test_attachment_bytes_and_origin_cannot_be_changed(self):
        model = ORM.IrAttachment()
        model._receipt_bill_direct_moves = lambda: [True]
        model._receipt_bill_direct_target = lambda values: []
        for name in ["datas", "raw", "store_fname", "checksum", "res_model", "res_id", "public", "access_token", "type"]:
            with self.assertRaises(AccessError):
                model.write({name: False})

    def test_existing_attachment_cannot_be_moved_into_origin(self):
        model = ORM.IrAttachment()
        model._receipt_bill_direct_moves = lambda: []
        model._receipt_bill_direct_target = lambda values: [True]
        with self.assertRaises(AccessError):
            model.write({"res_model": "account.move", "res_id": 81})

    def test_evidence_cannot_be_deleted(self):
        model = ORM.IrAttachment()
        model._receipt_bill_direct_moves = lambda: [True]
        with self.assertRaises(AccessError):
            model.unlink()

    def test_addon_is_account_only(self):
        manifest = ast.literal_eval((ROOT / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertEqual(manifest["depends"], ["account"])
        self.assertFalse(manifest["auto_install"])

    def test_latam_has_only_new_dependencies(self):
        manifest = ast.literal_eval((ROOT.parent / "receipt_bill_latam" / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertEqual(manifest["depends"], ["receipt_bill", "l10n_latam_invoice_document"])


if __name__ == "__main__":
    unittest.main()
