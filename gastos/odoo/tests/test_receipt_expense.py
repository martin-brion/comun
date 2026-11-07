"""Optional expense-report contract and early guards; no ORM or network simulation."""
import ast
from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parent.parent / "receipt_bill_expense"
spec = importlib.util.spec_from_file_location("expense_contract_test", ADDON / "models/expense_logic.py")
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)

BILL = {
    "id": 31, "contract": 2, "state": "posted", "move_type": "in_invoice",
    "company_id": 3, "currency_id": 46, "date": date(2026, 9, 23),
    "payment": "own_account", "total": 123.45, "original_total": 123.45,
    "original_analytic": {"521": 100}, "lines": [{"521": 100}],
    "lot": "L000123", "photo": 1,
}
EXPENSE = {
    "source_id": 31, "invoice_id": 31, "company_id": 3, "currency_id": 46,
    "date": date(2026, 9, 23), "payment": "own_account", "total": 123.45,
    "analytic": {"521": 100}, "name": "Comprobante de prueba [L000123/F01]",
}


class ContractTests(unittest.TestCase):
    def test_both_payment_modes_reference_same_canonical_bill(self):
        for payment in ("own_account", "company_account"):
            expense, bill = deepcopy(EXPENSE), deepcopy(BILL)
            expense["payment"] = bill["payment"] = payment
            self.assertTrue(logic.validate(expense, bill))

    def test_company_currency_date_and_invoice_are_not_inferred(self):
        for field, value in (("company_id", 7), ("currency_id", 47), ("date", "2026-09-24"),
                             ("invoice_id", 32), ("source_id", 32), ("source_id", False)):
            with self.subTest(field=field, value=value):
                expense = deepcopy(EXPENSE)
                expense[field] = value
                with self.assertRaises(logic.ExpenseContractError):
                    logic.validate(expense, BILL)

    def test_only_posted_canonical_vendor_bill(self):
        for field, value in (("state", "draft"), ("state", "cancel"), ("contract", 1),
                             ("move_type", "in_refund"), ("move_type", "entry")):
            with self.subTest(field=field, value=value):
                bill = deepcopy(BILL)
                bill[field] = value
                with self.assertRaises(logic.ExpenseContractError):
                    logic.validate(EXPENSE, bill)

    def test_fiscal_adjustment_requires_operator_review(self):
        bill, expense = deepcopy(BILL), deepcopy(EXPENSE)
        bill["total"] = expense["total"] = 124
        with self.assertRaisesRegex(logic.ExpenseContractError, "revisión explícita"):
            logic.validate(expense, bill)

    def test_invalid_or_changed_total_fails_closed(self):
        for value in (0, -1, float("nan"), float("inf"), True, None, "bad", 124):
            with self.subTest(value=value):
                expense = deepcopy(EXPENSE)
                expense["total"] = value
                with self.assertRaises(logic.ExpenseContractError):
                    logic.validate(expense, BILL)

    def test_payment_label_cannot_change_original_payment(self):
        expense = deepcopy(EXPENSE)
        expense["payment"] = "company_account"
        with self.assertRaises(logic.ExpenseContractError):
            logic.validate(expense, BILL)

    def test_analytic_change_in_expense_or_any_fiscal_line_blocks(self):
        for field, value in (("analytic", {"522": 100}), ("analytic", {})):
            expense = deepcopy(EXPENSE)
            expense[field] = value
            with self.assertRaises(logic.ExpenseContractError):
                logic.validate(expense, BILL)
        for lines in ([], [{"522": 100}], [{"521": 100}, {"522": 100}], [{}]):
            bill = deepcopy(BILL)
            bill["lines"] = lines
            with self.assertRaises(logic.ExpenseContractError):
                logic.validate(EXPENSE, bill)

    def test_analytic_ids_and_percentages_validate(self):
        for value in ({"bad": 100}, {"0": 100}, {"521": 0}, {"521": 101}, {"521": True}, {"521": "NaN"}):
            with self.assertRaises(logic.ExpenseContractError):
                logic.analytic(value)
        self.assertEqual(logic.analytic({"521,522": "100"}), {"521,522": 100})

    def test_exact_marker_occurs_once(self):
        for name in ("Comprobante [L000123/F02]", "[L000123/F01] [L000123/F01]", "Comprobante",
                     "[L000123/F01] [L000999/F02]", False):
            expense = deepcopy(EXPENSE)
            expense["name"] = name
            with self.assertRaises(logic.ExpenseContractError):
                logic.validate(expense, BILL)


class GuardError(Exception):
    pass


class FakeModel:
    def __init__(self):
        self.env = types.SimpleNamespace(context={})
        self.receipt_bill_source_id = False
        self.calls = []

    def __iter__(self):
        return iter([self])

    def filtered(self, field):
        return self if getattr(self, field) else []

    def create(self, values):
        self.calls.append(("create", values))
        return self

    def write(self, values):
        self.calls.append(("write", values))
        return True

    def copy(self, default=None):
        self.calls.append(("copy", default))
        return "copy"

    def copy_data(self, default=None):
        self.calls.append(("copy_data", default))
        return "copy_data"

    def unlink(self):
        self.calls.append(("unlink",))
        return True

    def action_expense_create_invoice(self):
        self.calls.append(("invoice",))
        return True

    def _do_create_moves(self):
        self.calls.append(("moves",))
        return True

    def _prepare_bills_vals(self):
        self.calls.append(("bills",))
        return True

    def action_sheet_move_post(self):
        self.calls.append(("post",))
        return True

    def _do_refuse(self, reason):
        self.calls.append(("refuse", reason))
        return True

    def _do_reverse_moves(self):
        self.calls.append(("reverse",))
        return True


def load_guards():
    fields = types.SimpleNamespace(Many2one=lambda *a, **k: None, Integer=lambda *a, **k: None)
    api = types.SimpleNamespace(model_create_multi=lambda f: f, depends=lambda *a: lambda f: f,
                                constrains=lambda *a: lambda f: f)
    odoo = types.SimpleNamespace(_=lambda text: text, fields=fields, api=api,
                                 models=types.SimpleNamespace(Model=FakeModel))
    package = types.ModuleType("expense_guard_test")
    package.__path__ = [str(ADDON / "models")]
    spec = importlib.util.spec_from_file_location("expense_guard_test.expense", ADDON / "models/expense.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"odoo": odoo, "expense_guard_test": package,
                                 "odoo.exceptions": types.SimpleNamespace(AccessError=GuardError,
                                      UserError=GuardError, ValidationError=GuardError)}):
        spec.loader.exec_module(module)
    return module


guards = load_guards()


class ModelGuardTests(unittest.TestCase):
    def nominal_model(self):
        model = guards.HrExpense()
        user = types.SimpleNamespace(id=17)
        company = types.SimpleNamespace(id=3, receipt_bill_enabled=True, receipt_bill_user_ids=[user])
        company.sudo = lambda: company
        employee = types.SimpleNamespace(id=81, user_id=user, company_id=company)
        employee.exists = lambda: employee
        employee.check_access = lambda operation: None
        bill = types.SimpleNamespace(id=31, receipt_bill_contract=2, company_id=company,
                                     receipt_bill_source_uid=user)
        bill.exists = lambda: bill
        bill.check_access = lambda operation: None
        class Env(dict):
            pass
        env = Env({"account.move": types.SimpleNamespace(browse=lambda identity: bill),
                   "hr.employee": types.SimpleNamespace(browse=lambda identity: employee)})
        parameter = types.SimpleNamespace(get_param=lambda name: "casa_prueba")
        parameter.sudo = lambda: parameter
        env["ir.config_parameter"] = parameter
        env.context, env.companies, env.user, env.uid = {}, [company], user, user.id
        env.cr = types.SimpleNamespace(dbname="casa_prueba")
        model.env = env
        model._receipt_bill_validate = lambda: True
        return model, bill, employee, company

    def test_creation_uses_only_nominal_source_and_company_employee(self):
        model, bill, employee, company = self.nominal_model()
        values = {"receipt_bill_source_id": 31, "invoice_id": 31,
                  "company_id": 3, "employee_id": 81}
        self.assertIs(model.create([values]), model)
        self.assertEqual(model.calls, [("create", [values])])

    def test_creation_rejects_foreign_employee_uid_database_or_company_enablement(self):
        changes = (
            lambda m, b, e, c: setattr(e, "user_id", types.SimpleNamespace(id=18)),
            lambda m, b, e, c: setattr(e, "company_id", types.SimpleNamespace(id=7)),
            lambda m, b, e, c: setattr(b, "receipt_bill_source_uid", types.SimpleNamespace(id=18)),
            lambda m, b, e, c: setattr(c, "receipt_bill_enabled", False),
            lambda m, b, e, c: setattr(c, "receipt_bill_user_ids", []),
            lambda m, b, e, c: setattr(m.env, "companies", []),
            lambda m, b, e, c: setattr(m.env.cr, "dbname", "otra_base"),
        )
        for change in changes:
            model, bill, employee, company = self.nominal_model()
            change(model, bill, employee, company)
            with self.assertRaises(GuardError):
                model.create([{"receipt_bill_source_id": 31, "invoice_id": 31,
                               "company_id": 3, "employee_id": 81}])
            self.assertEqual(model.calls, [])

    def test_accounting_read_denial_is_not_elevated_or_masked(self):
        model, bill, employee, company = self.nominal_model()
        def denied(operation):
            raise GuardError("nominal read denied")
        bill.check_access = denied
        with self.assertRaisesRegex(GuardError, "nominal read denied"):
            model.create([{"receipt_bill_source_id": 31, "invoice_id": 31,
                           "company_id": 3, "employee_id": 81}])
        self.assertEqual(model.calls, [])

    def test_source_and_contract_cannot_be_injected_by_defaults(self):
        for field in ("receipt_bill_source_id", "receipt_bill_contract", "invoice_id"):
            model = guards.HrExpense()
            model.env.context = {"default_" + field: 31}
            with self.assertRaises(GuardError):
                model.create([{}])
            self.assertEqual(model.calls, [])

    def test_contract_cannot_be_forged(self):
        model = guards.HrExpense()
        with self.assertRaises(GuardError):
            model.create([{"receipt_bill_contract": 2}])
        self.assertEqual(model.calls, [])

    def test_source_and_invoice_must_be_explicit_identical_positive_ids(self):
        for source, invoice in ((31, False), (31, 32), (True, True), (-1, -1)):
            model = guards.HrExpense()
            with self.assertRaises(GuardError):
                model.create([{"receipt_bill_source_id": source, "invoice_id": invoice}])
            self.assertEqual(model.calls, [])

    def test_no_source_write_even_same_value_or_unlink(self):
        for source in (False, 31):
            for field in ("receipt_bill_source_id", "receipt_bill_contract"):
                model = guards.HrExpense()
                model.receipt_bill_source_id = source
                with self.assertRaises(GuardError):
                    model.write({field: source})
                self.assertEqual(model.calls, [])

    def test_source_financial_fields_are_immutable(self):
        for field in guards.IMMUTABLE:
            model = guards.HrExpense()
            model.receipt_bill_source_id = 31
            with self.assertRaises(GuardError):
                model.write({field: False})
            self.assertEqual(model.calls, [])

    def test_copy_copy_data_delete_and_second_invoice_are_blocked(self):
        for method in ("copy", "copy_data", "unlink", "action_expense_create_invoice"):
            model = guards.HrExpense()
            model.receipt_bill_source_id = 31
            with self.assertRaises(GuardError):
                getattr(model, method)()
            self.assertEqual(model.calls, [])

    def test_ordinary_expense_copy_and_delete_remain_available(self):
        for method in ("copy", "copy_data", "unlink", "action_expense_create_invoice"):
            model = guards.HrExpense()
            self.assertTrue(getattr(model, method)())
            self.assertEqual(len(model.calls), 1)

    def test_guard_runs_before_all_sheet_accounting_entry_points(self):
        for method in ("_do_create_moves", "_prepare_bills_vals", "action_sheet_move_post"):
            model = guards.HrExpenseSheet()
            def fail():
                raise GuardError("changed source")
            model._receipt_bill_validate_sources = fail
            with self.assertRaises(GuardError):
                getattr(model, method)()
            self.assertEqual(model.calls, [])

    def test_validated_sheet_delegates_to_oca_without_automatic_action(self):
        for method in ("_do_create_moves", "_prepare_bills_vals", "action_sheet_move_post"):
            model = guards.HrExpenseSheet()
            model._receipt_bill_validate_sources = lambda: True
            self.assertTrue(getattr(model, method)())
            self.assertEqual(len(model.calls), 1)

    def test_refusal_does_not_call_bill_validation_or_invoice_methods(self):
        model = guards.HrExpenseSheet()
        model._receipt_bill_guard_canonical_moves = lambda: True
        model._receipt_bill_validate_sources = lambda: self.fail("HR refusal must not read fiscal contents")
        self.assertTrue(model._do_refuse("Revisión de conducta"))
        self.assertEqual(model.calls, [("refuse", "Revisión de conducta")])

    def test_canonical_invoice_cannot_be_a_report_generated_entry(self):
        model = guards.HrExpenseSheet()
        model.expense_line_ids = types.SimpleNamespace(mapped=lambda field: types.SimpleNamespace(ids=[31]))
        model.account_move_ids = types.SimpleNamespace(ids=[31])
        for method, args in (("_do_refuse", ("Motivo",)), ("_do_reverse_moves", ())):
            with self.assertRaises(GuardError):
                getattr(model, method)(*args)
        self.assertEqual(model.calls, [])

    def test_no_accounting_sudo_or_approval_automation_in_adapter(self):
        tree = ast.parse((ADDON / "models/expense.py").read_text(encoding="utf-8"))
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertNotIn("action_post", calls)
        self.assertNotIn("_post", calls)
        self.assertNotIn("_do_approve", calls)
        self.assertNotIn("action_register_payment", calls)
        self.assertEqual(calls.count("sudo"), 2)  # company configuration and exact database opt-in only
        manifest = ast.literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertEqual(manifest["depends"], ["receipt_bill", "hr_expense_invoice"])
        self.assertFalse(manifest["auto_install"])


if __name__ == "__main__":
    unittest.main()
